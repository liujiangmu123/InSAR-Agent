"""HTTP 后端访问层(MCP 工具的共用基座,不含任何 MCP SDK 依赖)。

职责:
  - httpx 异步客户端的惰性构造(基址/超时可配,transport 可注入 —— 测试用
    ASGITransport 直连 create_app,免起进程);
  - 错误归一化:连接失败 → 带启动指引的 BackendError;HTTP 4xx/5xx → 透传
    后端 detail(闭集校验错误原样给宿主 LLM);超时 → 指出可调的环境变量;
  - NDJSON 回合流消费:带总 deadline 与提前停止条件,达到即断开 ——
    后端契约(api/app.py ndjson)保证断开不取消回合,run 继续推进到终态;
  - run_id → session 的解析:后端 API 全部按会话取数,MCP 工具却以 run_id
    为主键。本进程发起的 run 记在内存映射;映射外的 run 退化为全会话扫描
    (本地单用户形态,会话数有限,可接受)。
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Callable

import httpx

DEFAULT_API_BASE = "http://127.0.0.1:8873"

#: 启动指引(后端不可达时给宿主的行动建议,拼进 BackendError)
_STARTUP_HINT = (
    "请先启动 InSAR 后端:`python -m insar_agent.api.app`(默认监听 "
    f"{DEFAULT_API_BASE}),或设置环境变量 INSAR_API_BASE 指向已运行的服务。"
)


class BackendError(RuntimeError):
    """后端访问失败的归一化异常;message 面向宿主 LLM,须自带下一步指引。

    status:触发异常的 HTTP 状态码(非 HTTP 层失败为 None),供调用方按状态
    细化指引 —— 如旧后端缺自主循环端点时把 404/405 换成升级提示。
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# ---------------- 配置(环境变量,调用时读取) ----------------

def api_base() -> str:
    base = _config.get("base_url") or os.environ.get("INSAR_API_BASE", DEFAULT_API_BASE)
    return base.rstrip("/")


def http_timeout() -> float:
    """单次 HTTP 请求超时(秒);INSAR_MCP_HTTP_TIMEOUT 可调。"""
    return float(os.environ.get("INSAR_MCP_HTTP_TIMEOUT", "30"))


def plan_deadline() -> float:
    """规划回合流的消费上限(秒):规划含环境探测但无重计算,通常秒级;
    超限则断开并让宿主转 run_status 轮询。INSAR_MCP_PLAN_TIMEOUT 可调。"""
    return float(os.environ.get("INSAR_MCP_PLAN_TIMEOUT", "120"))


def accept_window() -> float:
    """执行回合的受理观察窗(秒):窗口内收集早期事件判断受理与否,
    到点即断开(run 在后端继续)。INSAR_MCP_ACCEPT_WINDOW 可调。"""
    return float(os.environ.get("INSAR_MCP_ACCEPT_WINDOW", "3"))


def converse_deadline() -> float:
    """自主循环回合流的消费上限(秒):回合含多周期 LLM 决策与只读工具,
    比单步规划长;超限断开(后端契约:断开不取消回合)。
    INSAR_MCP_CONVERSE_TIMEOUT 可调。"""
    return float(os.environ.get("INSAR_MCP_CONVERSE_TIMEOUT", "180"))


# ---------------- 客户端生命周期 ----------------

_config: dict[str, Any] = {}          # configure() 的注入点(base_url / transport)
_client: httpx.AsyncClient | None = None
_run_sessions: dict[str, str] = {}    # 本进程见过的 run_id → session_id


def configure(*, base_url: str | None = None,
              transport: httpx.AsyncBaseTransport | None = None) -> None:
    """注入基址/transport(测试用 ASGITransport 直连后端 app)。

    只丢弃旧客户端引用、不 aclose:跨事件循环关闭会炸,而被弃客户端要么
    无真实连接(ASGITransport),要么随进程退出回收 —— 测试隔离优先。
    """
    global _client
    _config["base_url"] = base_url
    _config["transport"] = transport
    _client = None
    _run_sessions.clear()


def reset() -> None:
    """复位为纯环境变量配置(测试收尾用)。"""
    configure(base_url=None, transport=None)


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=api_base(), timeout=http_timeout(),
            transport=_config.get("transport"))
    return _client


# ---------------- 错误归一化 ----------------

def _detail_of(resp: httpx.Response) -> str:
    """提取错误响应的 detail(FastAPI 惯例),兜底截断原文。"""
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:500]
    if isinstance(data, dict) and "detail" in data:
        d = data["detail"]
        return d if isinstance(d, str) else json.dumps(d, ensure_ascii=False)[:800]
    return json.dumps(data, ensure_ascii=False)[:800]


def _raise_normalized(exc: httpx.HTTPError) -> None:
    """httpx 异常 → BackendError(带行动指引);永远 raise,无返回。"""
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        raise BackendError(
            f"无法连接 InSAR 后端 {api_base()}({exc.__class__.__name__})。"
            + _STARTUP_HINT) from exc
    if isinstance(exc, httpx.TimeoutException):
        raise BackendError(
            f"请求 InSAR 后端超时(>{http_timeout():.0f}s)。后端可能正忙;"
            "可用环境变量 INSAR_MCP_HTTP_TIMEOUT 调大超时。") from exc
    raise BackendError(f"请求 InSAR 后端失败:{exc}") from exc


