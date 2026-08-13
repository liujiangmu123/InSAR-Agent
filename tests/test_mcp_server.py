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
import socket
import time
from contextlib import asynccontextmanager
from functools import partial

import pytest

pytest.importorskip("mcp", reason="需要可选依赖组 [mcp]:pip install -e .[mcp]")

import anyio
import httpx
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from insar_agent.api.app import create_app
from insar_agent.mcp import backend as mcp_backend
from insar_agent.mcp import server as mcp_server_module
from insar_agent.mcp.server import mcp as insar_mcp

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

EXPECTED_TOOLS = {
    "insar_list_sessions", "insar_create_session", "insar_plan_run",
    "insar_execute_run", "insar_run_status", "insar_intervene",
    "insar_get_provenance", "insar_list_figures",
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
    """工具枚举:8 个 insar_ 前缀工具,描述非空,读写注解正确。"""
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
