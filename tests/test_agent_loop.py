"""自主循环回合(driver.converse_loop,LOOP-CONTRACT §4/§1)验收。

全部密封:假 Brain(带 cycle 职责的契约形状替身,B3 并行在建)、空 probe、
monkeypatch INSAR_HOME,零网络零重型计算。覆盖:

- 降级闸门:Brain(None)/缺 cycle 职责 → 逐事件转发既有 turn(零回归);
- 事件契约:agent.cycle 每周期恰好一条(n/max/action),双通道(回合流+总线);
- 终止闭集:say 正常收束 | max_cycles 耗尽 note | 取消 note | BrainUnavailable
  note | 同签名连续 3 次提案熔断 | 同签名连续 2 次失败(unresolved_failure);
- 动作特例:execute 只发确认卡(ask)绝不自启流水线;plan 进既有规划流程;
  search_data 对 net(B4)/subtasks(B8)防御性降级;inspect_file 闭集匹配零路径;
- steering(USER_MESSAGE)并入 goal、周期摘要 ≤300 字预算、路由钉死、
  usage_context 会话绑定。
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from insar_agent.brain.facade import Brain
from insar_agent.brain.provider import BrainUnavailable
from insar_agent.loop import events as ev
from insar_agent.loop.budget import clip_summary
from insar_agent.loop.driver import Driver
from insar_agent.runtime.probe import ProbeResult


def empty_probe():
    return ProbeResult(engines={"isce2": None, "mintpy": None, "snaphu": None, "gdal": None,
                                "snap": None, "pystamps": None, "pyaps": None},
                       credentials={"earthdata": False, "cds": False, "gacos": False},
                       disk_free_gb=100.0, cpu_count=8)


def make_driver(store, workspace, **kw) -> Driver:
    defaults = dict(workspace=workspace, probe=empty_probe(), poll=0.05,
                    allow_simulated=True, brain=Brain(None))
    defaults.update(kw)
    return Driver(store, **defaults)


def collect(agen) -> list[dict]:
    async def _run():
        return [e async for e in agen]

    return asyncio.run(_run())


@pytest.fixture(autouse=True)
def _sealed_env(tmp_path, monkeypatch):
    """密封:HOME 指向空目录,清掉数据源/数据目录环境变量(状态摘要会读)。"""
    monkeypatch.setenv("INSAR_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("INSAR_DATA_DIR", raising=False)
    monkeypatch.delenv("INSAR_HYP3_SOURCE", raising=False)


# ---------------- 契约形状替身(B3 并行在建,按 LOOP-CONTRACT §3 打桩) ----------------

def cy(action=None, say=None, done=False, route_index=None):
    """CycleResult 契约形状(action/say/done/source;route_index 是可选扩展)。"""
    return types.SimpleNamespace(action=action, say=say, done=done, source="llm",
                                 route_index=route_index)


class FakeCycleBrain:
    """带 cycle 职责的假 Brain(不走网络)。脚本项:CycleResult 形状 | 异常。

    cycle 之外的职责只显式补 select:plan 动作会进 _plan_turn 的决策点叙述,
    那里要 brain.select —— 委托给禁用态 Brain(None) 走规则降级(recommend),
    与 test_converse.py 的「非目标职责一律降级」打桩哲学同款。
    """

    enabled = True

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def cycle(self, *, goal, cycles_summary, state_summary, registry, route_pin=None):
        self.calls.append({"goal": goal, "cycles_summary": list(cycles_summary),
                           "state_summary": state_summary, "route_pin": route_pin})
        if not self.script:
            return cy(say="(脚本耗尽,收束)", done=True)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def select(self, cap, feasible, **kwargs):
        return Brain(None).select(cap, feasible, **kwargs)


class EnabledBrainWithoutCycle:
    """enabled 为真但没有 cycle 职责的替身(B3 未上线的过渡形态)。"""

    enabled = True

    def converse(self, *args, **kwargs):
        raise BrainUnavailable("stub:无 converse 剧本")

    def intent(self, text):
        return Brain(None).intent(text)


def seed_ready_run(store, workspace, session_id="s1"):
    """用规则路径(Brain(None) turn)种一个 ready 状态的 run(秒级,无执行)。"""
    d = make_driver(store, workspace)
    collect(d.turn(session_id, "Ridgecrest 地震同震形变分析"))
    run = store.latest_run(session_id)
    assert run is not None and run["status"] == "ready"
    return run


# ---------------- 事件工厂与摘要预算 ----------------

def test_agent_cycle_factory_shape():
    event = ev.agent_cycle(2, 6, "search_data")
    assert event == {"t": "agent.cycle", "n": 2, "max": 6, "action": "search_data"}


def test_clip_summary_budget():
    long = "长语句 " * 300
    clipped = clip_summary(long)
    assert len(clipped) == 300 and clipped.endswith("…")
    # 压平空白:多行日志变单行;短文本原样保留
    assert clip_summary("a\n b\t\tc") == "a b c"
    assert clip_summary("短", max_chars=10) == "短"


# ---------------- 降级闸门:零回归守护 ----------------

def test_brain_none_delegates_to_turn_byte_identical(workspace, tmp_path):
    """Brain(None) → converse_loop 逐事件转发 turn:两条路径事件逐字节一致。"""
    from insar_agent.core.db import Database
    from insar_agent.core.store import Store

    for text in ("随便帮我搞一下", "Ridgecrest 地震同震形变分析"):
        store_a, store_b = Store(Database(":memory:")), Store(Database(":memory:"))
        ws_a, ws_b = tmp_path / f"wa-{len(text)}", tmp_path / f"wb-{len(text)}"
        events_turn = collect(make_driver(store_a, ws_a).turn("s1", text))
        events_loop = collect(make_driver(store_b, ws_b).converse_loop("s1", text))
        assert events_loop == events_turn
        assert not any(e["t"] == "agent.cycle" for e in events_loop)
        # 聊天留痕由 turn 负责,转发路径绝不双写
        roles_a = [m["role"] for m in store_a.chat_history("s1")]
        roles_b = [m["role"] for m in store_b.chat_history("s1")]
        assert roles_b == roles_a


def test_enabled_brain_without_cycle_delegates(store, workspace):
    """brain.enabled 为真但缺 cycle 属性 → 同样转发 turn(B3 未就绪的过渡态)。"""
    driver = make_driver(store, workspace, brain=EnabledBrainWithoutCycle())
    events = collect(driver.converse_loop("s1", "随便帮我搞一下"))
    kinds = [e["t"] for e in events]
    assert kinds == ["note", "ask"]  # turn 的诚实降级路径:note + 表单
    assert "LLM 暂不可用" in events[0]["text"]
    assert not any(e["t"] == "agent.cycle" for e in events)


# ---------------- 正常终止(say)与周期账 ----------------

def test_say_terminates_after_action_cycles(store, workspace):
    brain = FakeCycleBrain([
        cy(action={"type": "status"}, say="先看下状态。"),
        cy(say="当前会话还没有 run,建议先说清楚场景需求。", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain)
    events = collect(driver.converse_loop("s1", "现在什么进度?"))
    kinds = [e["t"] for e in events]
    # 周期账恰好一条(say 收束不占周期);工具卡 id 配对;say 是最后一个事件
    cycles = [e for e in events if e["t"] == "agent.cycle"]
    assert [(c["n"], c["max"], c["action"]) for c in cycles] == [(1, 6, "status")]
    assert kinds[-1] == "say"
    assert events[-1]["parts"] == ["当前会话还没有 run,建议先说清楚场景需求。"]
    starts = [e for e in events if e["t"] == "tool.start"]
    ends = {e["id"]: e for e in events if e["t"] == "tool.end"}
    assert [t["id"] for t in starts] == ["loop1"] and ends["loop1"]["exit"] == 0
    # 周期摘要回灌:第二周期收到第一周期的压缩结果
    assert brain.calls[1]["cycles_summary"] == [f"[1] status:{ends['loop1']['summary']}"]
    assert "还没有 run" in brain.calls[1]["cycles_summary"][0]
    # 状态摘要与目标逐周期注入
    assert brain.calls[0]["state_summary"].startswith("环境:")
    assert brain.calls[0]["goal"] == "现在什么进度?"
    # 聊天留痕:user + agent(终止 say)
    assert [m["role"] for m in store.chat_history("s1")] == ["user", "agent"]


def test_immediate_say_emits_no_cycle_event(store, workspace):
    brain = FakeCycleBrain([cy(say="你好!需要处理什么数据?", done=True)])
    driver = make_driver(store, workspace, brain=brain)
    events = collect(driver.converse_loop("s1", "你好"))
    assert [e["t"] for e in events] == ["say"]  # 纯聊天:无周期账,无 note


def test_events_published_to_bus_dual_channel(store, workspace):
    import json as _json

    brain = FakeCycleBrain([
        cy(action={"type": "check_env"}, say=""),
        cy(say="环境看完了。", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain)
    q = driver.bus.subscribe()
    events = collect(driver.converse_loop("s1", "环境如何"))
    driver.bus.unsubscribe(q)
    bus_events = []
    while not q.empty():
        bus_events.append(q.get_nowait())

    def key(e: dict) -> str:
        return _json.dumps(e, sort_keys=True, ensure_ascii=False)

    bus_keys = {key(e) for e in bus_events}
    assert all(key(e) in bus_keys for e in events)  # 回合流事件全部同步上总线


# ---------------- 终止闭集:预算耗尽 / 熔断 / 取消 / LLM 失败 ----------------

def test_max_cycles_exhausted_closes_with_note(store, workspace):
    brain = FakeCycleBrain([
        cy(action={"type": "status"}, say=""),
        cy(action={"type": "check_env"}, say=""),
        cy(action={"type": "list_data"}, say=""),
    ])
    driver = make_driver(store, workspace, brain=brain)
    events = collect(driver.converse_loop("s1", "把能查的都查一遍", max_cycles=3))
    cycles = [(e["n"], e["action"]) for e in events if e["t"] == "agent.cycle"]
    assert cycles == [(1, "status"), (2, "check_env"), (3, "list_data")]
    assert events[-1]["t"] == "note" and "周期上限" in events[-1]["text"]
    assert not any(e["t"] == "say" for e in events)  # 耗尽走 note,不伪装正常终止


def test_repeat_signature_breaker_trips_on_third_proposal(store, workspace):
    same = {"type": "status"}
    brain = FakeCycleBrain([cy(action=dict(same), say="") for _ in range(3)])
    driver = make_driver(store, workspace, brain=brain)
    events = collect(driver.converse_loop("s1", "反复看状态"))
    cycles = [e for e in events if e["t"] == "agent.cycle"]
    assert len(cycles) == 3  # 第 3 次提案仍记周期账(每周期一条)
    # 但第 3 次不再执行:工具卡只有前两个周期的
    assert [e["id"] for e in events if e["t"] == "tool.start"] == ["loop1", "loop2"]
    assert events[-1]["t"] == "note" and "熔断" in events[-1]["text"]


def test_unresolved_failure_breaker_after_two_same_failures(store, workspace):
    # 无 run 的 set_params 必然失败;同签名连续失败 2 次 → 如实声明 unresolved_failure
    bad = {"type": "set_params", "step": 6, "params": {"min_coherence": 0.3}}
    brain = FakeCycleBrain([
        cy(action=dict(bad), say=""),
        cy(action=dict(bad), say=""),
        cy(say="不该到这里", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain)
    events = collect(driver.converse_loop("s1", "改第 6 步参数"))
    assert len([e for e in events if e["t"] == "agent.cycle"]) == 2
    ends = [e for e in events if e["t"] == "tool.end"]
    assert [e["exit"] for e in ends] == [1, 1]
    assert events[-1]["t"] == "note" and "unresolved_failure" in events[-1]["text"]
    assert not any(e["t"] == "say" for e in events)
    assert brain.script  # 第三个脚本项没被消费:回合确实提前收束


def test_cancel_at_cycle_boundary(store, workspace):
    run = seed_ready_run(store, workspace)
    store.request_cancel(run["run_id"])  # 持久化 control 位(/api/abort 同款)
    brain = FakeCycleBrain([cy(action={"type": "status"}, say="")])
    driver = make_driver(store, workspace, brain=brain)
    events = collect(driver.converse_loop("s1", "继续干活"))
    assert [e["t"] for e in events] == ["note"]
    assert "已取消" in events[0]["text"]
    assert brain.calls == []  # 边界检查先于 LLM 调用
    # run 未在执行:循环是取消意图的唯一在场消费者,兑现后复位 control 位,
    # 不让遗留意图误拦用户下一次显式执行
    assert store.get_run(run["run_id"])["control"] == "running"


def test_cancel_of_queued_run_not_consumed_by_loop(store, workspace):
    """排队中 run 的取消意图归未来的 execute 消费者:循环收束但只读不清(P1-4)。

    否则用户取消过的重计算在调度器出队时照样开跑(重型计算管控红线)——
    control 位必须留给 execute 入口消费(收尾为 interrupted)。
    """
    from insar_agent.loop.queue import RunQueue

    run = seed_ready_run(store, workspace)
    position, created = RunQueue(store).enqueue(run["run_id"], "s1")
    assert created and position == 1
    store.request_cancel(run["run_id"])
    brain = FakeCycleBrain([cy(action={"type": "status"}, say="")])
    driver = make_driver(store, workspace, brain=brain)
    events = collect(driver.converse_loop("s1", "继续干活"))
    assert [e["t"] for e in events] == ["note"] and "已取消" in events[0]["text"]
    assert brain.calls == []
    # 与 test_cancel_at_cycle_boundary 的复位对照:队列在场时只读不清
    assert store.get_run(run["run_id"])["control"] == "cancel_requested"


def test_stale_session_cancel_cleared_at_turn_entry(store, workspace):
    """陈旧会话级取消意图在回合入口作废(P2-8):不误杀下一个回合。"""
    brain = FakeCycleBrain([
        cy(action={"type": "status"}, say=""),
        cy(say="看完了。", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain)
    driver.request_session_cancel("s1")  # 上一回合收尾后才置位的遗留意图
    events = collect(driver.converse_loop("s1", "查查状态"))
    assert not any(e["t"] == "note" and "取消" in e.get("text", "") for e in events)
    assert events[-1]["t"] == "say"
    assert len(brain.calls) == 2  # 两个周期都跑到:回合未被陈旧意图误杀


def test_brain_unavailable_mid_loop_closes_with_note(store, workspace):
    brain = FakeCycleBrain([
        cy(action={"type": "status"}, say=""),
        BrainUnavailable("路由全挂"),
    ])
    driver = make_driver(store, workspace, brain=brain)
    events = collect(driver.converse_loop("s1", "查查状态"))
    assert len([e for e in events if e["t"] == "agent.cycle"]) == 1
    assert events[-1]["t"] == "note" and "LLM 暂不可用" in events[-1]["text"]


# ---------------- 动作特例:execute 审批不被绕过 / plan 进既有流程 ----------------

def test_execute_emits_confirm_card_and_never_starts_pipeline(store, workspace):
    run = seed_ready_run(store, workspace)
    brain = FakeCycleBrain([cy(action={"type": "execute"}, say="都准备好了,请你确认。")])
    driver = make_driver(store, workspace, brain=brain)
    events = collect(driver.converse_loop("s1", "开跑吧"))
    kinds = [e["t"] for e in events]
    assert kinds == ["agent.cycle", "ask", "say"]  # 确认卡 + say 收束,回合终止
    ask = events[1]
    assert run["run_id"] in ask["prompt"] and ask["fields"]
    assert events[2]["parts"] == ["都准备好了,请你确认。"]
    # 铁律:绝不自启流水线 —— run 状态不动,无任何执行事件
    assert store.get_run(run["run_id"])["status"] == "ready"
    assert not any(e["t"] in ("step.start", "step.stage", "result") for e in events)
    assert not any(e["t"] == "note" and "周期上限" in e.get("text", "") for e in events)


def test_execute_without_run_fails_cycle_and_loop_continues(store, workspace):
    brain = FakeCycleBrain([
        cy(action={"type": "execute"}, say="跑!"),
        cy(say="还没有计划,先聊聊需求吧。", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain)
    events = collect(driver.converse_loop("s1", "直接执行"))
    assert any(e["t"] == "note" and "还没有可执行的 run" in e["text"] for e in events)
    assert events[-1]["t"] == "say"  # 循环未终止,LLM 下一周期正常收束
    assert "execute 被拒" in brain.calls[1]["cycles_summary"][0]


def test_plan_action_enters_existing_planning_flow(store, workspace):
    brain = FakeCycleBrain([
        cy(action={"type": "plan", "scenario": "quake", "region": "Ridgecrest 北段"},
           say="按同震场景规划。"),
        cy(say="计划就绪,确认后可以执行。", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain)
    events = collect(driver.converse_loop("s1", "帮我分析地震形变"))
    kinds = [e["t"] for e in events]
    for t in ("agent.cycle", "thinking", "plan", "candidates", "say"):
        assert t in kinds, f"缺 {t}: {kinds}"
    thinking = next(e for e in events if e["t"] == "thinking")
    assert "Ridgecrest 北段" in thinking["body"]  # region 覆盖展示元数据
    run = store.latest_run("s1")
    assert run is not None and run["scenario"] == "quake"
    import json as _json
    assert _json.loads(run["intent"])["source"] == "agent_loop"
    assert "规划完成" in brain.calls[1]["cycles_summary"][0]


def test_plan_action_exception_isolated_to_cycle(store, workspace, monkeypatch):
    """plan 分发进单动作异常隔离(P2-11):规划环节抛错记 ok=False 摘要,不炸回合。"""
    async def boom(self, session_id, session, text, sc, *, intent_source):
        raise RuntimeError("探测炸了")
        yield  # 不可达:仅为把打桩函数定型为异步生成器

    monkeypatch.setattr(Driver, "_plan_turn", boom)
    brain = FakeCycleBrain([
        cy(action={"type": "plan", "scenario": "quake"}, say="规划一下。"),
        cy(say="规划失败,先收束。", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain)
    events = collect(driver.converse_loop("s1", "帮我规划"))
    assert events[-1]["t"] == "say"  # 回合活到 LLM 主动收束,异常没有外溢
    summary = brain.calls[1]["cycles_summary"][0]
    assert "plan 执行异常" in summary and "RuntimeError" in summary


def test_inspect_file_log_tail_only_on_failure(store, workspace, tmp_path):
    """inspect_file 的日志尾行只在失败步骤回灌且 ≤120 字(P2-10,红线 §0.5)。

    同一步先 done 后 failed 各查一次:done 的摘要绝不含日志;failed 的摘要
    回灌尾行且被硬截到 120 字(日志尾行是 500 个 E,只允许前 120 个出现)。
    """
    run = seed_ready_run(store, workspace)
    log = tmp_path / "s1.log"
    log.write_text("ok line\n" + "E" * 500 + "\n", encoding="utf-8")
    store.mark_step(run["run_id"], 1, state="done", log_path=str(log))
    brain = FakeCycleBrain([
        cy(action={"type": "inspect_file", "step": 1}, say=""),
        cy(action={"type": "inspect_file", "step": 1}, say=""),
        cy(say="看完了。", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain)
    events: list[dict] = []

    async def flow():
        async for e in driver.converse_loop("s1", "看第 1 步"):
            events.append(e)
            if e.get("t") == "tool.end" and e.get("id") == "loop1":
                # 第一周期(done)查完后把该步标失败:第二周期查的是 failed 态
                store.mark_step(run["run_id"], 1, state="failed", log_path=str(log))

    asyncio.run(flow())
    ends = [e for e in events if e["t"] == "tool.end"]
    assert "状态 done" in ends[0]["summary"] and "日志尾行" not in ends[0]["summary"]
    assert "状态 failed" in ends[1]["summary"] and "日志尾行" in ends[1]["summary"]
    assert "E" * 120 in ends[1]["summary"]      # 失败时回灌错误窗口
    assert "E" * 121 not in ends[1]["summary"]  # 且硬截 120 字


def test_thinking_action_emits_thinking_event(store, workspace):
    brain = FakeCycleBrain([
        cy(action={"type": "thinking"}, say="先理一下已知条件。"),
        cy(say="想清楚了,需要你提供区域。", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain)
    events = collect(driver.converse_loop("s1", "帮我想想"))
    th = next(e for e in events if e["t"] == "thinking")
    assert th["body"] == "先理一下已知条件。"
    assert not any(e["t"] == "tool.start" for e in events)  # thinking 无工具卡


def test_unknown_action_type_rejected_but_loop_continues(store, workspace):
    brain = FakeCycleBrain([
        cy(action={"type": "fly_to_moon"}, say=""),
        cy(say="换个思路。", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain)
    events = collect(driver.converse_loop("s1", "上天"))
    assert any(e["t"] == "note" and "越界" in e["text"] for e in events)
    assert events[-1]["t"] == "say"
    assert "越界" in brain.calls[1]["cycles_summary"][0]


# ---------------- set_params / set_method:入队既有干预队列 ----------------

def test_set_params_queued_with_run_attribution(store, workspace):
    run = seed_ready_run(store, workspace)
    brain = FakeCycleBrain([
        cy(action={"type": "set_params", "step": 6, "params": {"min_coherence": 0.3}},
           say=""),
        cy(say="阈值改好了,会在下一检查点生效。", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain)
    events = collect(driver.converse_loop("s1", "第 6 步阈值改 0.3"))
    assert any(e["t"] == "intervention" and "已排队" in e["text"] for e in events)
    queued = store.due_actions("steer", run_id=run["run_id"], include_unattributed=False)
    assert len(queued) == 1
    assert queued[0]["action"] == "SET_PARAMS" and queued[0]["target"] == "6"
    assert queued[0]["payload"] == {"params": {"min_coherence": 0.3}}
    end = next(e for e in events if e["t"] == "tool.end")
    assert end["exit"] == 0


def test_set_method_out_of_registry_rejected(store, workspace):
    seed_ready_run(store, workspace)
    brain = FakeCycleBrain([
        cy(action={"type": "set_method", "step": 6, "method": "quantum_unwrap"}, say=""),
        cy(say="方法不存在,算了。", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain)
    events = collect(driver.converse_loop("s1", "第 6 步换量子解缠"))
    end = next(e for e in events if e["t"] == "tool.end")
    assert end["exit"] == 1 and "越界" in end["summary"]
    assert store.due_actions("steer") == []  # 越界绝不入队


# ---------------- steering:捎话并入目标 ----------------

def test_steering_user_message_merged_into_goal(store, workspace):
    run = seed_ready_run(store, workspace)
    store.push_action(scope="run", target=run["run_id"], action="USER_MESSAGE",
                      payload={"text": "顺便把相干性阈值调低一点"},
                      deliver_as="steer", run_id=run["run_id"])
    store.push_action(scope="run", target=run["run_id"], action="KILL",
                      deliver_as="steer", run_id=run["run_id"])
    brain = FakeCycleBrain([
        cy(action={"type": "status"}, say=""),
        cy(say="好,记下了。", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain)
    events = collect(driver.converse_loop("s1", "看下进度"))
    assert any(e["t"] == "intervention" and "已并入用户补充" in e["text"] for e in events)
    assert "顺便把相干性阈值调低一点" in brain.calls[0]["goal"]
    assert "看下进度" in brain.calls[0]["goal"]
    # 只消费 USER_MESSAGE:KILL 留给执行回合的既有消费点
    left = store.due_actions("steer", run_id=run["run_id"], include_unattributed=False)
    assert [a["action"] for a in left] == ["KILL"]


def test_steering_without_run_consumes_unattributed_rows(store, workspace):
    store.create_session("s1", "s1")
    store.push_action(scope="run", target="-", action="USER_MESSAGE",
                      payload={"text": "区域是玉树"}, deliver_as="steer", run_id=None)
    brain = FakeCycleBrain([cy(say="收到,按玉树来。", done=True)])
    driver = make_driver(store, workspace, brain=brain)
    collect(driver.converse_loop("s1", "帮我规划"))
    assert "区域是玉树" in brain.calls[0]["goal"]
    assert store.due_actions("steer") == []


# ---------------- search_data:net/subtasks 的契约防御 ----------------

def test_search_data_degrades_when_net_missing(store, workspace, monkeypatch):
    # sys.modules 置 None:import 必抛 —— 无论 B4/B8 是否已落地都走缺失路径
    monkeypatch.setitem(sys.modules, "insar_agent.net", None)
    monkeypatch.setitem(sys.modules, "insar_agent.loop.subtasks", None)
    brain = FakeCycleBrain([
        cy(action={"type": "search_data", "query": "Ridgecrest SLC"}, say=""),
        cy(say="本地已有数据,不用下载。", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain)
    events = collect(driver.converse_loop("s1", "找数据"))
    end = next(e for e in events if e["t"] == "tool.end")
    assert end["exit"] == 0 and "仅本地盘点" in end["summary"]
    assert "本地数据集" in brain.calls[1]["cycles_summary"][0]


def fake_subtasks_module():
    mod = types.ModuleType("insar_agent.loop.subtasks")

    async def gather_limited(named_tasks, *, limit=3, timeout=None):
        out = {}
        for name, coro in named_tasks.items():
            try:
                value = await coro
                out[name] = types.SimpleNamespace(name=name, ok=True, value=value,
                                                  error=None, elapsed_ms=1)
            except Exception as exc:  # noqa: BLE001 —— 契约:单任务失败不炸整批
                out[name] = types.SimpleNamespace(name=name, ok=False, value=None,
                                                  error=str(exc), elapsed_ms=1)
        return out

    mod.gather_limited = gather_limited
    return mod


def fake_net_module(asf_result, web_result, calls):
    mod = types.ModuleType("insar_agent.net")

    def asf_search(**kwargs):
        calls.setdefault("asf", []).append(kwargs)
        if isinstance(asf_result, Exception):
            raise asf_result
        return asf_result

    def web_search(query, *, k=5, timeout=15.0):
        calls.setdefault("web", []).append({"query": query, "k": k})
        if isinstance(web_result, Exception):
            raise web_result
        return web_result

    mod.asf_search = asf_search
    mod.web_search = web_search
    return mod


def test_search_data_fanout_with_contract_modules(store, workspace, monkeypatch):
    # 断言改版说明(P1-1):此前动作直接带 region_wkt/start/end —— 那是 driver
    # 私有的旧字段,经真实 facade(只透传 query/region/timerange)永远不可达。
    # 现在按 facade 归一化形状喂入,断言 driver 完成 region/timerange →
    # intersects_wkt/start/end 的翻译。
    calls: dict = {}
    monkeypatch.setitem(sys.modules, "insar_agent.net", fake_net_module(
        [{"granule": "a"}, {"granule": "b"}, {"granule": "c"}],
        [{"title": "t", "url": "u", "snippet": "s"}], calls))
    monkeypatch.setitem(sys.modules, "insar_agent.loop.subtasks", fake_subtasks_module())
    brain = FakeCycleBrain([
        cy(action={"type": "search_data", "query": "Ridgecrest 2019 SLC",
                   "region": "POINT(-117.5 35.7)",
                   "timerange": "2019-06-01/2019-08-31"}, say=""),
        cy(say="找到了 3 景。", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain)
    events = collect(driver.converse_loop("s1", "找 Ridgecrest 的数据"))
    end = next(e for e in events if e["t"] == "tool.end")
    assert end["exit"] == 0
    assert "ASF 检索:命中 3 条" in end["summary"]
    assert "网页检索:命中 1 条" in end["summary"]
    assert "本地数据集" in end["summary"]
    assert "注:" not in end["summary"]  # 全部过滤条件都翻译成功,无丢弃注记
    # 检索参数按契约翻译透传(联网纪律:只出查询词/区域/时间,不出数据)
    assert calls["asf"][0]["intersects_wkt"] == "POINT(-117.5 35.7)"
    assert calls["asf"][0]["start"] == "2019-06-01"
    assert calls["asf"][0]["end"] == "2019-08-31"
    assert calls["web"][0]["query"] == "Ridgecrest 2019 SLC"


def test_search_data_non_wkt_region_and_bad_timerange_noted(store, workspace,
                                                            monkeypatch):
    """region 非 WKT / timerange 解析不出:不上送对应参数,摘要如实注记(P1-1)。"""
    calls: dict = {}
    monkeypatch.setitem(sys.modules, "insar_agent.net", fake_net_module(
        [{"granule": "a"}], [], calls))
    monkeypatch.setitem(sys.modules, "insar_agent.loop.subtasks", fake_subtasks_module())
    brain = FakeCycleBrain([
        cy(action={"type": "search_data", "region": "玉树地区", "timerange": "去年夏天"},
           say=""),
        cy(say="按无过滤检索了。", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain)
    events = collect(driver.converse_loop("s1", "找玉树的数据"))
    end = next(e for e in events if e["t"] == "tool.end")
    assert end["exit"] == 0
    assert calls["asf"][0]["intersects_wkt"] is None  # 非 WKT 绝不冒充空间参数
    assert calls["asf"][0]["start"] is None and calls["asf"][0]["end"] is None
    assert "非 WKT" in end["summary"] and "玉树地区" in end["summary"]
    assert "无法解析" in end["summary"]  # 被丢弃的过滤条件不静默蒸发


def test_search_data_single_ended_timerange(store, workspace, monkeypatch):
    """timerange 单端形态("2019-06")按只给 start 翻译(P1-1)。"""
    calls: dict = {}
    monkeypatch.setitem(sys.modules, "insar_agent.net", fake_net_module([], [], calls))
    monkeypatch.setitem(sys.modules, "insar_agent.loop.subtasks", fake_subtasks_module())
    brain = FakeCycleBrain([
        cy(action={"type": "search_data", "timerange": "2019-06"}, say=""),
        cy(say="好。", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain)
    collect(driver.converse_loop("s1", "找数据"))
    assert calls["asf"][0]["start"] == "2019-06" and calls["asf"][0]["end"] is None


def test_search_data_partial_failure_not_total(store, workspace, monkeypatch):
    calls: dict = {}
    monkeypatch.setitem(sys.modules, "insar_agent.net", fake_net_module(
        RuntimeError("ASF 超时"), [], calls))
    monkeypatch.setitem(sys.modules, "insar_agent.loop.subtasks", fake_subtasks_module())
    brain = FakeCycleBrain([
        cy(action={"type": "search_data"}, say=""),
        cy(say="联网检索失败,但本地有货。", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain)
    events = collect(driver.converse_loop("s1", "找数据"))
    end = next(e for e in events if e["t"] == "tool.end")
    assert end["exit"] == 0  # 本地盘点成功 → 部分失败不整体失败
    assert "ASF 检索:失败" in end["summary"] and "本地数据集" in end["summary"]


# ---------------- inspect_file:闭集匹配,零自由路径 ----------------

def make_hyp3_dataset(root):
    d = root / "S1AA_pair1"
    d.mkdir(parents=True)
    stem = "S1AA_20210101T000000_20210113T000000"
    (d / f"{stem}_unw_phase_clipped.tif").write_bytes(b"x" * 2048)
    (d / f"{stem}_corr_clipped.tif").write_bytes(b"y" * 1024)


def test_inspect_file_matches_dataset_by_name(store, workspace, tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setenv("INSAR_DATA_DIR", str(data_root))
    make_hyp3_dataset(data_root)
    brain = FakeCycleBrain([
        cy(action={"type": "inspect_file", "name": "S1AA"}, say=""),
        cy(say="数据在位。", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain)
    events = collect(driver.converse_loop("s1", "看看那个 S1AA 数据"))
    end = next(e for e in events if e["t"] == "tool.end")
    assert end["exit"] == 0
    assert "S1AA_pair1" in end["summary"] and "HyP3 产品" in end["summary"]


def test_inspect_file_step_and_missing_target(store, workspace):
    run = seed_ready_run(store, workspace)
    brain = FakeCycleBrain([
        cy(action={"type": "inspect_file", "step": 1}, say=""),
        cy(action={"type": "inspect_file"}, say=""),
        cy(say="看完了。", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain)
    events = collect(driver.converse_loop("s1", "查查第 1 步"))
    ends = [e for e in events if e["t"] == "tool.end"]
    assert ends[0]["exit"] == 0 and "第 1 步" in ends[0]["summary"]
    assert store.load_step(run["run_id"], 1).state in ends[0]["summary"]
    assert ends[1]["exit"] == 1 and "需要 step" in ends[1]["summary"]


# ---------------- 预算 / 路由钉死 / 用量上下文 ----------------

def test_cycle_summary_clipped_to_budget(store, workspace, monkeypatch):
    monkeypatch.setattr(Driver, "_run_status_text", lambda self, sid: "长" * 2000)
    brain = FakeCycleBrain([
        cy(action={"type": "status"}, say=""),
        cy(say="状态太长,截断了。", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain)
    collect(driver.converse_loop("s1", "状态?"))
    entry = brain.calls[1]["cycles_summary"][0]
    assert entry.startswith("[1] status:")
    assert len(entry) <= len("[1] status:") + 300  # 原始输出绝不整段进 LLM
    assert entry.endswith("…")


def test_route_pin_locked_after_first_cycle(store, workspace):
    brain = FakeCycleBrain([
        cy(action={"type": "status"}, say="", route_index=1),
        cy(action={"type": "check_env"}, say="", route_index=0),
        cy(say="完事。", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain)
    collect(driver.converse_loop("s1", "查状态和环境"))
    assert brain.calls[0]["route_pin"] is None  # 首周期自由选路
    assert brain.calls[1]["route_pin"] == 1     # 之后钉死首周期路由
    assert brain.calls[2]["route_pin"] == 1     # 后续返回值不改钉


def test_usage_context_binds_session(store, workspace):
    from insar_agent.brain import usage as usage_mod

    seen: dict = {}

    class UsageProbeBrain(FakeCycleBrain):
        def cycle(self, **kwargs):
            seen["ctx"] = usage_mod._context.get()  # to_thread 会拷贝 contextvars
            return super().cycle(**kwargs)

    brain = UsageProbeBrain([cy(say="好。", done=True)])
    driver = make_driver(store, workspace, brain=brain)
    collect(driver.converse_loop("s1", "你好"))
    assert seen["ctx"] == ("s1", None)  # 回合外围绑定 session_id(LOOP-CONTRACT §4)
