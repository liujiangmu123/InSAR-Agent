"""前后端事件契约对账 + 全流程 E2E(fastapi TestClient,不起真端口)。

契约基准(AGENT-DESIGN 前置结论):前端是基准,后端必须适配前端。
"前端需要的字段" 提取自 prototype/js:
  - 事件分发分支在 app.js 的 consume() 与 onGlobalEvent()(stream.js 只导出
    渲染器、无分发 switch,故注册表扫描 app.js 的 case 标签);
  - 每种事件的必需字段 = 对应渲染器(stream.js)与分支代码实际读取的字段。

四层校验:
  1. 工厂字段契约:loop/events.py 每个工厂产出的事件必须带前端读取的必需字段;
  2. 原始字面量纪律:src 里绕过工厂直发的事件({"t": ...} 字面量,如
     runtime/executor.py 的 step.stage)类型必须在 events.py 注册过;
  3. E2E 旅程:创建会话 → turn 规划 → pipeline 执行(simulated,秒级)→
     state/provenance/run.sh/trace/chat 与事件流交叉校验 → impact 预览 → fork 重跑;
     每行 NDJSON 必须 json.loads 成功且含类型字段 "t";
  4. 事件类型注册表:events.py 可产生的 type 集合与前端处理分支集合求差,
     差集必须与已知豁免清单完全一致(新增漂移会被拦截):
       后端有/前端无:step.stage(阶段推进,UI 静默忽略)、
                      handler_error(监听器错误隔离,当前后端也无调用点)、
                      agent.cycle(自主循环周期账 —— app.js 静默丢弃,由
                      agentloop.js 自建 SSE 订阅渲染,不走 consume 分发);
       前端有/后端无:tool.progress、budget(mock 演示专用,后端尚未产生 ——
                      按纪律不硬造,仅登记);
       过渡豁免:say.delta/say.abort(0814B 流式帧,W2 后端工厂与 W3 前端
                      分支并行落地 —— 见 STREAMING_LANDING 注释)。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import TIME_FACTOR
from insar_agent.api.app import create_app
from insar_agent.loop import events as ev

ROOT = Path(__file__).resolve().parents[1]
EVENTS_PY = ROOT / "src" / "insar_agent" / "loop" / "events.py"
APP_JS = ROOT / "prototype" / "js" / "app.js"
SRC_DIR = ROOT / "src" / "insar_agent"

# ---------------------------------------------------------------------------
# 前端契约注册表:事件类型 → 前端分支/渲染器实际读取的必需字段
# (可选字段不列入:前端对缺省有兜底,如 intervention.mode 缺省 'queue'、
#  gate_stop.stepId 缺省走通用标题、tool.end.artifacts 缺省 [])
# ---------------------------------------------------------------------------
FRONTEND_REQUIRED: dict[str, set[str]] = {
    "thinking": {"title", "body"},                    # Stream.thinking
    "say": {"parts"},                                 # Stream.agentMsg(renderPart)
    "say.delta": {"text"},                            # liveSay 追加(0814B §1.3,W3)
    "say.abort": {"reason"},                          # liveSay 标废(半截回复如实标注)
    "think.delta": {"text"},                          # liveThink 追加(循环等待思考)
    "think.end": set(),                               # 思考流收束,块保留可摺叠
    "ask": {"prompt", "fields"},                      # consume() ask 分支
    "plan": {"items"},                                # Stream.planPanel:items[{n,text,st}]
    "tool.start": {"id", "verb", "cmd", "label", "open"},
    "tool.log": {"id", "line", "tone"},
    "tool.progress": {"id", "pct"},                   # 后端尚不发(mock 专用)
    "tool.end": {"id", "exit", "summary", "artifacts"},
    "step.start": {"stepId"},
    "step.end": {"stepId", "exit"},
    "step.stage": {"stepId", "stage"},                # 前端暂不消费,登记形状防漂移
    "agent.cycle": {"n", "max", "action"},            # agentloop.js 进度条读取的字段
    "overall": {"pct"},
    "candidates": {"stepId"},
    "note": {"tone", "text"},
    "result": set(),
    "report": set(),
    "reattach": {"text"},                             # 服务端文本形态
    "intervention": {"text"},                         # mode/affected 可选
    "degrade": {"text", "evidenceBefore", "evidenceAfter"},
    "gate_stop": {"text", "suggestions"},             # stepId 可选
    "budget": {"diskFreeGB"},                         # 后端尚不发(mock 专用)
    "handler_error": {"source", "error"},             # 前端未消费
}

# 注册表差集豁免清单(变更须同步改这里与文件头说明)
BACKEND_ONLY = {"step.stage", "handler_error", "agent.cycle"}
FRONTEND_ONLY = {"tool.progress", "budget"}
# 流式帧(0814B §1.3):后端工厂(W2)与前端分支(W3)并行落地,两态都合法 ——
# W3 未合入时按「后端有/前端无」临时豁免;合入后进入双侧交集,自动退出差集。
# 集成完成后本清单应为空集(验证波次可收紧回精确断言)。
STREAMING_LANDING = {"say.delta", "say.abort", "think.delta", "think.end"}


# ---------------------------------------------------------------------------
# 夹具:密封环境(probe 空引擎、LLM 路由清空 → 规则分类 + registry 推荐、
# 允许 simulated 引擎)—— 全流程秒级,零重型计算
# ---------------------------------------------------------------------------
@pytest.fixture()
def client(tmp_path, monkeypatch):
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
    app = create_app(home=tmp_path / "home")
    with TestClient(app) as c:
        yield c


def _stream(client: TestClient, url: str, body: dict) -> list[dict]:
    """消费 NDJSON 流。纪律:每行必须 json.loads 成功且含类型字段 "t"。"""
    events: list[dict] = []
    with client.stream("POST", url, json=body) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/x-ndjson")
        for line in resp.iter_lines():
            if not line.strip():
                continue
            event = json.loads(line)  # 非法 JSON 行直接抛错失败
            assert isinstance(event, dict) and "t" in event, f"事件缺类型字段: {line!r}"
            events.append(event)
    return events


def _assert_frontend_fields(events: list[dict]) -> None:
    """每个事件都必须带前端对应分支读取的必需字段;未登记的类型直接失败。"""
    for event in events:
        required = FRONTEND_REQUIRED.get(event["t"])
        assert required is not None, f"后端发出了未登记的事件类型: {event['t']}(前端不会渲染)"
        missing = required - set(event)
        assert not missing, f"{event['t']} 缺前端必需字段 {missing}: {event}"


def _backend_types() -> set[str]:
    """loop/events.py 能产生的全部 type 值(工厂字面量扫描)。"""
    return set(re.findall(r'"t":\s*"([^"]+)"', EVENTS_PY.read_text(encoding="utf-8")))


def _frontend_types() -> set[str]:
    """前端事件分发分支(app.js consume() + onGlobalEvent() 的 case 标签)。"""
    return set(re.findall(r"case '([^']+)':", APP_JS.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# 1. 工厂字段契约:逐工厂调用,断言产出满足前端必需字段
# ---------------------------------------------------------------------------
def test_factories_satisfy_frontend_required_fields():
    samples = [
        ev.thinking("标题", "正文"),
        ev.say(["你好", {"code": "x"}]),
        ev.say_delta("正在生成的增量"),
        ev.say_abort("truncated"),
        ev.think_delta("先检查环境再盘点数据"),
        ev.think_end(),
        ev.ask("请补充:", [{"key": "scenario", "label": "场景", "options": ["quake"]}]),
        ev.plan([{"n": 1, "text": "探测", "st": "d"}]),
        ev.tool_start("s6", "[06/11]", "snaphu --method mcf", "解缠"),
        ev.tool_log("s6", "unwrapping...", "dim"),
        ev.tool_end("s6", 0, "完成", [{"path": "a.h5", "hash": "ab12"}]),
        ev.step_start(6),
        ev.step_end(6, 0),
        ev.step_stage(6, "PREPARED"),
        ev.agent_cycle(1, 6, "search_data"),
        ev.overall(50),
        ev.candidates(6),
        ev.note("warn", "提示"),
        ev.result(),
        ev.report(),
        ev.reattach("已接回第 7 步"),
        ev.intervention("改第 9 步方法", [9, 10], mode="steer"),
        ev.degrade("ERA5 不可用,已降级", "validated", "checked"),
        ev.gate_stop("覆盖率不足", ["换方法"], step_id=6),
        ev.handler_error("trace", "boom"),
    ]
    seen = set()
    for event in samples:
        required = FRONTEND_REQUIRED[event["t"]]
        missing = required - set(event)
        assert not missing, f"{event['t']} 工厂缺前端必需字段 {missing}: {event}"
        seen.add(event["t"])
    # 每个工厂都被覆盖(events.py 新增工厂时此处必须跟上)
    assert seen == _backend_types(), "样本未覆盖全部工厂类型"

    # 字段级补充断言:本次对账修复的两个字段
    assert ev.intervention("x")["mode"] == "queue"        # 前端缺省语义一致
    assert ev.gate_stop("x")["stepId"] is None            # 缺省走通用标题


# ---------------------------------------------------------------------------
# 2. 原始字面量纪律:绕过工厂直发的事件类型必须已在 events.py 注册
# ---------------------------------------------------------------------------
def test_raw_emitted_event_types_are_registered():
    registered = _backend_types()
    offenders: dict[str, set[str]] = {}
    for py in SRC_DIR.rglob("*.py"):
        if py.name == "events.py":
            continue
        raw = set(re.findall(r'\{"t":\s*"([^"]+)"', py.read_text(encoding="utf-8")))
        unknown = raw - registered
        if unknown:
            offenders[str(py.relative_to(ROOT))] = unknown
    assert not offenders, f"存在未注册的直发事件类型: {offenders}"


# ---------------------------------------------------------------------------
# 3. E2E 旅程:模拟 UI 客户端从建会话到 fork 重跑的完整链路
# ---------------------------------------------------------------------------
def test_e2e_ui_journey(client):
    # ---- 创建会话 ----
    r = client.post("/api/sessions", json={"id": "e2e", "name": "契约对账", "mode": "expert"})
    assert r.status_code == 200 and r.json()["session_id"] == "e2e"

    # ---- 规划回合(中文请求 → 规则分类命中 quake 场景) ----
    turn_events = _stream(client, "/api/turn",
                          {"session": "e2e", "text": "分析 Ridgecrest 2019 地震同震形变"})
    _assert_frontend_fields(turn_events)
    kinds = [e["t"] for e in turn_events]

    # 序列骨架:thinking → 探测工具(start→log→end)→ plan → say → candidates
    for t in ("thinking", "tool.start", "tool.log", "tool.end", "plan", "say", "candidates"):
        assert t in kinds, f"规划回合缺 {t} 事件: {kinds}"
    assert (kinds.index("thinking") < kinds.index("tool.start")
            < kinds.index("tool.end") < kinds.index("plan")
            < kinds.index("say") < kinds.index("candidates"))

    # plan 条目形状(planPanel 读 n/text/st)
    plan_items = next(e for e in turn_events if e["t"] == "plan")["items"]
    assert plan_items and all({"n", "text", "st"} <= set(it) for it in plan_items)
    assert all(it["st"] in ("p", "r", "d", "f", "s") for it in plan_items)
    # candidates 决策点(candidateSet 读 stepId)
    assert isinstance(next(e for e in turn_events if e["t"] == "candidates")["stepId"], int)
    # 引擎全缺 → 必须显式给「模拟执行」横幅(诚实性)
    assert any(e["t"] == "note" and "模拟" in e["text"] for e in turn_events)

    # ---- 批准执行(UI 审批卡「确认执行」→ POST /api/pipeline,simulated 秒级) ----
    run_events = _stream(client, "/api/pipeline", {"session": "e2e"})
    _assert_frontend_fields(run_events)
    kinds2 = [e["t"] for e in run_events]

    started = [e["stepId"] for e in run_events if e["t"] == "step.start"]
    ended = {e["stepId"]: e["exit"] for e in run_events if e["t"] == "step.end"}
    assert started and started == sorted(started), "step.start 应按拓扑序出现"
    assert set(started) == set(ended) and all(x == 0 for x in ended.values())

    # 每个执行步骤:step.start → tool.start(id=sN) → tool.end → step.end 成对闭合
    tool_ends = {e["id"]: e for e in run_events if e["t"] == "tool.end"}
    for sid in started:
        te = tool_ends.get(f"s{sid}")
        assert te is not None, f"第 {sid} 步缺 tool.end"
        assert te["exit"] == 0 and isinstance(te["artifacts"], list)
        assert all({"path", "hash"} <= set(a) for a in te["artifacts"])

    # 双通道承诺(P1 已修):执行器细节事件经 driver 队列泵入回合流,
    # 每个执行步骤的阶段推进(step.stage)必须出现在 NDJSON 里。
    assert [e for e in run_events if e["t"] == "step.stage"], \
        "执行期 step.stage 应进入回合 NDJSON 流(driver 泵接线)"

    # 进度单调递增至 100
    pcts = [e["pct"] for e in run_events if e["t"] == "overall"]
    assert pcts == sorted(pcts) and pcts[-1] == 100

    # 指标重解析必须通过(simulated 引擎产出与账本一致,不应出告警 note)
    assert not any(e["t"] == "note" and "指标重解析" in e.get("text", "") for e in run_events)

    # 终态:全部 step.end 之后 result → note(账本导出)→ report
    last_step_end = max(i for i, t in enumerate(kinds2) if t == "step.end")
    assert last_step_end < kinds2.index("result") < kinds2.index("report")
    assert any(e["t"] == "note" and "provenance" in e.get("text", "") for e in run_events)

    # ---- 状态镜像与事件一致(fetchState → syncServerSteps 契约) ----
    state = client.get("/api/state", params={"session": "e2e"}).json()
    assert state["run"]["status"] == "done"
    for s in state["steps"]:
        assert {"id", "name", "method", "params", "state", "stale",
                "fingerprint", "stage"} <= set(s)
    by_id = {s["id"]: s for s in state["steps"]}
    for sid in started:
        assert by_id[sid]["state"] == "done", f"事件里第 {sid} 步跑完,状态镜像应为 done"
    # 没有 step.start 的步骤只能是云端跳过(skipped),二者互补覆盖全链
    skipped = {s["id"] for s in state["steps"] if s["state"] == "skipped"}
    assert skipped == set(by_id) - set(started)

    # ---- 产物 / 账本 / 等价脚本 / 轨迹 / 历史对话与事件一致 ----
    prov = client.get("/api/provenance", params={"session": "e2e"}).json()
    assert prov["schema_version"] == "1.0" and prov["simulated"] is True
    assert isinstance(prov["metrics"], dict)
    prov_paths = {a["path"] for a in prov["artifacts"].values()}
    event_paths = {a["path"] for te in tool_ends.values() for a in te["artifacts"]}
    assert event_paths <= prov_paths, "tool.end 播报的产物必须都在账本里"
    for sid in started:
        assert prov["steps"][str(sid)]["state"] == "done"

    sh = client.get("/api/run.sh", params={"session": "e2e"}).text
    assert "set -euo pipefail" in sh

    trace = client.get("/api/trace", params={"session": "e2e"}).json()
    assert set(started) <= {row["step_no"] for row in trace if row["step_no"] is not None}
    for row in trace:  # 轨迹面板(dock.js traceLiveView)读取的列
        assert {"step_no", "phase", "action", "error_occurred",
                "error_type", "error_message", "revision_trigger",
                "thought", "observation"} <= set(row)

    chat = client.get("/api/chat", params={"session": "e2e"}).json()
    roles = {m["role"] for m in chat}
    assert {"user", "agent"} <= roles  # hydrateFromServer 按 role 重放
    assert all({"role", "content", "created_at"} <= set(m) for m in chat)
    assert any("Ridgecrest" in m["content"] for m in chat if m["role"] == "user")

    # ---- fork 改参数:影响预览(审批卡权威数据源)→ fork → 重跑摘要 ----
    run_id = state["run"]["run_id"]
    imp = client.get("/api/impact", params={
        "session": "e2e", "step": 9, "method": "exponential"}).json()
    # refineApprovalCard 读取的键
    assert {"changedStep", "reason", "affected", "rerunMinutes", "rerunBasis"} <= set(imp)
    assert imp["changedStep"] == 9 and imp["reason"] == "method_changed"
    assert all({"step_id", "reason", "state_before"} <= set(a) for a in imp["affected"])
    affected_ids = [a["step_id"] for a in imp["affected"]]
    assert affected_ids == [9, 10, 11]
    assert imp["rerunMinutes"] is None  # 历史样本不足 → 如实未知,不编数(§7.5)

    fork = client.post("/api/fork", json={
        "session": "e2e", "run_id": run_id,
        "changes": {"9": {"method": "exponential"}}}).json()
    assert fork["runId"] != run_id
    fork_states = {s["id"]: s["state"] for s in fork["steps"]}
    assert fork_states[9] == fork_states[10] == fork_states[11] == "pending"
    for sid in started:
        if sid < 9:
            assert fork_states[sid] == "done"  # 零重算复用

    # 重跑:只执行受影响子集,且与 impact 预览完全一致
    rerun_events = _stream(client, "/api/pipeline",
                           {"session": "e2e", "run_id": fork["runId"]})
    _assert_frontend_fields(rerun_events)
    rerun_started = [e["stepId"] for e in rerun_events if e["t"] == "step.start"]
    assert rerun_started == affected_ids, "重跑子集必须与影响预览一致"
    assert any(e["t"] == "result" for e in rerun_events)
    assert [e["pct"] for e in rerun_events if e["t"] == "overall"][-1] == 100

    fork_state = client.get("/api/state",
                            params={"session": "e2e", "run_id": fork["runId"]}).json()
    assert fork_state["run"]["status"] == "done"
    assert {s["id"]: s["method"] for s in fork_state["steps"]}[9] == "exponential"


# ---------------------------------------------------------------------------
# 4. 执行器细节事件的通道路由(P1 已修,双通道断言):
#    step.stage / 执行期 tool.log 由 runtime/executor.py 经 driver 的 pump 队列
#    同时:发布到 EventBus(全局 SSE)+ 泵入回合 NDJSON 流(工具卡滚动日志
#    由回合流承载;SSE 侧 onGlobalEvent 只认带外条目、busy 时跳过,不承担此职)。
#    本测试双边固定:总线形状正确 + 回合流确实收到细节事件。
# ---------------------------------------------------------------------------
@pytest.mark.timing  # 执行回合驱动真实 simulated 子进程(startup_grace 判定窗)
def test_executor_detail_events_bus_routing(store, workspace, monkeypatch):
    import asyncio

    from insar_agent.brain.facade import Brain
    from insar_agent.loop.driver import Driver
    from insar_agent.runtime.probe import ProbeResult

    probe = ProbeResult(
        engines={k: None for k in ("isce2", "mintpy", "snaphu", "gdal", "snap",
                                   "pystamps", "pyaps")},
        credentials={"earthdata": False, "cds": False, "gacos": False},
        disk_free_gb=100.0, cpu_count=8)
    driver = Driver(store, workspace=workspace, probe=probe, poll=0.05,
                    startup_grace=15.0 * TIME_FACTOR, allow_simulated=True,
                    brain=Brain(None))

    async def run() -> tuple[list[dict], list[dict], list[dict]]:
        turn_ev = [e async for e in driver.turn("s1", "Ridgecrest 地震同震形变")]
        q = driver.bus.subscribe()
        exec_ev = [e async for e in driver.execute("s1")]
        driver.bus.unsubscribe(q)
        bus_ev = []
        while not q.empty():
            bus_ev.append(q.get_nowait())
        return turn_ev, exec_ev, bus_ev

    _, exec_events, bus_events = asyncio.run(run())

    # 回合流:执行器细节事件已泵入(双通道承诺对执行期成立)
    assert [e for e in exec_events if e["t"] == "step.stage"], \
        "回合流应包含执行期 step.stage"

    # 总线:driver yield 的每个事件都同步发布(双通道承诺),外加执行器细节事件
    def _key(e: dict) -> str:
        return json.dumps(e, sort_keys=True, ensure_ascii=False)

    bus_keys = {_key(e) for e in bus_events}
    assert all(_key(e) in bus_keys for e in exec_events)

    stages = [e for e in bus_events if e["t"] == "step.stage"]
    assert stages, "执行器应向总线发布 step.stage 阶段推进事件"
    started = {e["stepId"] for e in exec_events if e["t"] == "step.start"}
    for e in stages:  # 直发字面量与 events.py 工厂形状一致
        assert set(e) == set(ev.step_stage(1, "PREPARED"))
        assert e["stage"] in ("PREPARED", "LAUNCHED", "COLLECTED", "VERIFIED")
        assert e["stepId"] in started
    # 每个执行步骤至少推进到 VERIFIED
    verified = {e["stepId"] for e in stages if e["stage"] == "VERIFIED"}
    assert verified == started

    # 执行期日志行(follow_job → tool.log)同样只在总线上
    bus_logs = [e for e in bus_events
                if e["t"] == "tool.log" and e["id"].startswith("s")]
    assert bus_logs, "执行期 tool.log 应出现在总线"
    assert all({"id", "line", "tone"} <= set(e) for e in bus_logs)
    stream_logs = [e for e in exec_events
                   if e["t"] == "tool.log" and e["id"].startswith("s")]
    assert stream_logs  # 回合流能收到执行期日志(P1 已修:工具卡滚动日志有数据源)


# ---------------------------------------------------------------------------
# 5. ask 事件:意图无法识别时转表单(此前前端无该分支 → 界面空白,已修)
# ---------------------------------------------------------------------------
def test_turn_unrecognized_intent_yields_ask(client):
    client.post("/api/sessions", json={"id": "ask-e2e"})
    events = _stream(client, "/api/turn", {"session": "ask-e2e", "text": "随便帮我搞一下"})
    assert events and events[-1]["t"] == "ask"
    ask = events[-1]
    assert ask["prompt"]
    assert isinstance(ask["fields"], list) and ask["fields"]
    # 前端 ask 分支渲染读 label(与可选 options)
    assert all("label" in f for f in ask["fields"])
    # app.js 必须有 ask 处理分支(回归守护:缺分支时回合流一片空白)
    assert "ask" in _frontend_types()


# ---------------------------------------------------------------------------
# 6. 事件类型注册表:后端可产生集合 × 前端处理分支集合,差集必须与豁免清单一致
# ---------------------------------------------------------------------------
def test_event_type_registry_diff_is_exactly_documented():
    backend = _backend_types()
    frontend = _frontend_types()
    # 双向差集必须与文件头登记的豁免清单完全一致 —— 任何一侧新增类型而
    # 另一侧未跟上(或未登记豁免)都会在此失败。流式帧按 STREAMING_LANDING
    # 过渡豁免:W3 的 app.js 分支未合入/已合入两态都精确合法,不放过其他漂移。
    diff = backend - frontend
    assert diff in (BACKEND_ONLY, BACKEND_ONLY | STREAMING_LANDING), (
        f"后端有/前端无 漂移: {diff} != {BACKEND_ONLY}"
        f"(流式帧过渡豁免仅限 {STREAMING_LANDING})")
    assert frontend - backend == FRONTEND_ONLY, (
        f"前端有/后端无 漂移: {frontend - backend} != {FRONTEND_ONLY}")
    # 契约注册表本身必须覆盖两侧全部类型
    assert backend | frontend == set(FRONTEND_REQUIRED), (
        f"FRONTEND_REQUIRED 注册表与实际类型集不一致: "
        f"{(backend | frontend) ^ set(FRONTEND_REQUIRED)}")
