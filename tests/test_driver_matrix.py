"""组合矩阵:干预 × 生命周期 × 事件流 × 失效传播(loop/driver 系统化交错测试)。

与单点测试(test_loop / test_admin_control / test_executor)的分工:那边各锁一个
场景,这里把「干预类型 × 注入时机 × 生命周期操作」组合起来跑,并给每个用例统一
套两把尺子:

    assert_event_invariants   事件流结构不变量(配对/单调/终态恰一次/窗口归属)
    assert_store_consistent   静止状态库自洽(无 running 遗留/账本闭合/状态相容)

矩阵设计(与任务书条目对应):
  1. 干预时机 × 类型:KILL(步间/步中)、PAUSE→PLAY、SET_PARAMS(steer,
     目标分「未跑步骤」与「已完成步骤」)、SET_METHOD(next_run)、SKIP、RESET
  2. 生命周期交错:abort→续跑;pause→服务重启→resume;执行中 fork→双 run
     并行执行(租约互斥/动作按 run 隔离)
  3. 事件流不变量:干净全链的精确断言 + 所有用例复用同一检查器
  4. 失效传播:done 后 fork,science 改动在 3 个不同步位 + presentation 只重跑本步
  5. 随机化冒烟:seed=0 的 20 步随机干预序列,终态库自洽
  6. 语义升级(2026-08-12 决策,正反例):末步 steer 收尾前消费(决策一)、
     done run 重入防空转(决策二)、done-带待重跑的收尾明示(决策三)

提速手段(纪律:单用例秒级,矩阵合计 <150s):注册表缩减为真实流水线的前 6 步
(1→2→3→4→5→6,含 3 依赖 (1,2) 的汇聚边),全部 simulated 引擎,轮询 0.03s。
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from dataclasses import replace

import pytest

from insar_agent.brain.facade import Brain
from insar_agent.core.db import Database
from insar_agent.core.store import Store
from insar_agent.engines import simulate
from insar_agent.loop.driver import Driver
from insar_agent.planner.plan import fork_run, make_plan
from insar_agent.registry.capabilities import REGISTRY
from insar_agent.registry.model import Param
from insar_agent.runtime.probe import ProbeResult

# ---------------------------------------------------------------------------
# 夹具:缩减注册表(前 6 步)+ 空探针 + simulated 全链
# ---------------------------------------------------------------------------

MATRIX_STEPS = (1, 2, 3, 4, 5, 6)


def matrix_registry():
    """真实流水线前 6 步:1→2→3(deps 1,2)→4→5→6,依赖/产物/参数声明全部沿用
    真实 registry;第 5 步额外补一个 presentation 参数(真实前 6 步没有呈现参数,
    失效传播矩阵需要「只重跑本步」的对照组)。"""
    caps = {sid: REGISTRY[sid] for sid in MATRIX_STEPS}
    caps[5] = replace(caps[5], params={
        **caps[5].params,
        "render_note": Param("plain", kind="presentation", type="str"),
    })
    return caps


def empty_probe():
    return ProbeResult(engines={"isce2": None, "mintpy": None, "snaphu": None, "gdal": None,
                                "snap": None, "pystamps": None, "pyaps": None},
                       credentials={"earthdata": False, "cds": False, "gacos": False},
                       disk_free_gb=100.0, cpu_count=8)


def make_driver(store, workspace, **kw) -> Driver:
    defaults = dict(workspace=workspace, probe=empty_probe(), poll=0.03,
                    startup_grace=15.0, allow_simulated=True, brain=Brain(None),
                    registry=matrix_registry())
    defaults.update(kw)
    return Driver(store, **defaults)


def plan_run(driver: Driver, session: str) -> str:
    """不经 turn(不依赖意图识别),直接落一个 6 步计划;返回 run_id。"""
    driver.store.create_session(session, session)
    plan = make_plan(driver.store, session, registry=driver.registry,
                     probe=driver.probe(), scenario=None,
                     workspace=str(driver.workspace), allow_simulated=True,
                     agent_hash=driver.agent_hash)
    assert not plan.problems, f"缩减链规划不应有可行性问题:{plan.problems}"
    return plan.run_id


async def collect(agen) -> list[dict]:
    return [e async for e in agen]


async def drive(agen, *, on_event=None, stop_when=None) -> list[dict]:
    """收集事件;on_event 逐事件回调(注入干预用);stop_when 命中则中断并
    关闭生成器(模拟宿主崩溃/放弃回合)。"""
    events: list[dict] = []
    stopped = False
    async for e in agen:
        events.append(e)
        if on_event is not None:
            on_event(e)
        if stop_when is not None and stop_when(e):
            stopped = True
            break
    if stopped:
        await agen.aclose()
    return events


async def await_until(cond, timeout: float = 20.0, msg: str = "条件未满足"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        await asyncio.sleep(0.02)
    pytest.fail(f"{msg}(等待 {timeout}s)")


def push_once(fired: dict, key: str, push) -> None:
    """事件回调里只注入一次的小工具。"""
    if key not in fired:
        fired[key] = True
        push()


def slow_step(monkeypatch, sid: int, lines: int = 50) -> None:
    """把第 sid 步的模拟日志拉长(lines × 0.02s ≈ 1s),给步中干预留出确定窗口。"""
    monkeypatch.setitem(simulate._LOG_LINES, sid, [f"忙碌 {i}" for i in range(lines)])


# ---------------------------------------------------------------------------
# 两把尺子:事件流不变量 + 库自洽(供后续测试复用)
# ---------------------------------------------------------------------------

_STEP_TOOL = re.compile(r"s\d+")

RUN_STATUSES = ("planning", "ready", "running", "paused", "interrupted", "done", "failed")
STEP_STATES = ("pending", "running", "done", "failed", "interrupted", "orphaned",
               "stale", "skipped")


def assert_event_invariants(events: list[dict], *, terminal: str | None = None) -> dict:
    """执行回合事件流的结构不变量。

    - step.start/step.end 严格配对且不嵌套、不交叉
    - 步骤级 tool.start/tool.end/tool.log(id=sN)只出现在所属步骤窗口内
    - 泵入的 step.stage 只出现在所属步骤 start/end 之间
    - overall 单调不减
    - 终态事件恰一次:result 与 report 成对且至多一次;terminal="done" 时恰一次,
      非正常终态(paused/interrupted/failed)时零次且必须有 warn/bad note 解释
    - 语义升级(2026-08-12 决策三):done 回合的 report 之后允许(且仅允许)
      提示 note 收尾 ——「done 但有待重跑步骤」时 driver 在 report 后补一条
      待重跑提示;无待重跑时 report 仍是最后一个事件
    返回 {pairs, overall_max, n_result} 供用例做进一步断言。
    """
    open_step: int | None = None
    open_tools: set[str] = set()
    pairs: list[int] = []
    last_overall = -1
    n_result = n_report = 0
    for e in events:
        t = e["t"]
        if t == "step.start":
            assert open_step is None, f"step.start({e['stepId']}) 时第 {open_step} 步未闭合"
            open_step = e["stepId"]
        elif t == "step.end":
            assert open_step == e["stepId"], \
                f"step.end({e['stepId']}) 与当前打开步骤 {open_step} 不配对"
            pairs.append(open_step)
            open_step = None
        elif t == "step.stage":
            assert open_step == e["stepId"], \
                f"step.stage({e['stepId']}/{e['stage']}) 漏出所属步骤窗口(当前 {open_step})"
        elif t == "tool.start" and _STEP_TOOL.fullmatch(str(e["id"])):
            assert open_step is not None and e["id"] == f"s{open_step}", \
                f"tool.start({e['id']}) 不在所属步骤窗口(当前 {open_step})"
            open_tools.add(e["id"])
        elif t == "tool.end" and _STEP_TOOL.fullmatch(str(e["id"])):
            assert e["id"] in open_tools, f"tool.end({e['id']}) 无配对的 tool.start"
            open_tools.discard(e["id"])
        elif t == "tool.log" and _STEP_TOOL.fullmatch(str(e["id"])):
            assert open_step is not None and e["id"] == f"s{open_step}", \
                f"tool.log({e['id']}) 漏出所属步骤窗口(当前 {open_step})"
        elif t == "overall":
            assert e["pct"] >= last_overall, f"overall 回退:{last_overall} -> {e['pct']}"
            last_overall = e["pct"]
        elif t == "result":
            n_result += 1
        elif t == "report":
            n_report += 1
    assert open_step is None, f"第 {open_step} 步缺 step.end"
    assert not open_tools, f"tool 窗口未闭合:{open_tools}"
    assert n_result <= 1 and n_report <= 1 and n_result == n_report, \
        f"终态事件必须成对且至多一次:result={n_result} report={n_report}"
    if terminal == "done":
        assert n_result == 1, "run 完成的回合必须恰有一次 result/report"
        # 决策三(2026-08-12):report 后只允许提示 note(待重跑步骤明示),
        # 其余事件仍必须出现在 report 之前 —— 原断言是 events[-1] 恰为 report
        i_report = next(i for i, e in enumerate(events) if e["t"] == "report")
        assert all(e["t"] == "note" for e in events[i_report + 1:]), \
            "report 之后只允许提示 note(待重跑明示),不得再有其他事件"
    elif terminal in ("paused", "interrupted", "failed"):
        assert n_result == 0, f"{terminal} 回合不应发 result/report"
        assert any(e["t"] == "note" and e.get("tone") in ("warn", "bad") for e in events), \
            f"{terminal} 回合必须有 warn/bad note 解释终态"
    return {"pairs": pairs, "overall_max": last_overall, "n_result": n_result}


def assert_store_consistent(store: Store) -> None:
    """静止状态(没有执行回合在飞)的库自洽断言 —— 可复用的完整性查询。

    - 无运行锁残留(execute 的 finally 必须释放租约)
    - 无 run/step 级 running 遗留
    - 命令账本闭合:凡有意图(reserve)必有结算(exit_code 非 NULL)
    - stage/state 相容:done ⇔ VERIFIED+run_ok=1;pending ⇔ PENDING;stale 必带标记
    - run 终态与步骤状态相容:done 不含失败步;failed 至少一失败步
    """
    leases = list(store.db.query("SELECT resource, holder FROM leases"))
    assert not leases, f"静止状态不应遗留运行锁:{[dict(r) for r in leases]}"
    for run in store.list_runs():
        rid = run["run_id"]
        assert run["status"] in RUN_STATUSES, f"run {rid} 状态非法:{run['status']}"
        assert run["status"] != "running", f"run {rid} 遗留 running(回合已结束必须收尾)"
        steps = store.load_steps(rid)
        for s in steps:
            assert s.state in STEP_STATES, f"step {rid}/{s.step_id} 状态非法:{s.state}"
            assert s.state != "running", f"step {rid}/{s.step_id} 遗留 running"
            if s.state == "done":
                assert s.stage == "VERIFIED" and s.run_ok == 1, \
                    f"step {rid}/{s.step_id} done 但 stage={s.stage} run_ok={s.run_ok}"
            if s.state == "pending":
                assert s.stage == "PENDING", \
                    f"step {rid}/{s.step_id} pending 但 stage={s.stage}"
            if s.state == "stale":
                assert s.stale, f"step {rid}/{s.step_id} state=stale 必须带 stale 标记"
        for c in store.commands_of(rid):
            assert c["exit_code"] is not None, \
                f"未结算命令 id={c['id']}(run {rid} 第 {c['step_id']} 步):账本必须闭合"
        states = [s.state for s in steps]
        if run["status"] == "done":
            assert all(v in ("done", "skipped", "pending", "stale") for v in states), \
                f"done run {rid} 含失败/中断步骤:{states}"
        if run["status"] == "failed":
            assert "failed" in states, f"failed run {rid} 找不到失败步骤:{states}"


def command_counts(store: Store, run_id: str) -> dict[int, int]:
    return {sid: len(store.commands_of(run_id, sid)) for sid in MATRIX_STEPS}


# ===========================================================================
# 1. 干预时机 × 类型
# ===========================================================================

def test_kill_between_steps_zero_new_effects(store, workspace):
    """KILL 在执行回合开始前排队 → 第一个步间检查点消费:零步骤启动、零命令意图,
    run 收尾 interrupted,control 位已兑现复位。"""
    driver = make_driver(store, workspace)
    run_id = plan_run(driver, "s1")
    store.push_action(scope="run", target=run_id, action="KILL",
                      deliver_as="steer", run_id=run_id)

    events = asyncio.run(collect(driver.execute("s1")))
    assert_event_invariants(events, terminal="interrupted")
    assert not [e for e in events if e["t"] == "step.start"]
    assert any(e["t"] == "intervention" and "KILL" in e["text"] for e in events)
    run = store.get_run(run_id)
    assert run["status"] == "interrupted"
    assert run["control"] == "running"  # 意图已兑现,复位
    assert all(s.state == "pending" for s in store.load_steps(run_id))
    assert not store.commands_of(run_id)  # 零新效果
    assert not store.due_actions("steer", run_id=run_id)  # 动作已结算,不悬挂
    assert_store_consistent(store)


def test_kill_during_step_interrupts_then_resumes(store, workspace, monkeypatch):
    """KILL 在第 3 步执行中注入(泵内每 poll 消费,即时语义):当前步 interrupted、
    已完成步保留、在途命令照常结算;续跑接回并全链完成。"""
    slow_step(monkeypatch, 3)  # 第 3 步 ≈1s,取消窗口确定
    driver = make_driver(store, workspace)
    run_id = plan_run(driver, "s1")

    fired: dict = {}
    events = asyncio.run(drive(
        driver.execute("s1"),
        on_event=lambda e: (e["t"] == "step.start" and e["stepId"] == 3 and push_once(
            fired, "kill", lambda: store.push_action(
                scope="run", target=run_id, action="KILL",
                deliver_as="steer", run_id=run_id)))))
    assert_event_invariants(events, terminal="interrupted")
    assert any(e["t"] == "intervention" and e.get("mode") == "steer" for e in events)
    run = store.get_run(run_id)
    assert run["status"] == "interrupted" and run["control"] == "running"
    states = {s.step_id: s.state for s in store.load_steps(run_id)}
    assert states[1] == "done" and states[2] == "done"  # 已完成步保留
    assert states[3] == "interrupted"
    assert states[4] == states[5] == states[6] == "pending"  # 取消后零新步骤
    # 在途命令照常结算(不留 NULL 账);未启动的步骤零命令
    for sid in (4, 5, 6):
        assert not store.commands_of(run_id, sid)
    for c in store.commands_of(run_id):
        assert c["exit_code"] is not None

    # 续跑:interrupted 步骤自动复位重跑,全链完成
    events2 = asyncio.run(collect(driver.execute("s1")))
    assert_event_invariants(events2, terminal="done")
    assert store.get_run(run_id)["status"] == "done"
    assert all(s.state == "done" for s in store.load_steps(run_id))
    assert_store_consistent(store)


def test_pause_before_any_step_then_play_via_queue(store, workspace):
    """PAUSE 排队在先 → 第一个检查点即暂停(零步骤启动);PLAY 经队列消费后
    整链跑完。验证 PAUSE/PLAY 的 apply_action 路径与暂停语义。"""
    driver = make_driver(store, workspace)
    run_id = plan_run(driver, "s1")
    store.push_action(scope="run", target=run_id, action="PAUSE",
                      deliver_as="steer", run_id=run_id)

    events = asyncio.run(collect(driver.execute("s1")))
    assert_event_invariants(events, terminal="paused")
    assert not [e for e in events if e["t"] == "step.start"]
    assert store.get_run(run_id)["status"] == "paused"
    assert all(s.state == "pending" for s in store.load_steps(run_id))

    store.push_action(scope="run", target=run_id, action="PLAY",
                      deliver_as="steer", run_id=run_id)
    events2 = asyncio.run(collect(driver.execute("s1")))
    assert_event_invariants(events2, terminal="done")
    assert any(e["t"] == "intervention" and "恢复" in e["text"] for e in events2)
    assert store.get_run(run_id)["status"] == "done"
    assert_store_consistent(store)


def test_pause_mid_run_preserves_progress(store, workspace):
    """PAUSE 在第 2 步执行中注入 → 当前步跑完后暂停(steer 步间语义):
    1-2 done、3-6 pending;再执行只跑剩余步骤,不重启已完成命令。"""
    driver = make_driver(store, workspace)
    run_id = plan_run(driver, "s1")

    fired: dict = {}
    events = asyncio.run(drive(
        driver.execute("s1"),
        on_event=lambda e: (e["t"] == "step.start" and e["stepId"] == 2 and push_once(
            fired, "pause", lambda: store.push_action(
                scope="run", target=run_id, action="PAUSE",
                deliver_as="steer", run_id=run_id)))))
    assert_event_invariants(events, terminal="paused")
    states = {s.step_id: s.state for s in store.load_steps(run_id)}
    assert states[1] == "done" and states[2] == "done"  # 当前步跑完才暂停
    assert all(states[sid] == "pending" for sid in (3, 4, 5, 6))

    events2 = asyncio.run(collect(driver.execute("s1")))
    assert_event_invariants(events2, terminal="done")
    assert command_counts(store, run_id) == {sid: 1 for sid in MATRIX_STEPS}  # 无重复启动
    assert_store_consistent(store)


def test_steer_set_params_lands_between_steps(store, workspace):
    """SET_PARAMS(steer)在第 2 步执行中注入、目标是未跑的第 4 步:
    干预事件出现在 step.end(2) 与 step.start(3) 之间(步间投递语义),
    第 4 步用新参数执行,run 正常完成。"""
    driver = make_driver(store, workspace)
    run_id = plan_run(driver, "s1")
    old_args = store.load_step(run_id, 4).args_hash

    fired: dict = {}
    events = asyncio.run(drive(
        driver.execute("s1"),
        on_event=lambda e: (e["t"] == "step.start" and e["stepId"] == 2 and push_once(
            fired, "steer", lambda: store.push_action(
                scope="step", target="4", action="SET_PARAMS",
                payload={"params": {"range_looks": 5}},
                deliver_as="steer", run_id=run_id)))))
    assert_event_invariants(events, terminal="done")

    idx = {(e["t"], e.get("stepId")): i for i, e in enumerate(events)
           if e["t"] in ("step.start", "step.end")}
    itv = [i for i, e in enumerate(events) if e["t"] == "intervention"]
    assert len(itv) == 1
    assert idx[("step.end", 2)] < itv[0] < idx[("step.start", 3)], \
        "steer 必须在当前步结束后、下一步启动前生效"

    step4 = store.load_step(run_id, 4)
    assert step4.params["range_looks"] == 5
    assert step4.args_hash != old_args  # science 参数进指纹
    assert step4.state == "done"
    assert store.get_run(run_id)["status"] == "done"
    assert_store_consistent(store)


def test_steer_set_params_on_done_step_marks_stale_then_rerun_closes(store, workspace):
    """SET_PARAMS(steer)目标是已完成的第 1 步(science):已完成的 1-3 标脏、
    未跑的 4-6 只改指纹;本回合照常完成;第二回合恰好重跑 1-3,账本闭合。"""
    driver = make_driver(store, workspace)
    run_id = plan_run(driver, "s1")

    fired: dict = {}
    events = asyncio.run(drive(
        driver.execute("s1"),
        on_event=lambda e: (e["t"] == "step.start" and e["stepId"] == 3 and push_once(
            fired, "steer", lambda: store.push_action(
                scope="step", target="1", action="SET_PARAMS",
                payload={"params": {"scenes": 9}},
                deliver_as="steer", run_id=run_id)))))
    assert_event_invariants(events, terminal="done")
    itv = [e for e in events if e["t"] == "intervention"]
    assert len(itv) == 1 and itv[0]["affected"] == [1, 2, 3, 4, 5, 6]

    states = {s.step_id: s.state for s in store.load_steps(run_id)}
    assert states[1] == states[2] == states[3] == "stale"  # 干预时已 done 的 1-3
    assert states[4] == states[5] == states[6] == "done"   # 干预后用新指纹跑完
    assert store.get_run(run_id)["status"] == "done"

    events2 = asyncio.run(collect(driver.execute("s1")))
    assert_event_invariants(events2, terminal="done")
    assert {e["stepId"] for e in events2 if e["t"] == "step.start"} == {1, 2, 3}
    assert all(s.state == "done" for s in store.load_steps(run_id))
    assert command_counts(store, run_id) == {1: 2, 2: 2, 3: 2, 4: 1, 5: 1, 6: 1}
    assert_store_consistent(store)


def test_next_run_set_method_defers_to_next_planning(store, workspace):
    """SET_METHOD(next_run)在执行中注入:本 run 全程不受影响、动作不被消费;
    下一次规划回合(turn)消费并生成带覆写的新计划。"""
    driver = make_driver(store, workspace)
    asyncio.run(collect(driver.turn("s1", "玉树冻土形变")))  # 场景规则:permafrost
    run1 = store.latest_run("s1")["run_id"]

    fired: dict = {}
    events = asyncio.run(drive(
        driver.execute("s1"),
        on_event=lambda e: (e["t"] == "step.start" and e["stepId"] == 2 and push_once(
            fired, "next", lambda: store.push_action(
                scope="step", target="5", action="SET_METHOD",
                payload={"method": "none"}, deliver_as="next_run", run_id=run1)))))
    assert_event_invariants(events, terminal="done")
    assert store.load_step(run1, 5).method == "goldstein"  # 本 run 不受影响
    assert len(store.due_actions("next_run")) == 1          # 执行回合不消费 next_run

    turn2 = asyncio.run(collect(driver.turn("s1", "玉树冻土形变")))
    assert any(e["t"] == "intervention" and "预约" in e["text"] for e in turn2)
    assert not store.due_actions("next_run")                # 规划回合消费
    run2 = store.latest_run("s1")["run_id"]
    assert run2 != run1                                     # 覆写强制重新规划
    assert store.load_step(run2, 5).method == "none"
    assert store.load_step(run1, 5).method == "goldstein"
    assert_store_consistent(store)


def test_skip_intervention_prevents_execution(store, workspace):
    """SKIP(steer)目标是尚未跑到的第 6 步:步间消费后该步不得再被执行
    (回合入口的 step_ids 快照必须复查状态)—— 零命令、状态保持 skipped,
    其余步骤照常完成,overall 仍推进到 100。"""
    driver = make_driver(store, workspace)
    run_id = plan_run(driver, "s1")
    store.push_action(scope="step", target="6", action="SKIP",
                      deliver_as="steer", run_id=run_id)

    events = asyncio.run(collect(driver.execute("s1")))
    info = assert_event_invariants(events, terminal="done")
    assert 6 not in info["pairs"], "skipped 步骤不得出现 step.start/step.end"
    assert info["overall_max"] == 100  # 跳过计入进度,总进度仍闭合
    step6 = store.load_step(run_id, 6)
    assert step6.state == "skipped"
    assert not store.commands_of(run_id, 6)  # 零命令意图 = 零执行
    assert {s.step_id for s in store.load_steps(run_id) if s.state == "done"} \
        == {1, 2, 3, 4, 5}
    assert store.get_run(run_id)["status"] == "done"
    assert_store_consistent(store)


def test_reset_done_step_mid_run_marks_downstream_then_rerun_closes(store, workspace):
    """RESET 在第 4 步执行中注入、目标是已完成的第 2 步:步间消费后第 2 步复位
    pending、下游已完成的 3/4 标脏;本回合把 5/6 跑完;第二回合恰好重跑 2/3/4。"""
    driver = make_driver(store, workspace)
    run_id = plan_run(driver, "s1")

    fired: dict = {}
    events = asyncio.run(drive(
        driver.execute("s1"),
        on_event=lambda e: (e["t"] == "step.start" and e["stepId"] == 4 and push_once(
            fired, "reset", lambda: store.push_action(
                scope="step", target="2", action="RESET",
                deliver_as="steer", run_id=run_id)))))
    assert_event_invariants(events, terminal="done")
    itv = [e for e in events if e["t"] == "intervention"]
    assert len(itv) == 1 and itv[0]["affected"] == [2, 3, 4]

    states = {s.step_id: s.state for s in store.load_steps(run_id)}
    assert states[2] == "pending" and store.load_step(run_id, 2).stage == "PENDING"
    assert states[3] == "stale" and states[4] == "stale"  # 干预时已 done 的下游
    assert states[1] == "done" and states[5] == "done" and states[6] == "done"

    events2 = asyncio.run(collect(driver.execute("s1")))
    assert_event_invariants(events2, terminal="done")
    assert {e["stepId"] for e in events2 if e["t"] == "step.start"} == {2, 3, 4}
    assert command_counts(store, run_id) == {1: 1, 2: 2, 3: 2, 4: 2, 5: 1, 6: 1}
    assert_store_consistent(store)


# ===========================================================================
# 2. 生命周期交错
# ===========================================================================

def test_abort_control_bit_mid_step_then_rerun(store, workspace, monkeypatch):
    """执行中直接落 control 位(API KILL 路径,非队列):当前步 interrupted、
    control 兑现复位;续跑接回全链完成,账本全程闭合。"""
    slow_step(monkeypatch, 2)
    driver = make_driver(store, workspace)
    run_id = plan_run(driver, "s1")

    fired: dict = {}
    events = asyncio.run(drive(
        driver.execute("s1"),
        on_event=lambda e: (e["t"] == "step.start" and e["stepId"] == 2 and push_once(
            fired, "abort", lambda: driver.request_cancel(run_id)))))
    assert_event_invariants(events, terminal="interrupted")
    run = store.get_run(run_id)
    assert run["status"] == "interrupted" and run["control"] == "running"
    states = {s.step_id: s.state for s in store.load_steps(run_id)}
    assert states[1] == "done" and states[2] == "interrupted"
    for c in store.commands_of(run_id):
        assert c["exit_code"] is not None  # 在途命令照常结算

    events2 = asyncio.run(collect(driver.execute("s1")))
    assert_event_invariants(events2, terminal="done")
    assert store.get_run(run_id)["status"] == "done"
    assert all(s.state == "done" for s in store.load_steps(run_id))
    assert_store_consistent(store)


def test_pause_then_restart_resume_stays_paused(store, workspace):
    """pause → 服务重启(新 Driver 实例同库):resume 不得自动接回用户主动暂停的
    run(paused ≠ running);显式 execute 才继续,且不重复启动已完成步骤。"""
    driver = make_driver(store, workspace)
    run_id = plan_run(driver, "s1")
    fired: dict = {}
    asyncio.run(drive(
        driver.execute("s1"),
        on_event=lambda e: (e["t"] == "step.start" and e["stepId"] == 2 and push_once(
            fired, "pause", lambda: store.push_action(
                scope="run", target=run_id, action="PAUSE",
                deliver_as="steer", run_id=run_id)))))
    assert store.get_run(run_id)["status"] == "paused"

    driver2 = make_driver(store, workspace)  # 「重启」:内存 token/租约状态全丢
    resume_events = asyncio.run(collect(driver2.resume("s1")))
    assert resume_events == []  # paused 不是 running:恢复流程不碰它
    assert store.get_run(run_id)["status"] == "paused"

    events = asyncio.run(collect(driver2.execute("s1")))
    assert_event_invariants(events, terminal="done")
    assert {e["stepId"] for e in events if e["t"] == "step.start"} == {3, 4, 5, 6}
    assert command_counts(store, run_id) == {sid: 1 for sid in MATRIX_STEPS}
    assert_store_consistent(store)


def test_crash_mid_step_restart_reattaches_without_double_launch(store, workspace,
                                                                 monkeypatch):
    """执行中宿主「崩溃」(放弃回合生成器):库里遗留 running run + LAUNCHED 步骤
    + 未结算命令;新 Driver resume 接回 —— 不重复启动(命令仍只有 1 条),
    跟随既有作业到结束并结算,全链完成。"""
    slow_step(monkeypatch, 2)
    driver = make_driver(store, workspace)
    run_id = plan_run(driver, "s1")

    events = asyncio.run(drive(
        driver.execute("s1"),
        stop_when=lambda e: e["t"] == "step.stage" and e["stepId"] == 2
        and e["stage"] == "LAUNCHED"))
    assert any(e["t"] == "step.stage" for e in events)
    # 崩溃现场:run 仍 running,第 2 步 LAUNCHED/running,命令意图未结算
    assert store.get_run(run_id)["status"] == "running"
    step2 = store.load_step(run_id, 2)
    assert step2.state == "running" and step2.stage == "LAUNCHED"
    assert store.latest_unsettled_command(run_id, 2) is not None

    driver2 = make_driver(store, workspace)  # 重启:同库同工作区
    resume_events = asyncio.run(collect(driver2.resume("s1")))
    assert any(e["t"] == "reattach" for e in resume_events)  # 接回,不重跑
    assert_event_invariants(resume_events, terminal="done")
    assert store.get_run(run_id)["status"] == "done"
    cmds = store.commands_of(run_id, 2)
    assert len(cmds) == 1 and cmds[0]["exit_code"] == 0  # 绝没有第二次启动
    assert all(s.state == "done" for s in store.load_steps(run_id))
    assert_store_consistent(store)


def test_fork_mid_execution_parallel_runs_lease_and_action_isolation(store, workspace,
                                                                     tmp_path):
    """执行中 fork → 两个 run 并行 execute:
    - 同 run 二次 execute 被租约拒绝(零事件副作用);
    - 异 run 互不干扰:各自跑到 done,fork 复用父 run 已完成步骤(零命令);
    - 动作按 run 隔离:A/B 各自的 steer 只改各自的参数,不互吞。"""
    d1 = make_driver(store, workspace)
    ws2 = tmp_path / "ws2"
    d2 = make_driver(store, ws2)
    run_a = plan_run(d1, "s1")

    async def scenario():
        task_a = asyncio.create_task(collect(d1.execute("s1", run_id=run_a)))

        def a_at_step3():
            s = store.load_step(run_a, 3)
            return s is not None and s.state == "running"

        await await_until(a_at_step3, msg="run A 未推进到第 3 步")
        # 此刻 A 的 1-2 已 done:fork 改第 5 步 science 参数
        plan_b = fork_run(store, run_a, registry=d1.registry,
                          changes={5: {"params": {"alpha": 0.6}}}, probe=empty_probe())
        run_b = plan_b.run_id
        # 同 run 并发执行:租约拒绝
        rejected = await collect(d1.execute("s1", run_id=run_a))
        # 动作按 run 定向
        store.push_action(scope="step", target="5", action="SET_PARAMS",
                          payload={"params": {"alpha": 0.5}},
                          deliver_as="steer", run_id=run_a)
        store.push_action(scope="step", target="6", action="SET_PARAMS",
                          payload={"params": {"min_coherence": 0.35}},
                          deliver_as="steer", run_id=run_b)
        task_b = asyncio.create_task(collect(d2.execute("s1", run_id=run_b)))
        ev_a, ev_b = await asyncio.gather(task_a, task_b)
        return run_b, rejected, ev_a, ev_b

    run_b, rejected, ev_a, ev_b = asyncio.run(scenario())

    # 租约:同 run 拒绝,拒绝回合零步骤事件
    assert len(rejected) == 1 and rejected[0]["t"] == "note"
    assert "运行锁" in rejected[0]["text"] or "并发" in rejected[0]["text"]

    # 两条流各自结构完好、各自完成
    assert_event_invariants(ev_a, terminal="done")
    assert_event_invariants(ev_b, terminal="done")
    assert store.get_run(run_a)["status"] == "done"
    assert store.get_run(run_b)["status"] == "done"

    # fork 复用:B 的 1-2 直接 done(零命令,qa 标注复用来源)
    for sid in (1, 2):
        assert not store.commands_of(run_b, sid)
        step = store.load_step(run_b, sid)
        assert step.state == "done" and step.qa[0]["check"] == "reused_from_parent"

    # 动作隔离:各改各的,不互吞
    assert store.load_step(run_a, 5).params["alpha"] == 0.5
    assert store.load_step(run_b, 5).params["alpha"] == 0.6
    assert store.load_step(run_a, 6).params["min_coherence"] == 0.25  # A 未被 B 的动作污染
    assert store.load_step(run_b, 6).params["min_coherence"] == 0.35
    assert not store.due_actions("steer", run_id=run_a)
    assert not store.due_actions("steer", run_id=run_b)
    assert_store_consistent(store)


# ===========================================================================
# 3. 事件流不变量(干净全链的精确断言;检查器本身被所有用例复用)
# ===========================================================================

def test_event_stream_invariants_clean_chain(store, workspace):
    driver = make_driver(store, workspace)
    run_id = plan_run(driver, "s1")
    events = asyncio.run(collect(driver.execute("s1")))

    info = assert_event_invariants(events, terminal="done")
    assert info["pairs"] == [1, 2, 3, 4, 5, 6]  # 严格配对且按拓扑序
    overall = [e["pct"] for e in events if e["t"] == "overall"]
    assert overall == [17, 33, 50, 67, 83, 100]  # 单调、逐级、闭合到 100

    # 每步的 stage 事件序:PREPARED → LAUNCHED → COLLECTED → VERIFIED
    stages: dict[int, list[str]] = {}
    for e in events:
        if e["t"] == "step.stage":
            stages.setdefault(e["stepId"], []).append(e["stage"])
    for sid in MATRIX_STEPS:
        assert stages[sid] == ["PREPARED", "LAUNCHED", "COLLECTED", "VERIFIED"]

    # 执行期日志经泵进入回合流(双通道承诺)
    assert any(e["t"] == "tool.log" and "[simulated]" in e["line"] for e in events)
    assert store.get_run(run_id)["status"] == "done"
    assert_store_consistent(store)


# ===========================================================================
# 4. 失效传播交错(done 后 fork;3 个 science 步位 + presentation 对照)
# ===========================================================================

@pytest.fixture(scope="module")
def fork_base(tmp_path_factory):
    """模块级基线:一条跑完的 6 步链,供多个 fork 用例复用(fork 不改父 run)。"""
    db = Database(":memory:")
    store = Store(db)
    ws = tmp_path_factory.mktemp("fork-base")
    driver = make_driver(store, ws)
    run_id = plan_run(driver, "s-base")
    events = asyncio.run(collect(driver.execute("s-base")))
    assert_event_invariants(events, terminal="done")
    assert store.get_run(run_id)["status"] == "done"
    yield store, driver, run_id
    db.close()


@pytest.mark.parametrize("sid,patch,expect_rerun", [
    (1, {"scenes": 9}, {1, 2, 3, 4, 5, 6}),          # 链头:全下游失效
    (4, {"range_looks": 5}, {4, 5, 6}),               # 链中:上游复用,下游失效
    (6, {"min_coherence": 0.4}, {6}),                 # 链尾:只重跑本步
])
def test_fork_science_change_stale_set_matches_position(fork_base, sid, patch,
                                                        expect_rerun):
    """done 后改 science 参数 fork:待重跑集合 = 改动步 + 其全部下游,
    其余步骤零重算复用(状态 done、qa 标注、零命令);父 run 不被触碰。"""
    store, driver, parent = fork_base
    plan = fork_run(store, parent, registry=driver.registry,
                    changes={sid: {"params": patch}}, probe=empty_probe())

    states = {s.step_id: s.state for s in store.load_steps(plan.run_id)}
    assert {k for k, v in states.items() if v == "pending"} == expect_rerun
    reused = set(MATRIX_STEPS) - expect_rerun
    for rid_ in reused:
        step = store.load_step(plan.run_id, rid_)
        assert step.state == "done" and step.stage == "VERIFIED" and step.run_ok == 1
        assert step.qa[0]["check"] == "reused_from_parent"
        assert not store.commands_of(plan.run_id, rid_)
    # 父 run 原封不动
    assert store.get_run(parent)["status"] == "done"
    assert all(s.state == "done" for s in store.load_steps(parent))
    assert_store_consistent(store)


def test_fork_science_midchain_executes_only_downstream(fork_base):
    """执行链中 fork(第 4 步 science 改动):只有 4/5/6 真正启动,
    1-3 的产物经祖先链解析(输入契约不缺),fork run 跑到 done。"""
    store, driver, parent = fork_base
    plan = fork_run(store, parent, registry=driver.registry,
                    changes={4: {"params": {"range_looks": 8}}}, probe=empty_probe())
    events = asyncio.run(collect(driver.execute("s-base", run_id=plan.run_id)))
    assert_event_invariants(events, terminal="done")
    assert {e["stepId"] for e in events if e["t"] == "step.start"} == {4, 5, 6}
    assert command_counts(store, plan.run_id) == {1: 0, 2: 0, 3: 0, 4: 1, 5: 1, 6: 1}
    # 复用步骤的产物沿 parent_run_id 祖先链解析
    art = store.find_artifact(plan.run_id, "coreg")
    assert art is not None and art["run_id"] == parent
    assert store.get_run(plan.run_id)["status"] == "done"
    assert_store_consistent(store)


def test_fork_presentation_change_reruns_single_step_only(fork_base):
    """presentation 参数(第 5 步 render_note)fork:eval_hash 不传播 ——
    待重跑集合恰为 {5},下游第 6 步照常复用;执行后只多一条命令。"""
    store, driver, parent = fork_base
    plan = fork_run(store, parent, registry=driver.registry,
                    changes={5: {"params": {"render_note": "journal"}}},
                    probe=empty_probe())
    states = {s.step_id: s.state for s in store.load_steps(plan.run_id)}
    assert {k for k, v in states.items() if v == "pending"} == {5}
    assert states[6] == "done"  # 呈现改动不传播下游

    events = asyncio.run(collect(driver.execute("s-base", run_id=plan.run_id)))
    assert_event_invariants(events, terminal="done")
    assert {e["stepId"] for e in events if e["t"] == "step.start"} == {5}
    assert command_counts(store, plan.run_id) == {1: 0, 2: 0, 3: 0, 4: 0, 5: 1, 6: 0}
    assert store.get_run(plan.run_id)["status"] == "done"
    assert_store_consistent(store)


# ===========================================================================
# 5. 随机化冒烟:seed=0 的随机干预序列,终态库自洽
# ===========================================================================

def test_random_intervention_sequence_smoke(store, workspace):
    """random.Random(0) 生成 20 步操作(执行/各类干预/控制位/fork 交错),
    对每条执行流套事件不变量,收尾断言整库自洽 —— 覆盖组合爆炸的长尾。"""
    rng = random.Random(0)
    driver = make_driver(store, workspace)
    session = "s-smoke"
    plan_run(driver, session)

    science_pool = {1: ("scenes", (3, 5, 9)), 3: ("esd_coherence_threshold", (0.7, 0.9)),
                    4: ("range_looks", (4, 8, 12)), 5: ("alpha", (0.3, 0.5, 0.7)),
                    6: ("min_coherence", (0.2, 0.3))}
    method_pool = {2: ("dem_srtm", "dem_copernicus", "dem_local"),
                   5: ("none", "boxcar", "goldstein"),
                   6: ("icu", "snaphu_mcf")}
    ops = ("execute", "steer_params", "steer_method", "skip", "reset",
           "kill", "pause", "play", "next_run_method", "cancel_bit", "fork")
    weights = (30, 12, 8, 6, 8, 6, 5, 5, 5, 5, 10)

    def latest() -> str:
        return store.latest_run(session)["run_id"]

    executed = 0
    forks = 0
    trail: list[str] = []  # 失败时的复现线索
    for _ in range(20):
        op = rng.choices(ops, weights=weights)[0]
        rid = latest()
        trail.append(op)
        if op == "execute":
            events = asyncio.run(collect(driver.execute(session)))
            assert_event_invariants(events)
            executed += 1
        elif op == "steer_params":
            sid = rng.choice(tuple(science_pool))
            key, values = science_pool[sid]
            store.push_action(scope="step", target=str(sid), action="SET_PARAMS",
                              payload={"params": {key: rng.choice(values)}},
                              deliver_as="steer", run_id=rid)
        elif op == "steer_method":
            sid = rng.choice(tuple(method_pool))
            store.push_action(scope="step", target=str(sid), action="SET_METHOD",
                              payload={"method": rng.choice(method_pool[sid])},
                              deliver_as="steer", run_id=rid)
        elif op == "skip":
            store.push_action(scope="step", target=str(rng.choice(MATRIX_STEPS)),
                              action="SKIP", deliver_as="steer", run_id=rid)
        elif op == "reset":
            store.push_action(scope="step", target=str(rng.choice(MATRIX_STEPS)),
                              action="RESET", deliver_as="steer", run_id=rid)
        elif op == "kill":
            store.push_action(scope="run", target=rid, action="KILL",
                              deliver_as="steer", run_id=rid)
        elif op == "pause":
            store.push_action(scope="run", target=rid, action="PAUSE",
                              deliver_as="steer", run_id=rid)
        elif op == "play":
            store.push_action(scope="run", target=rid, action="PLAY",
                              deliver_as="steer", run_id=rid)
        elif op == "next_run_method":
            sid = rng.choice(tuple(method_pool))
            store.push_action(scope="step", target=str(sid), action="SET_METHOD",
                              payload={"method": rng.choice(method_pool[sid])},
                              deliver_as="next_run", run_id=rid)
        elif op == "cancel_bit":
            driver.request_cancel(rid)
        elif op == "fork":
            if forks < 2:  # 上限防随机序列爆 run 数(时间纪律)
                sid = rng.choice(tuple(science_pool))
                key, values = science_pool[sid]
                fork_run(store, rid, registry=driver.registry,
                         changes={sid: {"params": {key: rng.choice(values)}}},
                         probe=empty_probe())
                forks += 1

    # 至少两轮真实执行,保证随机序列确实驱动过状态机
    while executed < 2:
        trail.append("execute(final)")
        events = asyncio.run(collect(driver.execute(session)))
        assert_event_invariants(events)
        executed += 1

    try:
        assert_store_consistent(store)
    except AssertionError:
        raise AssertionError(f"随机序列(seed=0)终态不自洽,操作轨迹:{trail}")


# ===========================================================================
# 6. 语义升级(2026-08-12 决策):每项正反例
#    决策一:末步执行期间排队的 steer 在收尾前有最后一次消费点,不许静默遗留
#            (可应用的应用并标脏;KILL/PAUSE/PLAY 已无作用对象 → 消费 + note)
#    决策二:done 且无待跑步骤的 run 重入 → 不空转收尾(不重写 provenance/
#            run.sh、不重发 result/report),一条 note 即返回;排队干预先消费
#            (不吞),造出待跑步骤则本回合直接重跑;显式 step_ids 仍允许执行
#    决策三:回合以 done 收尾但存在 stale/pending 步骤 → 维持 status=done
#            (不引入新状态),result 前 note 列出待重跑步骤、report 后再补
#            一条提示;provenance 照常导出
# ===========================================================================

def test_steer_during_last_step_consumed_before_finale(store, workspace):
    """决策一正例:最后一步(第 6 步)执行期间排队的 SET_PARAMS 已无步间检查点
    可消费 —— 旧语义静默遗留到该 run 的下一次 execute;新语义在收尾前的最后
    消费点应用(标脏第 6 步),note 明示待重跑,续跑恰好只重跑第 6 步。"""
    driver = make_driver(store, workspace)
    run_id = plan_run(driver, "s1")

    fired: dict = {}
    events = asyncio.run(drive(
        driver.execute("s1"),
        on_event=lambda e: (e["t"] == "step.start" and e["stepId"] == 6 and push_once(
            fired, "steer", lambda: store.push_action(
                scope="step", target="6", action="SET_PARAMS",
                payload={"params": {"min_coherence": 0.4}},
                deliver_as="steer", run_id=run_id)))))
    assert_event_invariants(events, terminal="done")
    # 核心:动作在本回合被消费(旧语义:悬挂到下一次 execute 才生效)
    assert not store.due_actions("steer", run_id=run_id)
    # 干预事件出现在最后一步 step.end 之后(收尾消费点)
    idx_end6 = max(i for i, e in enumerate(events)
                   if e["t"] == "step.end" and e["stepId"] == 6)
    itv = [i for i, e in enumerate(events) if e["t"] == "intervention"]
    assert len(itv) == 1 and itv[0] > idx_end6, "末步 steer 必须在收尾前消费"
    # 参数已应用、第 6 步标脏;run 维持 done(决策三:不引入新状态)
    step6 = store.load_step(run_id, 6)
    assert step6.params["min_coherence"] == 0.4 and step6.state == "stale"
    assert store.get_run(run_id)["status"] == "done"
    assert any(e["t"] == "note" and "待重跑" in e["text"] for e in events)

    # 续跑闭环:恰好只重跑第 6 步
    events2 = asyncio.run(collect(driver.execute("s1")))
    assert_event_invariants(events2, terminal="done")
    assert {e["stepId"] for e in events2 if e["t"] == "step.start"} == {6}
    assert command_counts(store, run_id) == {1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 2}
    assert all(s.state == "done" for s in store.load_steps(run_id))
    assert_store_consistent(store)


def test_kill_after_last_step_completion_consumed_as_noop(store, workspace):
    """决策一正例(KILL 分支):KILL 在最后一步完成后到达(错过泵内即时消费
    窗口)—— 已无可取消对象:消费 + note 说明,不落 control 位,run 照常以
    done 收尾;下一次 execute 不被遗留的 KILL 误拦成 interrupted。"""
    driver = make_driver(store, workspace)
    run_id = plan_run(driver, "s1")

    fired: dict = {}
    events = asyncio.run(drive(
        driver.execute("s1"),
        on_event=lambda e: (e["t"] == "step.end" and e["stepId"] == 6 and push_once(
            fired, "kill", lambda: store.push_action(
                scope="run", target=run_id, action="KILL",
                deliver_as="steer", run_id=run_id)))))
    assert_event_invariants(events, terminal="done")
    assert not store.due_actions("steer", run_id=run_id)  # 已消费,不遗留
    assert any(e["t"] == "note" and "KILL" in e["text"] and "无可作用对象" in e["text"]
               for e in events)
    run = store.get_run(run_id)
    assert run["status"] == "done"      # 步骤都完成了:不是 interrupted
    assert run["control"] == "running"  # 不落 control 位
    assert all(s.state == "done" for s in store.load_steps(run_id))

    # 反向守护:重入不被旧 KILL 拦截(决策二:空转防护 note,零终态重发)
    events2 = asyncio.run(collect(driver.execute("s1")))
    assert store.get_run(run_id)["status"] == "done"
    assert not any(e["t"] in ("result", "report") for e in events2)
    assert_store_consistent(store)


def test_pause_after_last_step_completion_not_paused(store, workspace):
    """决策一正例(PAUSE 分支):最后一步完成后到达的 PAUSE 已无可暂停对象 ——
    消费 + note,run 收尾 done 而非 paused(不把完成的 run 翻回暂停态)。"""
    driver = make_driver(store, workspace)
    run_id = plan_run(driver, "s1")

    fired: dict = {}
    events = asyncio.run(drive(
        driver.execute("s1"),
        on_event=lambda e: (e["t"] == "step.end" and e["stepId"] == 6 and push_once(
            fired, "pause", lambda: store.push_action(
                scope="run", target=run_id, action="PAUSE",
                deliver_as="steer", run_id=run_id)))))
    assert_event_invariants(events, terminal="done")
    assert not store.due_actions("steer", run_id=run_id)
    assert any(e["t"] == "note" and "PAUSE" in e["text"] for e in events)
    assert store.get_run(run_id)["status"] == "done"  # 不是 paused
    assert_store_consistent(store)


def test_execute_done_run_again_is_note_only_no_replay(store, workspace):
    """决策二正例:done 且无待跑步骤的 run 重入 → 一条 note 即返回:
    不重发 result/report(前端不重复渲染终态卡)、不重写 provenance/run.sh、
    零步骤/命令级新效果;反例对照:首回合(有待跑步骤)照常完整收尾。"""
    driver = make_driver(store, workspace)
    run_id = plan_run(driver, "s1")
    events1 = asyncio.run(collect(driver.execute("s1")))
    assert_event_invariants(events1, terminal="done")  # 反例面:首回合完整收尾
    prov_before = (workspace / "provenance.json").read_bytes()
    sh_before = (workspace / "run.sh").read_bytes()

    events2 = asyncio.run(collect(driver.execute("s1")))
    assert_event_invariants(events2)
    kinds = {e["t"] for e in events2}
    assert "result" not in kinds and "report" not in kinds
    assert "step.start" not in kinds
    assert any(e["t"] == "note" and "已完成" in e["text"] and "重跑" in e["text"]
               for e in events2)
    # 账本零重写(字节级),命令账本零增长
    assert (workspace / "provenance.json").read_bytes() == prov_before
    assert (workspace / "run.sh").read_bytes() == sh_before
    assert command_counts(store, run_id) == {sid: 1 for sid in MATRIX_STEPS}
    assert store.get_run(run_id)["status"] == "done"
    assert_store_consistent(store)


def test_execute_done_run_explicit_step_ids_still_runs(store, workspace):
    """决策二反例(豁免):显式 step_ids 指定时仍允许执行(重验证场景)——
    不走「已完成」短路,回合正常收尾(result/report 恰一次);已 VERIFIED 的
    步骤由执行器幂等守卫快速走查,不重复启动作业(命令账本不增长)。"""
    driver = make_driver(store, workspace)
    run_id = plan_run(driver, "s1")
    asyncio.run(collect(driver.execute("s1")))

    events = asyncio.run(collect(driver.execute("s1", run_id=run_id, step_ids=[6])))
    assert_event_invariants(events, terminal="done")
    assert [e["stepId"] for e in events if e["t"] == "step.start"] == [6]
    assert command_counts(store, run_id)[6] == 1  # 重验证 ≠ 重启动
    assert store.get_run(run_id)["status"] == "done"
    assert_store_consistent(store)


def test_execute_done_run_with_queued_steer_applies_and_reruns(store, workspace):
    """决策二正例(不吞干预):done run 上排队的 SET_PARAMS(science,第 4 步)
    在重入时入口消费:标脏 4-6 并在本回合直接重跑 —— 不是「note 后让用户再
    点一次」;动作不悬挂,账本闭合。"""
    driver = make_driver(store, workspace)
    run_id = plan_run(driver, "s1")
    asyncio.run(collect(driver.execute("s1")))
    store.push_action(scope="step", target="4", action="SET_PARAMS",
                      payload={"params": {"range_looks": 12}},
                      deliver_as="steer", run_id=run_id)

    events = asyncio.run(collect(driver.execute("s1")))
    assert_event_invariants(events, terminal="done")
    assert not store.due_actions("steer", run_id=run_id)
    assert any(e["t"] == "intervention" for e in events)
    assert {e["stepId"] for e in events if e["t"] == "step.start"} == {4, 5, 6}
    assert store.load_step(run_id, 4).params["range_looks"] == 12
    assert command_counts(store, run_id) == {1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 2}
    assert all(s.state == "done" for s in store.load_steps(run_id))
    assert_store_consistent(store)


def test_execute_done_run_with_queued_follow_up_not_stranded(store, workspace):
    """决策二正例(follow_up 不悬挂):run 已 done 后才入队的 follow_up 错过了
    它的天然消费点(run 收尾)—— 重入时入口消费并直接重跑,不被「已完成」
    短路吞掉(旧语义会在空转收尾里消费它,新语义必须显式接住)。"""
    driver = make_driver(store, workspace)
    run_id = plan_run(driver, "s1")
    asyncio.run(collect(driver.execute("s1")))
    store.push_action(scope="step", target="6", action="RESET",
                      deliver_as="follow_up", run_id=run_id)

    events = asyncio.run(collect(driver.execute("s1")))
    assert_event_invariants(events, terminal="done")
    assert not store.due_actions("follow_up", run_id=run_id)
    assert {e["stepId"] for e in events if e["t"] == "step.start"} == {6}
    assert command_counts(store, run_id)[6] == 2
    assert all(s.state == "done" for s in store.load_steps(run_id))
    assert_store_consistent(store)


def test_done_with_stale_finale_notes_list_waiting_steps(store, workspace):
    """决策三正例:执行中对已完成第 1 步 SET_PARAMS(science)→ 本回合以 done
    收尾但 1-3 待重跑:result 前的 note 明确列出待重跑步骤,report 之后恰好
    再跟提示 note;run 状态仍是 done(不引入新状态),provenance 照常导出。"""
    driver = make_driver(store, workspace)
    run_id = plan_run(driver, "s1")

    fired: dict = {}
    events = asyncio.run(drive(
        driver.execute("s1"),
        on_event=lambda e: (e["t"] == "step.start" and e["stepId"] == 3 and push_once(
            fired, "steer", lambda: store.push_action(
                scope="step", target="1", action="SET_PARAMS",
                payload={"params": {"scenes": 9}},
                deliver_as="steer", run_id=run_id)))))
    assert_event_invariants(events, terminal="done")
    kinds = [e["t"] for e in events]
    i_result, i_report = kinds.index("result"), kinds.index("report")
    # result 之前:列出待重跑步骤(1、2、3 = 干预时已 done 又被标脏的)
    pre = [e for e in events[:i_result] if e["t"] == "note" and "待重跑" in e["text"]]
    assert pre and "1、2、3" in pre[-1]["text"]
    # report 之后:只有提示 note,且确有一条提到待重跑
    tail = events[i_report + 1:]
    assert tail and all(e["t"] == "note" for e in tail)
    assert any("待重跑" in e["text"] for e in tail)
    assert store.get_run(run_id)["status"] == "done"
    assert (workspace / "provenance.json").exists()
    states = {s.step_id: s.state for s in store.load_steps(run_id)}
    assert states[1] == states[2] == states[3] == "stale"
    assert_store_consistent(store)


def test_clean_done_finale_has_no_waiting_notes(store, workspace):
    """决策三反例:无待重跑步骤的干净收尾 —— report 仍是最后一个事件,
    全程不出现「待重跑」提示(不给前端造 note 噪声)。"""
    driver = make_driver(store, workspace)
    run_id = plan_run(driver, "s1")
    events = asyncio.run(collect(driver.execute("s1")))
    assert_event_invariants(events, terminal="done")
    assert events[-1]["t"] == "report"
    assert not any(e["t"] == "note" and "待重跑" in e["text"] for e in events)
    assert store.get_run(run_id)["status"] == "done"
    assert_store_consistent(store)
