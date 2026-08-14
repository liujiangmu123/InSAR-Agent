"""MCP server(src/insar_agent/mcp)对真实 FastAPI 后端的端到端验收。

组网(全程无独立进程):
  - 后端:create_app(home=tmp)真实 app —— 多数用例经 httpx.ASGITransport
    直连;受理窗断流语义用例另起 in-loop uvicorn(随机 18xxx 高位端口,
    同事件循环任务,用完置 should_exit 收尾);
  - MCP:官方 SDK 的内存流对接(create_client_server_memory_streams +
    ClientSession),对 server 实例做真实 initialize/list_tools/call_tool;
  - 密封:probe 打空桩(引擎全缺 → 模拟执行)、LLM 环境变量清空(意图识别
    走规则/表单层),不做任何真实 InSAR 计算。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import threading
import time
from contextlib import asynccontextmanager
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("mcp", reason="需要可选依赖组 [mcp]:pip install -e .[mcp]")

import anyio
import httpx
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from conftest import TIME_FACTOR
from insar_agent.api.app import create_app
from insar_agent.mcp import backend as mcp_backend
from insar_agent.mcp import server as mcp_server_module
from insar_agent.mcp.server import mcp as insar_mcp

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

EXPECTED_TOOLS = {
    "insar_list_sessions", "insar_create_session", "insar_plan_run",
    "insar_execute_run", "insar_run_status", "insar_intervene",
    "insar_get_provenance", "insar_list_figures", "insar_converse",
}


def run(coro):
    """每个用例自持事件循环:后端 app 的回合泵、httpx 客户端、MCP 内存会话
    都必须建在同一个循环内(asyncio.run 收尾时统一取消余留任务)。"""
    return asyncio.run(coro)


@asynccontextmanager
async def mcp_client():
    """对 server 实例开一条进程内 MCP 会话(stdio 语义的内存流等价物)。"""
    lowlevel = insar_mcp._lowlevel_server
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        async with anyio.create_task_group() as tg:
            tg.start_soon(partial(
                lowlevel.run, *server_streams,
                lowlevel.create_initialization_options(), raise_exceptions=False))
            async with ClientSession(*client_streams) as session:
                await session.initialize()
                yield session
            tg.cancel_scope.cancel()


async def call(session: ClientSession, tool: str, args: dict | None = None):
    return await session.call_tool(tool, args or {})


def payload_of(result) -> dict:
    """取工具的结构化返回(structured_content 优先,文本 JSON 兜底)。"""
    assert not result.is_error, f"工具意外报错:{result.content}"
    sc = result.structured_content
    if isinstance(sc, dict):
        return sc["result"] if set(sc) == {"result"} else sc
    import json

    return json.loads(result.content[0].text)


def error_text(result) -> str:
    assert result.is_error, f"预期工具报错,实际成功:{result.content}"
    return result.content[0].text


# ---------------- 后端 fixture(密封) ----------------

@pytest.fixture()
def backend_app(tmp_path, monkeypatch):
    """真实 FastAPI 后端(密封):空 probe → 全链模拟;LLM 变量清空。"""
    from insar_agent.runtime.probe import ProbeResult

    def empty_probe(*args, **kwargs):
        return ProbeResult(
            engines={k: None for k in ("isce2", "mintpy", "snaphu", "gdal", "snap",
                                       "pystamps", "pyaps")},
            credentials={"earthdata": False, "cds": False, "gacos": False},
            disk_free_gb=100.0, cpu_count=8)

    monkeypatch.setattr("insar_agent.loop.driver.probe_environment", empty_probe)
    for var in ("INSAR_LLM_BASE_URL", "INSAR_LLM_MODEL",
                "INSAR_LLM_FALLBACK_BASE_URL", "INSAR_LLM_FALLBACK_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("INSAR_ALLOW_SIMULATED", "1")
    monkeypatch.setenv("INSAR_MCP_HTTP_TIMEOUT", "15")
    app = create_app(home=tmp_path / "home")
    yield app
    mcp_backend.reset()
    mcp_server_module._session_scenarios.clear()


@pytest.fixture()
def asgi_backend(backend_app):
    """MCP 侧 httpx 经 ASGITransport 直连后端 app(免起进程/免端口)。"""
    mcp_backend.configure(base_url="http://insar-backend.test",
                          transport=httpx.ASGITransport(app=backend_app))
    return backend_app


# ---------------- 用例 ----------------

def test_tool_enumeration_and_annotations(asgi_backend):
    """工具枚举:9 个 insar_ 前缀工具,描述非空,读写注解正确。"""
    async def flow():
        async with mcp_client() as sess:
            listed = await sess.list_tools()
            by_name = {t.name: t for t in listed.tools}
            assert set(by_name) == EXPECTED_TOOLS
            for t in by_name.values():
                assert t.description and t.description.strip()
                assert t.input_schema.get("type") == "object"
            assert by_name["insar_run_status"].annotations.read_only_hint is True
            assert by_name["insar_get_provenance"].annotations.read_only_hint is True
            assert by_name["insar_intervene"].annotations.destructive_hint is True
            assert by_name["insar_execute_run"].annotations.read_only_hint is False
    run(flow())


def test_full_chain_simulated(asgi_backend, tmp_path):
    """全链:create_session → plan_run → execute_run → run_status 轮询到终态
    → get_provenance / list_figures(模拟模式,秒级)。"""
    async def flow():
        async with mcp_client() as sess:
            created = payload_of(await call(sess, "insar_create_session",
                                            {"name": "MCP 联调"}))
            sid = created["session"]["session_id"]
            assert sid.startswith("mcp-") and created["session"]["name"] == "MCP 联调"

            listed = payload_of(await call(sess, "insar_list_sessions"))
            assert any(s["session_id"] == sid for s in listed["sessions"])

            plan = payload_of(await call(sess, "insar_plan_run", {
                "session_id": sid, "intent_text": "Ridgecrest 地震同震形变分析"}))
            rid = plan["run_id"]
            assert rid and plan["run_status"] == "ready"
            assert plan["simulated"] is True          # 空 probe → 引擎全缺 → 模拟
            assert plan["problems"] == [] and plan["questions"] == []
            assert len(plan["steps"]) == 11
            assert plan["decision_step"] is not None
            assert plan["estimated_minutes"] is None  # 无历史依据不编数(§7.5)

            ex = payload_of(await call(sess, "insar_execute_run", {"session_id": sid}))
            assert ex["accepted"] is True and ex["run_id"] == rid
            assert "insar_run_status" in ex["events_hint"]

            deadline = time.monotonic() + 120
            while True:
                st = payload_of(await call(sess, "insar_run_status", {"run_id": rid}))
                if st["terminal"]:
                    break
                assert time.monotonic() < deadline, f"run 未在限时内到终态:{st['run']}"
                await asyncio.sleep(0.2)
            assert st["run"]["status"] == "done"
            assert st["failures"] == []
            counts = st["counts"]
            # quake 场景:2-6 云端跳过,其余执行到 done
            assert counts.get("done", 0) + counts.get("skipped", 0) == 11
            step_row = st["steps"][0]
            assert {"id", "name", "method", "state", "stage",
                    "failure_class"} <= set(step_row)

            prov = payload_of(await call(sess, "insar_get_provenance", {"run_id": rid}))
            doc = prov["provenance"]
            assert doc["schema_version"] == "1.0" and doc["simulated"] is True
            assert isinstance(prov["truncated"], bool)

            # 模拟链不产 PNG:向 run 工作区补一张小图,验证图件清单与绝对 URL
            gallery = tmp_path / "home" / "sessions" / sid / "products" / "figures"
            gallery.mkdir(parents=True, exist_ok=True)
            (gallery / "velocity.png").write_bytes(PNG_BYTES)
            figs = payload_of(await call(sess, "insar_list_figures", {"run_id": rid}))
            assert figs["run_id"] == rid and figs["count"] >= 1
            names = {f["name"] for f in figs["figures"]}
            assert "velocity.png" in names
            for f in figs["figures"]:
                assert f["url"].startswith("http://insar-backend.test/api/artifact-file")
    run(flow())


def test_plan_run_asks_when_scenario_unknown(asgi_backend):
    """意图缺信息:后端发 ask(表单)→ plan_run 返回 questions 且不给 run_id;
    create_session 的 scenario 提示可补救(并入意图文本的规则识别)。"""
    async def flow():
        async with mcp_client() as sess:
            sid = payload_of(await call(sess, "insar_create_session",
                                        {"name": "无场景"}))["session"]["session_id"]
            plan = payload_of(await call(sess, "insar_plan_run", {
                "session_id": sid, "intent_text": "帮我随便分析一下形变"}))
            assert plan["run_id"] is None
            assert plan["questions"], "缺场景时应返回补充信息问题"
            assert "场景" in plan["next"]

            sid2 = payload_of(await call(sess, "insar_create_session", {
                "name": "带场景提示", "scenario": "quake"}))["session"]["session_id"]
            plan2 = payload_of(await call(sess, "insar_plan_run", {
                "session_id": sid2, "intent_text": "帮我随便分析一下形变"}))
            assert plan2["run_id"] and plan2["scenario"] == "quake"
            assert plan2["questions"] == []
    run(flow())


def test_intervene_accept_and_closed_set_passthrough(asgi_backend):
    """干预:合法动作 202 入队(RESUME 别名归一为 PLAY);闭集校验错误
    (未知动作/未知方法)由后端 400 原样透传。"""
    async def flow():
        async with mcp_client() as sess:
            sid = payload_of(await call(sess, "insar_create_session",
                                        {"name": "干预"}))["session"]["session_id"]
            plan = payload_of(await call(sess, "insar_plan_run", {
                "session_id": sid, "intent_text": "Ridgecrest 地震同震"}))
            rid = plan["run_id"]

            pause = payload_of(await call(sess, "insar_intervene",
                                          {"run_id": rid, "action": "PAUSE"}))
            assert pause["accepted"] is True and pause["action_id"]

            resume = payload_of(await call(sess, "insar_intervene",
                                           {"run_id": rid, "action": "RESUME"}))
            assert resume["accepted"] is True and resume["action"] == "PLAY"

            # SET_PARAMS 容错形态:直接给参数字典,自动包 {"params": ...}
            setp = payload_of(await call(sess, "insar_intervene", {
                "run_id": rid, "action": "SET_PARAMS", "target": 7,
                "payload": {"max_temporal_baseline": 90},
                "deliver_as": "next_run"}))
            assert setp["accepted"] is True

            bogus_action = await call(sess, "insar_intervene",
                                      {"run_id": rid, "action": "FROBNICATE"})
            assert "未知动作" in error_text(bogus_action)

            bogus_method = await call(sess, "insar_intervene", {
                "run_id": rid, "action": "SET_METHOD", "target": 9,
                "payload": {"method": "nope"}})
            assert "没有方法" in error_text(bogus_method)
    run(flow())


def test_run_status_unknown_run(asgi_backend):
    """查不到的 run:报错给出行动指引(先 plan_run / 传 session_id)。"""
    async def flow():
        async with mcp_client() as sess:
            res = await call(sess, "insar_run_status", {"run_id": "no-such-run"})
            assert "insar_plan_run" in error_text(res)
    run(flow())


def test_backend_down_gives_startup_guidance(monkeypatch):
    """错误面:后端不可达时,工具报错必须自带启动指引(命令 + 环境变量)。"""
    monkeypatch.setenv("INSAR_MCP_HTTP_TIMEOUT", "5")
    with socket.socket() as s:  # 拿一个刚释放的空闲端口:必然连接拒绝
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    mcp_backend.configure(base_url=f"http://127.0.0.1:{port}")
    try:
        async def flow():
            async with mcp_client() as sess:
                res = await call(sess, "insar_list_sessions")
                text = error_text(res)
                assert "python -m insar_agent.api.app" in text
                assert "INSAR_API_BASE" in text
        run(flow())
    finally:
        mcp_backend.reset()


def test_execute_detach_then_poll_over_live_http(backend_app, monkeypatch):
    """真 HTTP 流(in-loop uvicorn,随机 18xxx 高位端口):execute_run 在受理窗
    内看到 step.start 即断流返回,断流不取消 run —— 随后轮询到 done。"""
    monkeypatch.setenv("INSAR_MCP_ACCEPT_WINDOW", "5")

    async def flow():
        import uvicorn

        port = _free_port_18xxx()
        server = uvicorn.Server(uvicorn.Config(
            backend_app, host="127.0.0.1", port=port, log_level="warning"))
        serve_task = asyncio.create_task(server.serve())
        try:
            deadline = time.monotonic() + 15
            while not server.started:
                if serve_task.done():
                    serve_task.result()  # 启动即失败:把真实异常抛给断言现场
                assert time.monotonic() < deadline, "uvicorn 未在 15s 内就绪"
                await asyncio.sleep(0.05)
            mcp_backend.configure(base_url=f"http://127.0.0.1:{port}")  # 真网络
            async with mcp_client() as sess:
                sid = payload_of(await call(sess, "insar_create_session",
                                            {"name": "live"}))["session"]["session_id"]
                plan = payload_of(await call(sess, "insar_plan_run", {
                    "session_id": sid, "intent_text": "Ridgecrest 地震同震"}))
                rid = plan["run_id"]
                assert rid

                ex = payload_of(await call(sess, "insar_execute_run",
                                           {"session_id": sid, "run_id": rid}))
                assert ex["accepted"] is True
                assert ex["started_step"] == 1      # 真流式:受理窗内看到首步开跑
                assert ex["stream_detached"] is True

                poll_deadline = time.monotonic() + 120
                while True:
                    st = payload_of(await call(sess, "insar_run_status",
                                               {"run_id": rid}))
                    if st["terminal"]:
                        break
                    assert time.monotonic() < poll_deadline, f"未到终态:{st['run']}"
                    await asyncio.sleep(0.2)
                assert st["run"]["status"] == "done"  # 断流没有取消 run
        finally:
            server.should_exit = True
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(serve_task, timeout=10)

    run(flow())


def _free_port_18xxx() -> int:
    """在 18000-18999 内找一个可绑定端口(绑定成功即释放,留给 uvicorn)。"""
    for port in range(18100, 19000, 7):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("18xxx 端口段无可用端口")


def test_stdio_entrypoint_smoke():
    """真 stdio 子进程:python -m insar_agent.mcp 能被宿主拉起并完成
    initialize + list_tools 握手(工具枚举不依赖后端,子进程用后即收)。"""
    import sys

    from mcp.client.stdio import StdioServerParameters, stdio_client

    async def flow():
        params = StdioServerParameters(
            command=sys.executable, args=["-X", "utf8", "-m", "insar_agent.mcp"])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                assert {t.name for t in listed.tools} == EXPECTED_TOOLS

    run(flow())


# ================ 自主循环工具 insar_converse(契约 §10)================
#
# 后端 POST /api/converse 由 B5 并行开发,driver.converse_loop 由 B1 并行开发:
# 本区用例对 MCP 自己的两层打桩验证 —— 工具层(MCP 会话真实 call_tool,HTTP 面
# 用注入 transport 伪造后端)与 backend 层(直接调 mcp.backend.converse)。
# 真后端联调用例(test_converse_over_real_app_when_endpoint_lands)在端点
# 落地前自动跳过,落地后无须改动即激活。

#: 契约 §1/§4 形状的多周期回合剧本:每周期一条 agent.cycle,工具执行走
#: tool.start/tool.end,中途告警 note 不是收尾,execute 只产生确认卡(ask),
#: say = 正常收束(parts 里的对象部件应被过滤)。
CONVERSE_SCRIPT = [
    {"t": "agent.cycle", "n": 1, "max": 4, "action": "search_data"},
    {"t": "tool.start", "id": "cv1", "name": "search_data", "label": "检索 ASF 归档"},
    {"t": "tool.end", "id": "cv1", "exit": 0, "summary": "命中 12 景 SLC"},
    {"t": "agent.cycle", "n": 2, "max": 4, "action": "check_env"},
    {"t": "tool.start", "id": "cv2", "name": "check_env", "label": "探测处理环境"},
    {"t": "tool.end", "id": "cv2", "exit": 0, "summary": "引擎 0/7 就绪(将走模拟)"},
    {"t": "note", "tone": "warn", "text": "云端步骤将跳过"},
    {"t": "agent.cycle", "n": 3, "max": 4, "action": "plan"},
    {"t": "agent.cycle", "n": 4, "max": 4, "action": "execute"},
    {"t": "ask", "prompt": "将执行 11 步计划(模拟模式),确认?", "fields": []},
    {"t": "say", "parts": ["数据与环境已核对:12 景 SLC,计划就绪,等待执行确认。",
                          {"kind": "card"}]},
]


def _ndjson_bytes(events: list[dict]) -> bytes:
    return "".join(
        json.dumps(e, ensure_ascii=False) + "\n" for e in events).encode("utf-8")


class _HangingStream(httpx.AsyncByteStream):
    """先吐出部分事件行,然后长眠不收尾 —— 模拟回合超出消费窗仍未结束。"""

    def __init__(self, lines: list[bytes], hang_s: float = 3600.0):
        self._lines = lines
        self._hang_s = hang_s

    async def __aiter__(self):
        for line in self._lines:
            yield line
        await asyncio.sleep(self._hang_s)   # deadline 必须在此打断

    async def aclose(self) -> None:
        return None


class _StreamTransport(httpx.AsyncBaseTransport):
    """返回自定义流式响应的 transport(MockTransport 只能给定长 body,
    验证 deadline 截断需要「可悬挂」的流)。"""

    def __init__(self, make_response):
        self._make_response = make_response

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return self._make_response(request)


@pytest.fixture()
def stub_transport():
    """把 MCP 后端层指到注入的 httpx transport(无真实后端,用后复位)。"""
    def install(transport: httpx.AsyncBaseTransport) -> None:
        mcp_backend.configure(base_url="http://insar-backend.test",
                              transport=transport)
    yield install
    mcp_backend.reset()


def test_converse_tool_registration_and_schema():
    """① 工具注册与 schema:参数名、必填集、max_cycles 边界(1..48)进 schema,
    读写注解正确(非只读、非破坏性)。"""
    async def flow():
        async with mcp_client() as sess:
            listed = await sess.list_tools()
            tool = next(t for t in listed.tools if t.name == "insar_converse")
            assert tool.description and "自主循环" in tool.description
            schema = tool.input_schema
            props = schema["properties"]
            assert {"session_id", "text", "max_cycles"} <= set(props)
            assert set(schema.get("required", [])) == {"session_id", "text"}
            assert props["max_cycles"].get("minimum") == 1
            assert props["max_cycles"].get("maximum") == 48
            assert props["max_cycles"].get("default") == 24
            assert tool.annotations.read_only_hint is False
            assert tool.annotations.destructive_hint is False
    run(flow())


def test_converse_multi_cycle_digest(stub_transport):
    """② 打桩流(工具层):契约形状的多周期事件 → 周期账/结论/工具摘要/确认卡;
    同时验证请求体形状({session, text, max_cycles})与路径 /api/converse。"""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path == "/api/converse"
        seen.append(json.loads(request.content))
        return httpx.Response(200, content=_ndjson_bytes(CONVERSE_SCRIPT),
                              headers={"content-type": "application/x-ndjson"})

    stub_transport(httpx.MockTransport(handler))

    async def flow():
        async with mcp_client() as sess:
            out = payload_of(await call(sess, "insar_converse", {
                "session_id": "s-loop", "text": "检查数据并准备计划",
                "max_cycles": 4}))
            assert seen == [{"session": "s-loop", "text": "检查数据并准备计划",
                             "max_cycles": 4}]
            assert out["session_id"] == "s-loop"
            assert out["cycles"] == [
                {"n": 1, "action": "search_data"}, {"n": 2, "action": "check_env"},
                {"n": 3, "action": "plan"}, {"n": 4, "action": "execute"}]
            assert out["ended_by"] == "say"
            assert out["reply"] == "数据与环境已核对:12 景 SLC,计划就绪,等待执行确认。"
            assert out["tool_summaries"] == ["命中 12 景 SLC", "引擎 0/7 就绪(将走模拟)"]
            assert out["problems"] == []          # 告警 note 不是坏消息
            assert out["questions"] == [
                {"prompt": "将执行 11 步计划(模拟模式),确认?", "fields": []}]
            assert out["truncated"] is False and out["stream_finished"] is True
            assert "questions" in out["next"]     # 确认卡要转述给用户
    run(flow())


def test_converse_note_wrapup(stub_transport):
    """②b 预算耗尽:回合以 note 收尾(无 say)→ ended_by=note,收尾语进 reply,
    指引提示调大 max_cycles / 拆小目标。"""
    script = [
        {"t": "agent.cycle", "n": 1, "max": 2, "action": "thinking"},
        {"t": "agent.cycle", "n": 2, "max": 2, "action": "status"},
        {"t": "note", "tone": "warn",
         "text": "周期预算耗尽:已完成状态盘点,计划未生成"},
    ]
    stub_transport(httpx.MockTransport(lambda request: httpx.Response(
        200, content=_ndjson_bytes(script))))

    async def flow():
        async with mcp_client() as sess:
            out = payload_of(await call(sess, "insar_converse",
                                        {"session_id": "s", "text": "推进"}))
            assert out["ended_by"] == "note"
            assert out["reply"] == "周期预算耗尽:已完成状态盘点,计划未生成"
            assert out["truncated"] is False       # 回合正常收尾,不是截断
            assert [c["action"] for c in out["cycles"]] == ["thinking", "status"]
            assert "max_cycles" in out["next"]
    run(flow())


@pytest.mark.timing  # 判定窗 = 消费 deadline(区分「事件已到」与「流悬挂」),乘 TIME_FACTOR
def test_converse_deadline_truncation(stub_transport, monkeypatch):
    """③ deadline 截断:流吐出 2 个周期后悬挂 → INSAR_MCP_CONVERSE_TIMEOUT 打断,
    如实标 truncated=true 且 next 给轮询指引(断开不取消回合)。"""
    monkeypatch.setenv("INSAR_MCP_CONVERSE_TIMEOUT", str(0.6 * TIME_FACTOR))
    lines = [json.dumps(e, ensure_ascii=False).encode("utf-8") + b"\n" for e in (
        {"t": "agent.cycle", "n": 1, "max": 6, "action": "search_data"},
        {"t": "agent.cycle", "n": 2, "max": 6, "action": "plan"},
    )]
    stub_transport(_StreamTransport(lambda request: httpx.Response(
        200, stream=_HangingStream(lines), request=request)))

    async def flow():
        async with mcp_client() as sess:
            out = payload_of(await call(sess, "insar_converse",
                                        {"session_id": "s", "text": "推进"}))
            assert out["truncated"] is True and out["stream_finished"] is False
            assert out["ended_by"] is None and out["reply"] == ""
            assert [c["action"] for c in out["cycles"]] == ["search_data", "plan"]
            assert "INSAR_MCP_CONVERSE_TIMEOUT" in out["next"]   # 可调上限
            assert "insar_run_status" in out["next"]             # 轮询指引
    run(flow())


@pytest.mark.timing  # 判定窗 = 消费 deadline,乘 TIME_FACTOR
def test_converse_truncation_ignores_midstream_note(stub_transport, monkeypatch):
    """③b 摘要不误判(P2-9):deadline 截断前恰好收到一条中途告警 note ——
    绝不能把它当收尾(此前 ended_by="note"、truncated=false,宿主被误导为
    「回合已正常收尾」而不再轮询)。截断流一律 ended_by=None + truncated=true。"""
    monkeypatch.setenv("INSAR_MCP_CONVERSE_TIMEOUT", str(0.6 * TIME_FACTOR))
    lines = [json.dumps(e, ensure_ascii=False).encode("utf-8") + b"\n" for e in (
        {"t": "agent.cycle", "n": 1, "max": 6, "action": "check_env"},
        {"t": "note", "tone": "warn", "text": "云端步骤将跳过"},  # 中途告警,不是收尾
    )]
    stub_transport(_StreamTransport(lambda request: httpx.Response(
        200, stream=_HangingStream(lines), request=request)))

    async def flow():
        async with mcp_client() as sess:
            out = payload_of(await call(sess, "insar_converse",
                                        {"session_id": "s", "text": "推进"}))
            assert out["ended_by"] is None      # 中途 note 不是终止事件
            assert out["truncated"] is True and out["stream_finished"] is False
            assert "insar_run_status" in out["next"]  # 仍给轮询指引,不装收尾
    run(flow())


class _SlowNdjsonServer:
    """真 socket 的 NDJSON 慢流服务:相邻事件行之间睡 gap_s 秒再续写(chunked)。

    验证 read timeout 语义必须走真实网络 —— 注入 transport 的假流不经 httpcore
    的超时机制,read timeout 永远不会触发,断言会空转。
    """

    def __init__(self, events: list[dict], gap_s: float):
        outer_lines = [json.dumps(e, ensure_ascii=False).encode("utf-8") + b"\n"
                       for e in events]

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # 静默:保持测试输出干净
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for i, line in enumerate(outer_lines):
                    if i:
                        time.sleep(gap_s)  # 事件间静默期(> http_timeout)
                    self.wfile.write(f"{len(line):X}\r\n".encode() + line + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def start(self):
        self._thread.start()

    def stop(self):
        self.httpd.shutdown()
        self._thread.join(timeout=5)
        self.httpd.server_close()


@pytest.mark.timing  # 判定窗:事件间静默期(2s×TF)> http_timeout(0.5s×TF),乘系数保持比例
def test_converse_survives_event_gap_longer_than_http_timeout(monkeypatch):
    """③c 读超时不误杀长回合(P1-3):自主循环周期间的 LLM 决策静默期可远超
    单次 HTTP 超时 —— consume 侧 per-request 把 read 超时放宽到消费 deadline,
    相邻事件间隔 > INSAR_MCP_HTTP_TIMEOUT 时流仍存活直至自然收尾。"""
    monkeypatch.setenv("INSAR_MCP_HTTP_TIMEOUT", str(0.5 * TIME_FACTOR))
    monkeypatch.setenv("INSAR_MCP_CONVERSE_TIMEOUT", str(30 * TIME_FACTOR))
    srv = _SlowNdjsonServer([
        {"t": "agent.cycle", "n": 1, "max": 6, "action": "thinking"},
        {"t": "say", "parts": ["静默期后照常收尾"]},
    ], gap_s=2.0 * TIME_FACTOR)
    srv.start()
    try:
        # 真网络(无注入 transport):read timeout 由 httpcore 真实执行
        mcp_backend.configure(base_url=srv.base_url)
        events, finished = run(mcp_backend.converse("s1", "推进", 6))
        assert finished is True                       # 修复前:ReadTimeout → BackendError
        assert [e["t"] for e in events] == ["agent.cycle", "say"]
    finally:
        srv.stop()
        mcp_backend.reset()


def test_converse_endpoint_missing_upgrade_guidance(stub_transport):
    """④ 旧后端(无 /api/converse 端点):404/405 不裸传 HTTP 状态,
    归一化为升级指引(版本线 + 分步替代方案)。"""
    for status in (404, 405):
        stub_transport(httpx.MockTransport(
            lambda request, status=status: httpx.Response(
                status, json={"detail": "Not Found"})))

        async def flow():
            async with mcp_client() as sess:
                res = await call(sess, "insar_converse",
                                 {"session_id": "s", "text": "推进"})
                msg = error_text(res)
                assert "版本过旧" in msg and "2026-08-14" in msg
                assert "insar_plan_run" in msg    # 升级前的分步替代指引
        run(flow())


def test_converse_backend_layer(stub_transport):
    """backend 层直测:mcp.backend.converse 的事件解析、结束标志与 404 归一化
    (BackendError.status 属性承载状态码)。"""
    def ok_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/converse"
        return httpx.Response(200, content=_ndjson_bytes(CONVERSE_SCRIPT))

    stub_transport(httpx.MockTransport(ok_handler))
    events, finished = run(mcp_backend.converse("s1", "推进", 6))
    assert finished is True and len(events) == len(CONVERSE_SCRIPT)
    assert events[0]["t"] == "agent.cycle" and events[-1]["t"] == "say"

    stub_transport(httpx.MockTransport(
        lambda request: httpx.Response(404, json={"detail": "Not Found"})))
    with pytest.raises(mcp_backend.BackendError) as ei:
        run(mcp_backend.converse("s1", "推进", 6))
    assert ei.value.status == 404 and "版本过旧" in str(ei.value)


def test_converse_max_cycles_bounds_rejected(monkeypatch):
    """⑤ max_cycles 越界(0/49/-1):MCP 层直接拒(schema ge/le),请求绝不
    触达后端(不会产生 HTTP 400);边界值 1/48 照常放行。"""
    calls: list[int] = []

    async def fake_converse(session_id: str, text: str, max_cycles: int):
        calls.append(max_cycles)
        return [{"t": "say", "parts": ["ok"]}], True

    monkeypatch.setattr(mcp_backend, "converse", fake_converse)

    async def flow():
        async with mcp_client() as sess:
            for bad in (0, 49, -1):
                res = await call(sess, "insar_converse", {
                    "session_id": "s", "text": "推进", "max_cycles": bad})
                assert res.is_error, f"max_cycles={bad} 应被 MCP 层拒绝"
            for good in (1, 48):
                out = payload_of(await call(sess, "insar_converse", {
                    "session_id": "s", "text": "推进", "max_cycles": good}))
                assert out["ended_by"] == "say"
    run(flow())
    assert calls == [1, 48], "越界值不应触达后端层"


def test_converse_over_real_app_when_endpoint_lands(backend_app, monkeypatch):
    """(前瞻联调)后端 POST /api/converse 落地(B5)后自动激活:
    driver.converse_loop 打桩为契约 §4 形状的假生成器,ASGITransport 直连
    真实 app,验证「MCP 工具 → 端点 → NDJSON → 摘要」全链。落地前跳过。"""
    if not any(getattr(r, "path", None) == "/api/converse" for r in backend_app.routes):
        pytest.skip("后端尚无 POST /api/converse(B5 并行开发中);落地后本用例自动激活")

    from insar_agent.loop.driver import Driver

    async def fake_converse_loop(self, session_id: str, text: str, *,
                                 max_cycles: int = 6):
        # 签名对齐 B1 落地形状(与 turn 同形,首参 session_id;契约 §4 的
        # converse_loop(text) 是省写)—— 端点按 (session, text=..., max_cycles=...)
        # 调用,省写签名会 TypeError(text 重复赋值)
        yield {"t": "agent.cycle", "n": 1, "max": max_cycles, "action": "check_env"}
        yield {"t": "tool.start", "id": "cv9", "name": "check_env", "label": "探测环境"}
        yield {"t": "tool.end", "id": "cv9", "exit": 0, "summary": "引擎 0/7 可用"}
        yield {"t": "agent.cycle", "n": 2, "max": max_cycles, "action": "plan"}
        yield {"t": "say", "parts": [f"目标「{text}」已推进:计划就绪"]}

    monkeypatch.setattr(Driver, "converse_loop", fake_converse_loop, raising=False)
    mcp_backend.configure(base_url="http://insar-backend.test",
                          transport=httpx.ASGITransport(app=backend_app))

    async def flow():
        async with mcp_client() as sess:
            sid = payload_of(await call(sess, "insar_create_session",
                                        {"name": "自主循环"}))["session"]["session_id"]
            out = payload_of(await call(sess, "insar_converse", {
                "session_id": sid, "text": "把计划准备好", "max_cycles": 5}))
            assert [c["action"] for c in out["cycles"]] == ["check_env", "plan"]
            assert out["ended_by"] == "say" and "计划就绪" in out["reply"]
            assert out["tool_summaries"] == ["引擎 0/7 可用"]
    run(flow())
