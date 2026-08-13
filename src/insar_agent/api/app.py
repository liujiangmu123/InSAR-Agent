"""FastAPI 层:人与 LLM 共用同一套接口(AGENT-DESIGN §2)。

协议纪律(吸收 pi RPC,absorb-E4/E8):
  - 回合是流式 NDJSON:POST 响应只代表「已接受」,过程与结果走事件流,
    绝不给同一请求发第二个终态响应。
  - 运行中投递消息必须显式声明 deliver_as=steer|follow_up|next_run,否则 400。
  - 全局事件走 SSE(/api/events);单回合事件走该回合自己的 NDJSON 流。

启动:python -m insar_agent.api.app(默认 127.0.0.1:8873,INSAR_HOME 可配)。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.datastructures import MutableHeaders

from insar_agent.api.admin_router import create_admin_router
from insar_agent.api.advisor_router import create_advisor_router
from insar_agent.api.artifacts_router import create_artifacts_router
from insar_agent.api.diag_router import create_diag_router
from insar_agent.api.doctor_router import create_doctor_router
from insar_agent.api.queue_router import create_queue_router
from insar_agent.api.setup_router import create_setup_router
from insar_agent.api.skills_router import router as skills_router
from insar_agent.api.version_router import router as version_router
from insar_agent.audit.contract import load_contract
from insar_agent.brain.facade import Brain
from insar_agent.brain.provider import LLMProvider
from insar_agent.core.actions import ACTIONS
from insar_agent.core.db import Database
from insar_agent.core.ledger import export_provenance
from insar_agent.core.stale import preview_change
from insar_agent.core.store import DELIVER_AS, Store
from insar_agent.loop.driver import Driver
from insar_agent.loop.queue import QueueScheduler, RunQueue
from insar_agent.planner.feasibility import narrow_methods
from insar_agent.planner.plan import fork_run
from insar_agent.registry.capabilities import PIPELINE, REGISTRY
from insar_agent.report.bundle import build_repro_bundle
from insar_agent.report.script import export_run_script

# INSAR_UI_DIR:静态 UI 目录的唯一环境变量覆盖点(桌面冻结版由
# desktop/backend-bundle/entry.py 在 import 本模块前设置;源码运行不受影响,
# 缺省仍按 __file__ 回溯源码树 src/../prototype)。
_UI_DIR_OVERRIDE = os.environ.get("INSAR_UI_DIR", "")
PROTOTYPE_DIR = (Path(_UI_DIR_OVERRIDE) if _UI_DIR_OVERRIDE
                 else Path(__file__).resolve().parents[3] / "prototype")

log = logging.getLogger(__name__)

# session_id 会成为 workspace 子目录名(home/sessions/<id>):必须在 API 边界
# 挡掉路径穿越与文件系统非法名,否则 GET /api/registry?session=../../x 之类的
# 请求会在 home 之外创建目录(driver 构造时 mkdir)。
_SESSION_ID_MAX = 64
_SESSION_FORBIDDEN_CHARS = set('/\\:*?"<>|')
_WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL",
                           *(f"COM{i}" for i in range(1, 10)),
                           *(f"LPT{i}" for i in range(1, 10))}

#: /api/actions 可入队的动作闭集(core/actions.ACTIONS + 掉线消息 USER_MESSAGE,
#: 见 schema.sql pending_actions.action 注释);其中步骤级动作需要整数 target。
_KNOWN_ACTIONS = frozenset(ACTIONS) | {"USER_MESSAGE"}
_STEP_ACTIONS = frozenset({"RESET", "SKIP", "SET_METHOD", "SET_PARAMS"})

#: SQLite INTEGER 上限之内的宽松步骤号边界(流水线实际只有 1-11)
_STEP_ID_MAX = 1_000_000

#: 影像面板可直读的图像扩展名闭集(→ Content-Type)。svg 显式排除:
#: 它可携带脚本,作为同源文档打开会成为 XSS 面;位图格式无此风险。
_IMAGE_MEDIA_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}

#: 三档尺寸契约(HyP3 式,engines/figures.py 的产物目录约定):
#: <name>_browse.* / <name>_thumb.* 是 <name>.* 的浏览/缩略档,
#: 列表时归并进基图条目(url/thumbUrl),不单独成条目。
_TIER_SUFFIXES = ("_browse", "_thumb")

#: 元数据 sidecar(<name>.json)的读取上限:防坏文件/误命名的大 JSON 拖垮列表
_SIDECAR_MAX_BYTES = 64 * 1024

# ---------------- 安全响应头与 CSP(第二轮加固,AUDIT-security-r2-2026-08-13) ----------------

#: 静态 UI 的 CSP 公共骨架:资源一律同源(img/font 等未列指令回落
#: default-src 'self':UI 的 API_BASE=''、CSS 零外链、无 data:/blob: 引用,
#: 均经 grep 实证),并关闭 object / base 篡改 / 被嵌入 / 表单外发四个面。
#: connect-src 额外放行 Tauri IPC 通道(ipc: 与 http://ipc.localhost):桌面壳
#: 的 invoke 首选 fetch 型 IPC,缺此项会命中 CSP 拦截→回退 postMessage(功能
#: 不破但每页首个 invoke 多一次失败往返 + 控制台告警,desktop/ALIGNMENT-2026-08-13
#: 实证);浏览器侧对未知 scheme 直接忽略,无副作用。
_CSP_BASE = ("default-src 'self'; "
             "connect-src 'self' ipc: http://ipc.localhost; "
             "object-src 'none'; base-uri 'none'; "
             "form-action 'self'; frame-ancestors 'none'")

#: 未在启动扫描表内的 HTML(理论上不存在:表按落盘文件生成)给最严格兜底
_CSP_STRICT_FALLBACK = _CSP_BASE + "; script-src 'self'; style-src 'self'"

#: 内联 <script> 块(无 src 属性):其正文可用 sha256 哈希白名单放行
_INLINE_SCRIPT_RE = re.compile(rb"<script\b(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>",
                               re.IGNORECASE | re.DOTALL)
#: 内联事件处理器(onclick= 等)属性形态的脚本:CSP 哈希覆盖不了属性,
#: 含此形态的页面(仅 v2-backup 历史快照)整页降级 'unsafe-inline'
_EVENT_HANDLER_RE = re.compile(rb"<[^>]*\son[a-z]+\s*=", re.IGNORECASE)
#: <style> 块或 style= 属性:出现则该页 style-src 放行 'unsafe-inline'
#: (CSS 注入面远小于脚本;核心页 index.html 两者皆无,保持全严格)
_INLINE_STYLE_RE = re.compile(rb"<style\b|\sstyle\s*=", re.IGNORECASE)


def _page_csp(raw: bytes) -> str:
    """按单个 HTML 页面的实际形态生成最小 CSP(启动时算一次)。

    哈希口径:HTML 解析器把输入流的 CRLF 归一为 LF 后才取脚本正文,
    浏览器按归一后的文本算 sha256 —— 这里必须保持同一口径,否则
    Windows 检出(CRLF)的页面哈希对不上,内联脚本会被静默拦截。
    """
    body = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if _EVENT_HANDLER_RE.search(body):
        # CSP 语义:script-src 同时给哈希与 'unsafe-inline' 时后者被忽略,
        # 两者不能混用 —— 含事件处理器属性的页面只能整页放行内联
        script_src = "'self' 'unsafe-inline'"
    else:
        hashes = [
            "'sha256-" + base64.b64encode(hashlib.sha256(m.group(1)).digest()).decode() + "'"
            for m in _INLINE_SCRIPT_RE.finditer(body)
        ]
        script_src = " ".join(["'self'", *hashes])
    style_src = "'self' 'unsafe-inline'" if _INLINE_STYLE_RE.search(body) else "'self'"
    return f"{_CSP_BASE}; script-src {script_src}; style-src {style_src}"


def scan_ui_csp(ui_dir: Path) -> dict[str, str]:
    """静态 UI 目录 → {URL 路径: 按页 CSP}(create_app 启动时扫一次)。

    index.html 同时注册目录索引路径(/ 与 /sub/,对齐 StaticFiles html=True
    的服务语义);UI 目录不存在(无 UI 部署形态)返回空表。
    运行中改动 HTML 内联脚本需重启服务才会重算哈希(生产形态 UI 只读,
    源码调试改完内联块后重启即可,静态外部 js/css 不受影响)。
    """
    table: dict[str, str] = {}
    if not ui_dir.is_dir():
        return table
    for f in sorted(ui_dir.rglob("*.html")):
        try:
            policy = _page_csp(f.read_bytes())
        except OSError:
            continue  # 单页不可读不拖垮启动:该页命中严格兜底
        rel = f.relative_to(ui_dir).as_posix()
        table["/" + rel] = policy
        if f.name == "index.html":
            table["/" + rel[: -len("index.html")]] = policy
    return table


class SecurityHeadersMiddleware:
    """安全响应头中间件(纯 ASGI 形态:不用 BaseHTTPMiddleware,后者会把
    响应重包一层 —— 本服务的 NDJSON 回合流 / SSE 事件流不必冒这个险)。

    - 全部响应:X-Content-Type-Options / Referrer-Policy / X-Frame-Options。
      X-Frame-Options 取 DENY:UI 无 iframe,桌面壳(Tauri)以
      WebviewUrl::External 做「顶层导航」加载 http://127.0.0.1:<port>/,
      不受该头约束(它只管被嵌入),故无需 SAMEORIGIN(desktop/src/main.rs 实证);
    - /api/*:Cache-Control: no-store —— 状态/日志/图件都是随 run 演化的
      易变数据,浏览器缓存会展示过期状态;回环链路重取成本可忽略;
    - 静态 UI 的 HTML 文档:按页 CSP(启动扫描表,scan_ui_csp)。
    端点已自设的同名头一律不覆写。
    """

    def __init__(self, app, csp_by_path: dict[str, str] | None = None) -> None:
        self.app = app
        self.csp_by_path = csp_by_path or {}

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message.setdefault("headers", []))
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "no-referrer")
                headers.setdefault("X-Frame-Options", "DENY")
                if path == "/api" or path.startswith("/api/"):
                    headers.setdefault("Cache-Control", "no-store")
                elif headers.get("content-type", "").startswith("text/html"):
                    headers.setdefault("Content-Security-Policy",
                                       self.csp_by_path.get(path, _CSP_STRICT_FALLBACK))
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _host_is_loopback(host: str) -> bool:
    """host 是否回环:localhost 或 127.0.0.0/8、::1 等回环 IP。"""
    if host.strip().lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip()).is_loopback
    except ValueError:
        return False  # 主机名/空串等按非回环对待:宁可多告警


def resolve_bind_host(default: str = "127.0.0.1") -> str:
    """解析监听地址(INSAR_HOST)并对非回环绑定显式告警(放行不拦截)。

    本应用是本地单用户形态:全部端点无鉴权,信任边界就是回环接口。
    绑定 0.0.0.0 等于把无鉴权的 /api/admin/*(外部终结)、产物文件读取、
    会话操作面整个暴露给所在网络 —— 不硬禁止(内网联调是正当用法),
    但必须留下告警痕迹(边界声明见 README「安全边界」节)。
    """
    host = os.environ.get("INSAR_HOST", default)
    if not _host_is_loopback(host):
        log.warning(
            "INSAR_HOST=%s 不是回环地址:全部 API(含无鉴权的 /api/admin/*)"
            "将暴露给该网络接口。本应用按本地单用户设计,不得绑 0.0.0.0 对外暴露;"
            "确属内网联调请自行确保网络边界(见 README 安全边界节)。", host)
    return host


def read_sidecar_meta(image: Path) -> dict | None:
    """读图件同名 .json sidecar(engines/figures.py 随图落盘的元数据)。

    容忍一切失败:缺文件/超限/坏 JSON/顶层非对象都返回 None,
    列表端点绝不因一个坏 sidecar 而 500。
    """
    sidecar = image.with_suffix(".json")
    try:
        if not sidecar.is_file() or sidecar.stat().st_size > _SIDECAR_MAX_BYTES:
            return None
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # ValueError 覆盖 JSONDecodeError/UnicodeDecodeError
        return None
    return data if isinstance(data, dict) else None


#: 会话显示名上限(check_session_id 的宽松版:name 只是标签,不做目录名)
_SESSION_NAME_MAX = 80


def check_session_name(name: str) -> str:
    """校验会话显示名;不合法直接 400。返回去首尾空白后的名字。

    与 check_session_id 的差异:name 不落文件系统,放开路径分隔符等敏感字符,
    上限放宽到 80;保留的检查是防 500/防 UI 破版的底线 —— 非空、无控制字符、
    可编码 UTF-8(孤代理会在 SQLite 绑定时逃逸为 500,同 id 校验的教训)。
    """
    trimmed = name.strip()
    if not trimmed or len(trimmed) > _SESSION_NAME_MAX:
        raise HTTPException(
            400, f"name 不合法:去首尾空白后长度须为 1-{_SESSION_NAME_MAX} 字符")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in trimmed):
        raise HTTPException(400, "name 不合法:不允许控制字符(含换行/制表符)")
    try:
        trimmed.encode("utf-8")
    except UnicodeEncodeError:
        raise HTTPException(400, "name 不合法:含无法编码为 UTF-8 的码位(孤代理)")
    return trimmed


def check_session_id(session_id: str) -> str:
    """校验 session_id 可安全用作目录名;不合法直接 400(结构化 detail)。"""
    sid = session_id
    if not sid or len(sid) > _SESSION_ID_MAX:
        raise HTTPException(400, f"session 不合法:长度须为 1-{_SESSION_ID_MAX} 字符")
    if any(c in _SESSION_FORBIDDEN_CHARS or ord(c) < 0x20 or ord(c) == 0x7F
           for c in sid):
        raise HTTPException(400, "session 不合法:不允许路径分隔符、控制字符或 "
                                 '\'/\\:*?"<>|\' 字符')
    if sid[0] == " " or sid[-1] in " .":
        raise HTTPException(400, "session 不合法:首尾不允许空格,结尾不允许 '.'")
    if sid.split(".")[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise HTTPException(400, f"session 不合法:{sid} 是 Windows 保留设备名")
    try:
        # 孤代理(U+D800-DFFF)可经原始字节体注入:无法编码 UTF-8,落到 SQLite
        # 绑定或 mkdir 时抛 UnicodeEncodeError 逃逸为 500(fuzz 波次发现 1,P1)
        sid.encode("utf-8")
    except UnicodeEncodeError:
        raise HTTPException(400, "session 不合法:含无法编码为 UTF-8 的码位(孤代理)")
    return sid


class TurnBody(BaseModel):
    session: str
    text: str


class PipelineBody(BaseModel):
    session: str
    run_id: str | None = None
    step_ids: list[int] | None = None


class MessageBody(BaseModel):
    session: str
    text: str
    deliver_as: str | None = None  # steer | follow_up | next_run(运行中必填)


class ActionBody(BaseModel):
    session: str
    run_id: str | None = None
    scope: str = "step"
    target: str
    action: str
    payload: dict = {}
    deliver_as: str = "steer"


class ForkBody(BaseModel):
    session: str
    run_id: str
    changes: dict[int, dict]


class SessionBody(BaseModel):
    id: str
    name: str | None = None
    mode: str = "expert"


class SessionPatchBody(BaseModel):
    """PATCH /api/sessions/{id}:至少给一个字段。

    name:重命名(check_session_name 宽松校验);
    archived:true=归档(同 DELETE 软删,受 running 守卫)/ false=还原。
    """
    name: str | None = None
    archived: bool | None = None


def create_app(home: Path | None = None) -> FastAPI:
    home = Path(home or os.environ.get("INSAR_HOME", "workspace")).resolve()
    home.mkdir(parents=True, exist_ok=True)
    db = Database(home / "insar.db")
    store = Store(db)
    contract = load_contract()
    drivers: dict[str, Driver] = {}
    # FastAPI 同步端点在线程池并发执行:driver_of 的 check-then-set 不互斥时,
    # 同一会话的两个首次请求会各建一个 Driver(各自 EventBus/探测,SSE 订阅到
    # 与实际执行不同的总线而收不到事件,REVIEW P2-7)
    drivers_lock = threading.Lock()

    app = FastAPI(title="insar-agent", version="0.1.0")

    @app.exception_handler(RequestValidationError)
    def _on_validation_error(request: Request, exc: RequestValidationError):
        # 422 错误体会回显出错输入;体内 NaN/Infinity(json.loads 的非标准扩展,
        # requests 类客户端可发出)经默认渲染 allow_nan=False 二次抛错逃逸为
        # 500(fuzz 波次发现 2,P2)。非有限浮点替换为 None 后安全渲染。
        safe = jsonable_encoder(
            {"detail": exc.errors()},
            custom_encoder={float: lambda v: v if math.isfinite(v) else None})
        return JSONResponse(status_code=422, content=safe)

    app.include_router(create_setup_router(home))  # 环境向导(/api/setup/*,settings.json 与 DB 同目录)
    from insar_agent.brain.provider import set_usage_sink
    from insar_agent.brain.usage import UsageLedger
    usage_ledger = UsageLedger(db, home=home)  # LLM 用量账本:每次调用的 token/成本流水(计费中转站)
    set_usage_sink(usage_ledger.record)        # provider 保持纯传输层,经模块级回调上报用量
    from insar_agent.api.llm_router import create_llm_router
    app.include_router(create_llm_router(home, usage_ledger))  # LLM 密钥/模型配置 + 用量账本
    app.include_router(version_router)             # 版本信息与更新检查(/api/version*)
    app.include_router(skills_router)              # 步骤技能文档(/api/skills*,规划/分诊知识源)
    app.include_router(create_admin_router(store))  # 外部终结与运维视图(/api/admin/*,absorb-E6)
    app.include_router(create_artifacts_router(store))  # 产物清单(/api/artifacts,文件面板数据源)
    app.include_router(create_doctor_router(home))  # 一键体检(/api/doctor,面向排障的秒级只读深检)
    from insar_agent.api.data_router import create_data_router; app.include_router(create_data_router(store))  # 点位时序数据
    app.include_router(create_diag_router(home))  # 诊断包一键导出(/api/diagnostics*)
    from insar_agent.api.data_catalog_router import create_data_catalog_router  # 数据集清单
    app.include_router(create_data_catalog_router(home))  # /api/datasets*(文件面板「数据集」区)
    from insar_agent.api.report_router import create_report_router
    app.include_router(create_report_router(store, home))  # 方法章节草稿(/api/report/draft)
    from insar_agent.api.visionqa_router import create_visionqa_router
    app.include_router(create_visionqa_router(home, store))  # AI 识图质检(/api/vision-qa)
    app.include_router(create_advisor_router(store, home))  # 下一步建议(/api/advise,run 终态建议卡)
    from insar_agent.api.memory_router import create_memory_router
    app.include_router(create_memory_router(store))  # 跨会话记忆(/api/memory*,记忆面板数据源)

    def driver_of(session_id: str) -> Driver:
        check_session_id(session_id)  # 边界校验:id 将成为目录名(见模块头注释)
        with drivers_lock:
            if session_id not in drivers:
                ws = home / "sessions" / session_id
                # LLM 路由:workspace/llm.json(界面可配)优先,环境变量兜底;
                # 都未配置 = brain 禁用,系统退化为手动流水线(§3.5 铁律)
                from insar_agent.brain.llm_config import routes_from_config
                drivers[session_id] = Driver(
                    store, workspace=ws,
                    brain=Brain(LLMProvider(routes_from_config(home))),
                    allow_simulated=os.environ.get("INSAR_ALLOW_SIMULATED", "1") == "1")
                store.create_session(session_id, session_id)
            return drivers[session_id]

    # 运行队列:全局串行调度(并发=1,对齐本机重计算管控),startup 恢复 pending
    run_queue = RunQueue(store)
    QueueScheduler(run_queue, driver_of).install(app)
    app.include_router(create_queue_router(store, run_queue, driver_of))

    # 回合泵任务的强引用(asyncio 只弱引用 task,不留强引用会被 GC 掐断)
    turn_tasks: set[asyncio.Task] = set()

    def ndjson(agen):
        """回合流:响应通道与回合执行解耦(协议纪律:POST 只代表「已接受」)。

        - 客户端中途断开只关闭响应,不取消回合:run 归服务端所有,继续推进到
          终态(事件仍发布到 SSE 总线),不留 status=running 却无人跟随的孤儿;
          用户主动取消走 /api/abort(control 位)。
        - 回合内部异常转成一条 note 事件(流协议里错误是事件,不是裸断连),
          细节进服务端日志。
        """
        async def gen():
            queue: asyncio.Queue = asyncio.Queue()

            async def pump():
                try:
                    async for event in agen:
                        queue.put_nowait(json.dumps(event, ensure_ascii=False) + "\n")
                except Exception:  # noqa: BLE001 —— 转结构化事件,绝不裸断连
                    log.exception("回合流内部异常")
                    queue.put_nowait(json.dumps(
                        {"t": "note", "tone": "bad",
                         "text": "服务内部错误,回合中止(详见服务端日志)"},
                        ensure_ascii=False) + "\n")
                finally:
                    queue.put_nowait(None)

            task = asyncio.create_task(pump())
            turn_tasks.add(task)
            task.add_done_callback(turn_tasks.discard)
            while True:
                line = await queue.get()
                if line is None:
                    break
                yield line

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    def resolve_run(session_id: str, run_id: str | None, *,
                    required: bool = True) -> dict | None:
        """按会话解析 run 并做归属校验。

        run_id 缺省 → 该会话最近一个 run;给了 run_id 但不属于该会话 → 404
        (统一按「不存在」处理,不泄露其他会话的 run 是否存在)。
        """
        run = store.get_run(run_id) if run_id else store.latest_run(session_id)
        if run is None:
            if required:
                raise HTTPException(404, "no run")
            return None
        if run["session_id"] != session_id:
            raise HTTPException(404, f"run {run['run_id']} 不存在或不属于会话 {session_id}")
        return run

    def running_run(session_id: str) -> dict | None:
        latest = store.latest_run(session_id)
        return latest if latest and latest["status"] == "running" else None

    # ---------------- 会话 ----------------

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": app.version}

    @app.get("/api/sessions")
    def sessions(include_archived: bool = False,
                 limit: int | None = Query(None, ge=1, le=1000),
                 cursor: str | None = None, q: str | None = None):
        # 默认不含已归档(软删除的「列表不显示」);?include_archived=1 给
        # 前端「已归档」折叠组当数据源
        if limit is not None or cursor is not None or q is not None:
            try:  # 分页/过滤路径(键集游标);带 limit 才有 next_cursor;坏 cursor → 400
                page = store.list_sessions_page(limit, cursor, include_archived, q)
            except ValueError as exc:
                raise HTTPException(400, str(exc))
            return page if limit is not None else page["items"]
        return store.list_sessions(include_archived=include_archived)

    @app.post("/api/sessions")
    def create_session(body: SessionBody):
        check_session_id(body.id)  # 先校验再落库:非法 id 不留半截会话行
        store.create_session(body.id, body.name or body.id, mode=body.mode)
        driver_of(body.id)
        return store.get_session(body.id)

    def guard_archivable(session_id: str) -> None:
        """归档守卫:有 running run 的会话拒绝归档(409)—— 先取消/等结束。
        看全量 run 而非最新:老 run 仍在跑而新 run 已建时,latest 口径会漏。"""
        if store.has_running_run(session_id):
            raise HTTPException(
                409, f"会话 {session_id} 有正在运行的 run,请先取消或等待结束后再归档")

    def require_session(session_id: str) -> dict:
        check_session_id(session_id)  # 目录名级校验:purge 要拿 id 拼工作区路径
        sess = store.get_session(session_id)
        if sess is None:
            raise HTTPException(404, f"会话 {session_id} 不存在")
        return sess

    def retire_workspace(session_id: str) -> str | None:
        """硬删只删 DB 行:工作区目录改名 <id>.deleted-<时间戳> 留人工回收。
        目录不存在返回 None;改名失败(句柄占用等)目录原样保留,绝不删文件。"""
        ws = home / "sessions" / session_id
        if not ws.is_dir():
            return None
        stamp = time.strftime("%Y%m%dT%H%M%S")
        target = ws.with_name(f"{session_id}.deleted-{stamp}")
        if target.exists():  # 同秒重复 purge 同名会话:补随机后缀防覆盖
            target = ws.with_name(f"{session_id}.deleted-{stamp}-{uuid.uuid4().hex[:6]}")
        try:
            ws.rename(target)
        except OSError as exc:
            log.warning("purge %s:工作区改名失败,目录原样保留:%s", session_id, exc)
            return None
        return target.name

    @app.patch("/api/sessions/{session_id}")
    def patch_session(session_id: str, body: SessionPatchBody):
        require_session(session_id)
        if body.name is None and body.archived is None:
            raise HTTPException(
                400, "PATCH 需要至少一个字段:name(重命名)或 archived(归档/还原)")
        if body.name is not None:
            store.rename_session(session_id, check_session_name(body.name))
        if body.archived is True:
            guard_archivable(session_id)
            store.archive_session(session_id)
        elif body.archived is False:
            store.restore_session(session_id)
        return store.get_session(session_id)

    @app.delete("/api/sessions/{session_id}")
    def delete_session(session_id: str, purge: bool = False):
        """默认软删除(归档):列表不显示,run 与工作区数据一律保留,可还原。

        ?purge=1 硬删除,极度保守:仅当会话无任何 run 时允许(有 run → 409
        只能归档);也只删 DB 行,工作区目录改名 <id>.deleted-<时间戳> 留人工回收。
        """
        require_session(session_id)
        if not purge:
            guard_archivable(session_id)
            store.archive_session(session_id)
            return {"ok": True, "archived": True, "session": store.get_session(session_id)}
        n_runs = store.count_runs(session_id)
        if n_runs > 0:
            raise HTTPException(
                409, f"会话 {session_id} 含 {n_runs} 个 run,只能归档(去掉 purge=1)")
        try:
            store.purge_session(session_id)  # 事务内复查 run 数:上面的预检只为报数
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        drivers.pop(session_id, None)  # 工作区路径即将失效,丢弃缓存的 driver
        moved = retire_workspace(session_id)
        return {"ok": True, "purged": True, "workspace_moved_to": moved}

    @app.get("/api/chat")
    def chat(session: str):
        return store.chat_history(session)

    # ---------------- registry / 环境 / 状态 ----------------

    @app.get("/api/registry")
    def registry(session: str | None = None):
        probe = driver_of(session).probe() if session else None
        out = []
        for cap in PIPELINE:
            methods = []
            feas = narrow_methods(cap, probe, allow_simulated=True) if probe else None
            feas_by_id = {f.method.id: f for f in feas} if feas else {}
            for m in cap.methods:
                f = feas_by_id.get(m.id)
                methods.append({
                    "id": m.id, "label": m.label, "engine": m.engine, "why": m.why,
                    "recommend": m.recommend, "extra": m.extra,
                    "ok": f.ok if f else True,
                    "simulated": f.simulated if f else False,
                    "blocked": f.blocked_reason if f else "",
                })
            out.append({
                "id": cap.id, "name": cap.name, "deps": list(cap.deps),
                "method": cap.default_method, "methods": methods,
                "params": {k: {"default": p.default, "kind": p.kind, "type": p.type,
                               "min": p.min, "max": p.max, "hint": p.hint}
                           for k, p in cap.params.items()},
                "outputs": [{"path": a.candidates[0], "kind": a.kind, "layout": a.layout}
                            for a in cap.artifacts],
                "replay": cap.replay,
                "timeouts": {"idle": cap.timeouts.idle, "total": cap.timeouts.total},
            })
        return out

    @app.get("/api/env")
    def env(session: str):
        d = driver_of(session)
        probe = d.probe(refresh=True)
        # WSL 引擎环境探测:发行版可达时并入(面板显示带 (wsl) 后缀的引擎)。
        # 纯查询、失败静默 —— 没装 WSL 的机器该端点行为不变。
        try:
            from insar_agent.runtime.wsl_probe import (merge_wsl_probe,
                                                       probe_wsl_engines_cached)

            wsl_result = probe_wsl_engines_cached(timeout=30.0)  # TTL 缓存,与向导共享
            if wsl_result.get("ok"):
                merge_wsl_probe(probe, wsl_result)
        except Exception:  # noqa: BLE001 —— 可选探测绝不拖垮环境面板
            # wsl_probe 契约上已把 runner 异常内部归一化,这里兜底编程性意外;
            # 留 debug 痕便于排障,不再静默(REVIEW P2-2)
            log.debug("WSL 引擎探测合并失败,按未探测处置", exc_info=True)
        return {
            "probe": probe.to_dict(),
            "thresholds": [{"key": k, "value": t.value, "source": t.source,
                            "ref": t.ref, "status": t.status}
                           for k, t in contract.items()],
        }

    @app.get("/api/runs")
    def runs(session: str, limit: int | None = Query(None, ge=1, le=1000),
             cursor: str | None = None, status: str | None = None):
        """该会话的 run 清单(前端 run 历史切换器的数据源,轻量窄集)。

        - 归属口径同 resolve_run:只列属于该会话的 run,不泄露其他会话的
          run 是否存在;会话不存在或还没有 run → 空清单(与 /api/state
          「无 run 不 404」一致,前端以此隐藏切换器)。
        - 排序:created_at 倒序(store.list_runs 的 SQL 排序,最新在前)。
        - 每条附 parent_run_id(fork 谱系)与步骤终态统计
          (total|done|skipped|failed),不含 intent/tool_versions 等大字段。
        - 纯读端点:不走 driver_of,不为未知会话创建目录/会话行。
        - 可选 limit/cursor/status 走键集分页/过滤(store.list_runs_page);
          全不传时与老响应逐字节一致,next_cursor 字段仅在带 limit 时出现。
        """
        page = None
        if limit is not None or cursor is not None or status is not None:
            try:  # 分页/过滤路径(键集游标);坏 cursor → 400
                page = store.list_runs_page(session, limit, cursor, status)
            except ValueError as exc:
                raise HTTPException(400, str(exc))
        items = []
        for run in (page["items"] if page is not None else store.list_runs(session)):
            steps = store.load_steps(run["run_id"])
            counts = {"done": 0, "skipped": 0, "failed": 0}
            for s in steps:
                if s.state in counts:
                    counts[s.state] += 1
            items.append({
                "run_id": run["run_id"],
                "parent_run_id": run["parent_run_id"],
                "created_at": run["created_at"],
                "status": run["status"],
                "scenario": run["scenario"],
                "steps": {"total": len(steps), **counts},
            })
        extra = {"next_cursor": page["next_cursor"]} if limit is not None else {}
        return {"session": session, "runs": items, **extra}

    @app.get("/api/state")
    def state(session: str, run_id: str | None = None):
        run = resolve_run(session, run_id, required=False)
        if run is None:
            return {"run": None, "steps": []}
        steps = [{
            "id": s.step_id, "name": s.name, "method": s.method, "params": s.params,
            "stage": s.stage, "state": s.state, "stale": s.stale,
            "staleReason": s.stale_reason, "failureClass": s.failure_class,
            "fingerprint": s.eval_hash[:12], "runOk": s.run_ok,
            "logOffset": s.log_offset,
        } for s in store.load_steps(run["run_id"])]
        return {"run": dict(run), "steps": steps}

    # ---------------- 回合(流式) ----------------

    @app.post("/api/turn")
    def turn(body: TurnBody):
        return ndjson(driver_of(body.session).turn(body.session, body.text))

    @app.post("/api/pipeline")
    def pipeline(body: PipelineBody):
        d = driver_of(body.session)  # 先做 session 校验,再解析 run
        run = resolve_run(body.session, body.run_id, required=False)
        if body.run_id and run is None:
            raise HTTPException(404, f"run {body.run_id} 不存在")
        if body.step_ids:
            # 流式响应一旦开始就无法改状态码:step_ids 必须在开流前校验,
            # 否则非法 id 会在 driver 循环里 KeyError 炸断连接
            if run is None:
                raise HTTPException(404, "no run")
            planned = {s.step_id for s in store.load_steps(run["run_id"])}
            bad = [sid for sid in body.step_ids if sid not in planned]
            if bad:
                raise HTTPException(400, f"步骤 id 不在该 run 的计划内:{bad}")
        return ndjson(d.execute(body.session, body.run_id, body.step_ids))

    @app.post("/api/resume")
    def resume(body: PipelineBody):
        return ndjson(driver_of(body.session).resume(body.session))

    @app.post("/api/abort")
    def abort(body: PipelineBody):
        run = resolve_run(body.session, body.run_id)  # 404 覆盖不存在/不属于该会话
        driver_of(body.session).request_cancel(run["run_id"])
        return JSONResponse({"accepted": True}, status_code=202)

    # ---------------- 消息与干预 ----------------

    @app.post("/api/message")
    def message(body: MessageBody):
        if store.get_session(body.session) is None:
            # chat_messages 有外键约束:不先查会话,未知 session 会 IntegrityError → 500
            raise HTTPException(404, f"会话 {body.session} 不存在")
        if body.deliver_as is not None and body.deliver_as not in DELIVER_AS:
            raise HTTPException(
                400, f"未知投递语义 {body.deliver_as}(可用:{'|'.join(DELIVER_AS)})")
        active = running_run(body.session)
        if active and body.deliver_as not in DELIVER_AS:
            # absorb-E4(pi rpc.md:56-65):运行中投递必须显式声明语义
            raise HTTPException(
                400, "run 正在执行:必须指定 deliver_as=steer|follow_up|next_run")
        store.append_chat(body.session, "user", body.text,
                          meta={"deliver_as": body.deliver_as} if body.deliver_as else None)
        return JSONResponse({"accepted": True, "deliver_as": body.deliver_as},
                            status_code=202)

    @app.post("/api/actions")
    def actions(body: ActionBody):
        # 入队即校验(闭集 + payload 形状):干预队列是跨回合消费的,坏动作
        # 入队后会在之后某次 turn/execute 的流中间引爆(int(target)/payload KeyError),
        # 那时已无法给客户端返回错误 —— 必须挡在入口。
        if body.action not in _KNOWN_ACTIONS:
            raise HTTPException(
                400, f"未知动作 {body.action}(可用:{sorted(_KNOWN_ACTIONS)})")
        if body.deliver_as not in DELIVER_AS:
            raise HTTPException(
                400, f"未知投递语义 {body.deliver_as}(可用:{'|'.join(DELIVER_AS)})")
        if body.scope not in ("step", "run"):
            raise HTTPException(400, f"未知 scope {body.scope}(可用:step|run)")
        if body.action in _STEP_ACTIONS:
            try:
                sid = int(body.target)
            except (TypeError, ValueError):
                raise HTTPException(
                    400, f"{body.action} 的 target 必须是步骤号,收到 {body.target!r}")
            cap = REGISTRY.get(sid)
            if cap is None:
                raise HTTPException(400, f"未知步骤 {sid}")
            if body.action == "SET_METHOD":
                method = body.payload.get("method")
                if not isinstance(method, str) or cap.method(method) is None:
                    raise HTTPException(
                        400, f"第 {sid} 步没有方法 {method!r}"
                             f"(候选:{[m.id for m in cap.methods]})")
            if body.action == "SET_PARAMS":
                params = body.payload.get("params")
                if not isinstance(params, dict):
                    raise HTTPException(400, "SET_PARAMS 需要 payload.params 为 JSON 对象")
                errors = cap.validate_params(params)
                if errors:
                    raise HTTPException(400, f"参数校验失败:{errors}")
        # 动作归属:入队即绑定 run(缺省取该会话最近 run)。没有归属的动作会被
        # 任何 run 的 driver 消费(跨 run/会话互吞,REVIEW P1),溯源也无法入账
        run = resolve_run(body.session, body.run_id, required=False)
        if body.action == "KILL" and run:
            driver_of(body.session).request_cancel(run["run_id"])
        action_id = store.push_action(scope=body.scope, target=body.target,
                                      action=body.action, payload=body.payload,
                                      deliver_as=body.deliver_as,
                                      run_id=run["run_id"] if run else None)
        return JSONResponse({"accepted": True, "id": action_id}, status_code=202)

    @app.get("/api/impact")
    def impact(session: str, step: int = Query(ge=0, le=_STEP_ID_MAX),
               method: str | None = None, params: str | None = None,
               run_id: str | None = None):
        run = resolve_run(session, run_id)
        d = driver_of(session)
        if store.load_step(run["run_id"], step) is None:
            raise HTTPException(404, f"步骤 {step} 不在该 run 中")
        if params is not None:
            try:
                params_patch = json.loads(params)
            except json.JSONDecodeError as exc:
                raise HTTPException(400, f"params 不是合法 JSON:{exc}")
            if not isinstance(params_patch, dict):
                raise HTTPException(400, "params 必须是 JSON 对象(参数名 → 值)")
        else:
            params_patch = None
        if method is not None:
            cap = d.registry.get(step)
            if cap is not None and cap.method(method) is None:
                raise HTTPException(
                    400, f"第 {step} 步没有方法 {method!r}"
                         f"(候选:{[m.id for m in cap.methods]})")
        imp = preview_change(
            store, run["run_id"], step, registry=d.registry,
            tool_versions=d.probe().tool_versions(), method=method,
            params_patch=params_patch)
        return {
            "changedStep": imp.changed_step, "reason": imp.reason,
            "affected": imp.affected, "rerunMinutes": imp.rerun_minutes,
            "rerunBasis": imp.rerun_basis,
        }

    @app.post("/api/fork")
    def fork(body: ForkBody):
        d = driver_of(body.session)
        run = resolve_run(body.session, body.run_id)  # fork_run 对未知 run 抛 KeyError
        changes = {int(k): v for k, v in body.changes.items()}
        planned = {s.step_id for s in store.load_steps(run["run_id"])}
        for sid, change in changes.items():
            if sid not in planned:
                raise HTTPException(400, f"步骤 id 不在该 run 的计划内:{sid}")
            cap = d.registry.get(sid)
            m = change.get("method")
            if m is not None and (not isinstance(m, str)
                                  or (cap is not None and cap.method(m) is None)):
                raise HTTPException(
                    400, f"第 {sid} 步没有方法 {m!r}"
                         f"(候选:{[x.id for x in cap.methods] if cap else []})")
            if "params" in change and not isinstance(change["params"], dict):
                raise HTTPException(400, f"changes[{sid}].params 必须是 JSON 对象")
        try:
            plan = fork_run(store, run["run_id"], registry=d.registry,
                            changes=changes, probe=d.probe())
        except ValueError as exc:  # cap.validate_params 拒绝(未声明参数/越界)
            raise HTTPException(400, str(exc))
        return {"runId": plan.run_id,
                "steps": [{"id": p.step_id, "state": p.state, "method": p.method}
                          for p in plan.steps]}

    # ---------------- 导出 ----------------

    @app.get("/api/provenance")
    def provenance(session: str, run_id: str | None = None):
        run = resolve_run(session, run_id)
        return export_provenance(store, run["run_id"], contract=contract,
                                 workspace=driver_of(session).workspace)

    @app.get("/api/run.sh")
    def run_script(session: str, run_id: str | None = None):
        run = resolve_run(session, run_id)
        return PlainTextResponse(
            export_run_script(store, run["run_id"], driver_of(session).workspace))

    @app.get("/api/methods.md")
    def methods(session: str, run_id: str | None = None):
        run = resolve_run(session, run_id)
        doc = export_provenance(store, run["run_id"], contract=contract,
                                workspace=driver_of(session).workspace)
        md, source = driver_of(session).brain.narrate(doc)
        return PlainTextResponse(md, headers={"X-Narrate-Source": source})

    @app.get("/api/repro-bundle")
    def repro_bundle(session: str, run_id: str | None = None):
        """复现包 zip 一键导出(provenance / run.sh / methods.md / qa.json /
        图件 PNG+sidecar / MANIFEST 的 sha256 清单),交付给同行/审稿人。

        会话归属校验同 resolve_run(跨会话按 404「不存在」);run 非 done 一律
        409 —— 复现包对外代表「已完成 run 的可复现记录」,中途态出包会让收件人
        拿到与最终账本不一致的半成品。
        """
        run = resolve_run(session, run_id)
        if run["status"] != "done":
            raise HTTPException(
                409, f"run {run['run_id']} 状态为 {run['status']},复现包只对已完成"
                     "(done)的 run 导出:半成品的账本/图件不完整,请等待运行结束"
                     "或先 resume 收尾后再出包")
        buf = build_repro_bundle(store, run["run_id"], driver_of(session).workspace,
                                 contract=contract)
        # Content-Disposition 转义:HTTP 头须 latin-1 —— ASCII 档名做字符白名单
        # 清洗(防引号/控制字符破坏 quoted-string),原始档名走 RFC 5987 的
        # filename*(UTF-8 百分号编码),两者并给以兼容新旧客户端
        prefix = run["run_id"][:24]
        ascii_name = "insar-repro-" + re.sub(r"[^A-Za-z0-9._-]", "_", prefix) + ".zip"
        utf8_name = quote(f"insar-repro-{prefix}.zip", safe="")
        return StreamingResponse(buf, media_type="application/zip", headers={
            "Content-Disposition":
                f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}",
        })

    @app.get("/api/trace")
    def trace(session: str, run_id: str | None = None,
              limit: int | None = Query(None, ge=1, le=1000), cursor: str | None = None):
        run = resolve_run(session, run_id, required=False)
        if run is None:
            return [] if limit is None else {"items": [], "next_cursor": None}
        if limit is not None or cursor is not None:
            try:  # 事件回放分页(键集游标);带 limit 才有 next_cursor;坏 cursor → 400
                page = store.events_page(run["run_id"], limit, cursor)
            except ValueError as exc:
                raise HTTPException(400, str(exc))
            return page if limit is not None else page["items"]
        return store.trace_of(run["run_id"])

    @app.get("/api/logs")
    def logs(session: str, step: int = Query(ge=0, le=_STEP_ID_MAX),
             run_id: str | None = None, tail_kb: int = Query(64, ge=1, le=1024)):
        """步骤日志尾部(面板 8「终端」):读 log_path 末尾 N KB;无 run/步骤/日志文件 → 404。"""
        run = resolve_run(session, run_id)
        step_row = store.load_step(run["run_id"], step)
        if step_row is None:
            raise HTTPException(404, "no step")
        path = Path(step_row.log_path) if step_row.log_path else None
        if path is None or not path.exists():
            raise HTTPException(404, "no log file")
        size = path.stat().st_size
        limit = tail_kb * 1024
        with path.open("rb") as f:
            if size > limit:
                f.seek(size - limit)
            data = f.read()
        text = data.decode("utf-8", errors="replace")
        if size > limit and "\n" in text:
            text = text.split("\n", 1)[1]  # 掐掉截断处的半行
        return PlainTextResponse(text, headers={
            "X-Log-Size": str(size),
            "X-Log-Truncated": "1" if size > limit else "0",
        })

    # ---------------- 图像产物(面板 2「影像」) ----------------

    def resolve_artifact_file(run: dict, rel_path: str) -> Path | None:
        """产物相对路径 → run 工作区内的绝对路径;越界一律返回 None。

        artifacts.path 由执行器落库为相对工作区的路径,但该值可能来自
        坏数据/被篡改的 DB 行 —— 与 check_session_id 同理,读文件前必须
        在边界处规范化复核:绝对路径、盘符、../ 穿越都不放行。
        调用方对 None 统一按 404 处理,错误信息不携带磁盘路径。

        既定边界(REVIEW-r2 P2-7):经目录联接/符号链接导入的产物树 resolve
        后落在工作区外,同样返回 None(404)—— 联接树产物不经本 API 直读;
        今天的 figures 均由脚本在工作区内直写目录,不受影响。
        """
        base = Path(run["workspace"]).resolve()
        rel = Path(rel_path)
        if rel.is_absolute() or rel.drive:
            return None
        target = (base / rel).resolve()
        if target == base or not target.is_relative_to(base):
            return None
        return target

    @app.get("/api/figures")
    def figures(session: str, run_id: str | None = None):
        """该 run 全部图像类产物的清单(按扩展名闭集过滤,文件已缺失的行不列)。

        条目含 step/artId/文件名/尺寸/mtime 与三档 url(三档尺寸契约,
        engines/figures.py 的产物目录约定):
          - url      浏览档(_browse 存在时优先,灯箱用)
          - fullUrl  原图(「查看原图」/下载)
          - thumbUrl 缩略档(_thumb 存在时优先,网格用)
        三档缺档一律回退原图;_browse/_thumb 文件不单独成条目。
        同名 .json sidecar 存在且可解析时并入 meta 字段(坏文件容忍不并入)。
        尺寸与 mtime 取落盘原图实测值,不信 DB 记录(可能已被覆写)。

        健壮性与口径(REVIEW-r2 P2-6/P2-7):画廊 3s 轮询会撞上出图步骤的
        覆写/清理窗口 —— 枚举与 stat 之间消失的文件逐行跳过,绝不 500 整表;
        目录成员 resolve 后必须仍落在产物目录内(与取回端点同一判据),
        指向外部的符号链接不列出(列了也取不回,还泄漏外部文件元数据)。
        """
        run = resolve_run(session, run_id, required=False)
        if run is None:
            return {"run": None, "figures": []}

        def file_url(art: dict, member: str | None = None) -> str:
            q = {"session": session, "run_id": run["run_id"],
                 "step": art["step_id"], "art_id": art["art_id"]}
            if member:
                q["file"] = member
            return "/api/artifact-file?" + urlencode(q)

        def entry(art: dict, target: Path, member: str | None = None,
                  browse: str | None = None, thumb: str | None = None) -> dict | None:
            try:
                st = target.stat()
            except OSError:
                return None  # is_file()/iterdir() 与 stat() 的窗口内文件被清理:跳过该行
            full = file_url(art, member)
            item = {
                "step": art["step_id"], "artId": art["art_id"],
                "name": target.name, "path": art["path"], "kind": art["kind"],
                "size": st.st_size, "mtime": st.st_mtime,
                "url": file_url(art, browse) if browse else full,
                "fullUrl": full,
                "thumbUrl": file_url(art, thumb) if thumb else full,
            }
            meta = read_sidecar_meta(target)
            if meta is not None:
                item["meta"] = meta
            return item

        def within(member: Path, base: Path) -> bool:
            # 成员口径与 /api/artifact-file 的取回判据一致:resolve 后仍须落在
            # 产物目录内 —— symlink 指向外部的成员不列(REVIEW-r2 P2-7);
            # resolve 期间文件消失(竞态)按不在场处置
            try:
                return member.resolve().is_relative_to(base)
            except OSError:
                return False

        items = []
        for art in store.artifacts_of(run["run_id"]):
            target = resolve_artifact_file(run, art["path"])
            if target is None:
                continue
            if Path(art["path"]).suffix.lower() in _IMAGE_MEDIA_TYPES:
                if target.is_file():
                    item = entry(art, target)
                    if item is not None:
                        items.append(item)
            elif target.is_dir():
                # 目录型产物(注册表里 figures 声明的是 products/figures 目录):
                # 枚举目录内图像文件 —— 真实链的图件都长在这里,只按产物路径
                # 后缀过滤会让画廊对标准管线永远空转(2026-08-12 终验发现)
                try:
                    children = sorted(p for p in target.iterdir()
                                      if p.is_file()
                                      and p.suffix.lower() in _IMAGE_MEDIA_TYPES
                                      and within(p, target))
                except OSError:
                    continue  # 目录本身在枚举窗口内被清理:整个产物行跳过
                by_stem = {p.stem: p.name for p in children}
                listed = 0
                for child in children:
                    stem = child.stem
                    # 基图存在的 _browse/_thumb 档并入基图条目,不单独列;
                    # 孤档(基图缺失)仍按普通图件列出,列表不吞真实文件
                    if any(stem.endswith(sfx) and stem[:-len(sfx)] in by_stem
                           for sfx in _TIER_SUFFIXES):
                        continue
                    item = entry(art, child, member=child.name,
                                 browse=by_stem.get(stem + "_browse"),
                                 thumb=by_stem.get(stem + "_thumb"))
                    if item is None:
                        continue
                    items.append(item)
                    listed += 1
                    if listed >= 100:
                        break
        items.sort(key=lambda x: (x["step"], x["artId"], x["name"]))
        return {"run": run["run_id"], "figures": items}

    @app.get("/api/artifact-file")
    def artifact_file(session: str, art_id: str,
                      step: int = Query(ge=0, le=_STEP_ID_MAX),
                      run_id: str | None = None, file: str | None = None):
        """产物图像文件本体(只读 FileResponse)。

        安全边界:非图像扩展名 400;产物不存在/路径越界/跨会话一律 404,
        且不泄露磁盘路径(与 resolve_run 的「不存在」口径一致)。
        file 参数用于目录型产物(如 products/figures)取内部成员:成员解析后
        必须仍落在产物目录内(防 ../ 越界),且同受图像扩展名闭集约束。
        """
        run = resolve_run(session, run_id)
        art = next((a for a in store.artifacts_of(run["run_id"], step)
                    if a["art_id"] == art_id), None)
        if art is None:
            raise HTTPException(404, "no artifact")
        target = resolve_artifact_file(run, art["path"])
        if target is None:
            raise HTTPException(404, "no artifact file")
        if file is not None:
            if not target.is_dir():
                raise HTTPException(404, "no artifact file")
            try:
                member = (target / file).resolve()
                member.relative_to(target.resolve())
            except (ValueError, OSError):
                raise HTTPException(404, "no artifact file")
            target = member
            media = _IMAGE_MEDIA_TYPES.get(target.suffix.lower())
        else:
            media = _IMAGE_MEDIA_TYPES.get(Path(art["path"]).suffix.lower())
        if media is None:
            raise HTTPException(
                400, f"仅支持图像类产物({'/'.join(sorted(e.lstrip('.') for e in _IMAGE_MEDIA_TYPES))})")
        if not target.is_file():
            raise HTTPException(404, "no artifact file")
        return FileResponse(target, media_type=media)

    # ---------------- 全局 SSE ----------------

    @app.get("/api/events")
    async def events(session: str, request: Request):
        d = driver_of(session)

        async def gen():
            q = d.bus.subscribe()
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        import asyncio

                        event = await asyncio.wait_for(q.get(), timeout=15.0)
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    except TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                d.bus.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream")

    # ---------------- 静态 UI ----------------

    if PROTOTYPE_DIR.exists():
        app.mount("/", StaticFiles(directory=str(PROTOTYPE_DIR), html=True), name="ui")

    # 安全响应头(最外层包裹,对含静态 UI 在内的全部响应生效;
    # CSP 表按落盘 HTML 启动时算一次,见 scan_ui_csp)
    app.add_middleware(SecurityHeadersMiddleware, csp_by_path=scan_ui_csp(PROTOTYPE_DIR))

    return app


def main() -> None:
    import uvicorn

    app = create_app()
    uvicorn.run(app, host=resolve_bind_host(),
                port=int(os.environ.get("INSAR_PORT", "8873")))


if __name__ == "__main__":
    main()