async def request(method: str, path: str, **kwargs: Any) -> Any:
    """一次 JSON 请求。4xx/5xx 透传后端 detail;连接/超时给行动指引。"""
    try:
        resp = await client().request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        _raise_normalized(exc)
    if resp.status_code >= 400:
        raise BackendError(f"后端拒绝(HTTP {resp.status_code}):{_detail_of(resp)}",
                           status=resp.status_code)
    return resp.json()


# ---------------- NDJSON 回合流 ----------------

async def consume_ndjson(path: str, body: dict, *, deadline_s: float,
                         stop: Callable[[dict], bool] | None = None,
                         ) -> tuple[list[dict], bool]:
    """POST 一个回合并消费其 NDJSON 事件流。

    返回 (events, finished):finished=True 表示流自然走完;False 表示命中
    stop 条件或 deadline 后主动断开 —— 后端契约保证断开不取消回合
    (api/app.py ndjson:「run 归服务端所有,继续推进到终态」)。
    """
    events: list[dict] = []
    finished = False
    # read 超时按本次消费上限放宽(per-request 覆盖,共享 client 的默认超时不动):
    # 自主循环周期间的 LLM 决策静默期可达数分钟,共享 client 的 30s read timeout
    # 会在相邻事件的间隙误杀长回合(P1-3)—— deadline 才是消费的唯一上限,
    # 由外层 asyncio.timeout 承载;connect/write/pool 保持 http_timeout()。
    timeout = httpx.Timeout(http_timeout(), read=max(deadline_s, http_timeout()))
    try:
        async with asyncio.timeout(deadline_s):
            async with client().stream("POST", path, json=body,
                                       timeout=timeout) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    raise BackendError(
                        f"后端拒绝(HTTP {resp.status_code}):{_detail_of(resp)}",
                        status=resp.status_code)
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue  # 坏行不炸整个回合(流协议里错误本就是事件)
                    events.append(event)
                    if stop is not None and stop(event):
                        return events, False
                finished = True
    except TimeoutError:
        pass  # deadline 到:携带已收事件返回,由调用方给 events_hint
    except httpx.HTTPError as exc:
        _raise_normalized(exc)
    return events, finished


# ---------------- 自主循环回合(契约 §10) ----------------

#: 旧后端(< 2026-08-14)没有自主循环端点时的升级指引
_CONVERSE_UPGRADE_HINT = (
    "InSAR 后端版本过旧:自主循环端点 POST /api/converse 缺失,"
    "需要 2026-08-14 及以上版本。请更新代码后重启后端"
    "(python -m insar_agent.api.app);升级前可改用 insar_plan_run + "
    "insar_execute_run 的分步流程。"
)


async def converse(session_id: str, text: str, max_cycles: int) -> tuple[list[dict], bool]:
    """POST /api/converse 发起自主循环回合,消费 NDJSON 事件流到回合结束。

    回合以 say(正常收束)或 note(预算耗尽/取消/降级收尾)终止,终止后流
    自然关闭(与 plan_run 消费 /api/turn 同款语义:不设提前停止条件,中途的
    告警 note 不会被误当收尾)。返回 (events, finished):finished=False 表示
    deadline(INSAR_MCP_CONVERSE_TIMEOUT)打断 —— 回合在后端继续推进,
    断开不取消。旧后端没有该端点:404/405 归一化为带升级指引的 BackendError。
    """
    try:
        return await consume_ndjson(
            "/api/converse",
            {"session": session_id, "text": text, "max_cycles": max_cycles},
            deadline_s=converse_deadline())
    except BackendError as exc:
        if exc.status in (404, 405):
            raise BackendError(_CONVERSE_UPGRADE_HINT, status=exc.status) from exc
        raise


# ---------------- run → session 解析 ----------------

def remember_run(run_id: str, session_id: str) -> None:
    _run_sessions[run_id] = session_id


async def session_of_run(run_id: str, session_id: str | None = None) -> str:
    """解析 run 的归属会话:显式参数 > 本进程映射 > 全会话扫描。"""
    if session_id:
        remember_run(run_id, session_id)
        return session_id
    if run_id in _run_sessions:
        return _run_sessions[run_id]
    sessions = await request("GET", "/api/sessions",
                             params={"include_archived": True})
    for row in sessions:
        sid = row["session_id"]
        data = await request("GET", "/api/runs", params={"session": sid})
        if any(r["run_id"] == run_id for r in data.get("runs", [])):
            remember_run(run_id, sid)
            return sid
    raise BackendError(
        f"后端中找不到 run {run_id!r}。请确认 run_id 来自 insar_plan_run /"
        " insar_execute_run 的返回,或先用 insar_plan_run 生成计划;"
        "也可以传 session_id 参数直接指定归属会话。")
