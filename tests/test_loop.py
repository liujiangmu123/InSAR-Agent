"""Phase 4 验收(loop):端到端回合、干预消费、暂停/取消、brain 拔除守护、事件隔离。"""

from __future__ import annotations

import asyncio

from insar_agent.brain.facade import Brain
from insar_agent.loop.driver import Driver
from insar_agent.loop.events import EventBus
from insar_agent.runtime.probe import ProbeResult


def empty_probe():
    return ProbeResult(engines={"isce2": None, "mintpy": None, "snaphu": None, "gdal": None,
                                "snap": None, "pystamps": None, "pyaps": None},
                       credentials={"earthdata": False, "cds": False, "gacos": False},
                       disk_free_gb=100.0, cpu_count=8)


def make_driver(store, workspace, **kw) -> Driver:
    defaults = dict(workspace=workspace, probe=empty_probe(), poll=0.05,
                    startup_grace=15.0, allow_simulated=True, brain=Brain(None))
    defaults.update(kw)
    return Driver(store, **defaults)


async def collect(agen) -> list[dict]:
    return [e async for e in agen]


def test_turn_produces_plan_and_candidates(store, workspace):
    driver = make_driver(store, workspace)
    events = asyncio.run(collect(driver.turn("s1", "Ridgecrest 地震同震形变分析")))
    kinds = [e["t"] for e in events]
    assert "thinking" in kinds and "plan" in kinds and "candidates" in kinds
    # 模拟模式横幅(引擎缺失必须显式告知)
    assert any(e["t"] == "note" and "模拟" in e["text"] for e in events)
    # 会话留痕
    history = store.chat_history("s1")
    assert history[0]["role"] == "user" and history[-1]["role"] == "agent"


def test_turn_unknown_intent_asks_form(store, workspace):
    driver = make_driver(store, workspace)
    events = asyncio.run(collect(driver.turn("s1", "随便帮我搞一下")))
    assert events[-1]["t"] == "ask"  # 转表单,不猜(§3.5 降级)


def test_brain_removed_full_pipeline_still_works(store, workspace):
    """DESIGN.md:233 守护测试:拔掉 brain 整层,手动模式仍跑通全链。"""
    driver = make_driver(store, workspace, brain=Brain(None))
    asyncio.run(collect(driver.turn("s1", "玉树冻土形变")))
    run = store.latest_run("s1")
    assert run is not None

    events = asyncio.run(collect(driver.execute("s1")))
    kinds = [e["t"] for e in events]
    assert "result" in kinds and "report" in kinds
    assert store.get_run(run["run_id"])["status"] == "done"
    steps = store.load_steps(run["run_id"])
    assert all(s.state == "done" for s in steps)
    # 账本与等价命令已导出
    assert (workspace / "provenance.json").exists()
    assert (workspace / "run.sh").exists()


def test_execute_consumes_steer_between_steps(store, workspace):
    driver = make_driver(store, workspace)
    asyncio.run(collect(driver.turn("s1", "Ridgecrest 地震")))
    run = store.latest_run("s1")

    # 排队:第 9 步换 exponential(steer,在步骤间生效)
    store.push_action(scope="step", target="9", action="SET_METHOD",
                      payload={"method": "exponential"}, deliver_as="steer",
                      run_id=run["run_id"])
    events = asyncio.run(collect(driver.execute("s1")))
    interventions = [e for e in events if e["t"] == "intervention"]
    assert interventions and "exponential" in interventions[0]["text"]
    assert store.load_step(run["run_id"], 9).method == "exponential"
    assert store.get_run(run["run_id"])["status"] == "done"


def test_pause_stops_after_current_step(store, workspace):
    driver = make_driver(store, workspace)
    asyncio.run(collect(driver.turn("s1", "Ridgecrest 地震")))
    run = store.latest_run("s1")
    store.push_action(scope="run", target=run["run_id"], action="PAUSE",
                      deliver_as="steer", run_id=run["run_id"])

    events = asyncio.run(collect(driver.execute("s1")))
    assert any(e["t"] == "note" and "暂停" in e["text"] for e in events)
    assert store.get_run(run["run_id"])["status"] == "paused"
    states = [s.state for s in store.load_steps(run["run_id"])]
    assert "pending" in states  # 没跑完

    # PLAY 后续跑到底
    store.set_run_status(run["run_id"], "running")
    events2 = asyncio.run(collect(driver.execute("s1")))
    assert any(e["t"] == "result" for e in events2)
    assert store.get_run(run["run_id"])["status"] == "done"


def test_kill_action_interrupts_run(store, workspace):
    driver = make_driver(store, workspace)
    asyncio.run(collect(driver.turn("s1", "Ridgecrest 地震")))
    run = store.latest_run("s1")

    async def scenario():
        agen = driver.execute("s1")
        events = []
        async for e in agen:
            events.append(e)
            if e["t"] == "step.start" and e["stepId"] >= 2:
                store.push_action(scope="run", target=run["run_id"],
                                  action="KILL", deliver_as="steer",
                                  run_id=run["run_id"])
        return events

    events = asyncio.run(scenario())
    assert store.get_run(run["run_id"])["status"] == "interrupted"
    states = {s.step_id: s.state for s in store.load_steps(run["run_id"])}
    assert states[1] == "done"  # 已完成的保留
    assert any(v in ("interrupted", "pending") for v in states.values())

    # 续跑:reset 已由 execute 处理(interrupted 步骤自动复位)
    driver2 = make_driver(store, workspace)
    events2 = asyncio.run(collect(driver2.execute("s1")))
    assert any(e["t"] == "result" for e in events2)


def test_event_bus_isolates_bad_listener(store, workspace):
    """absorb-E8:监听器崩溃不影响主循环。"""
    bus = EventBus()
    q = bus.subscribe()
    bus.publish({"t": "note", "text": "ok"})
    assert q.get_nowait()["t"] == "note"
    # 队列塞满也不阻塞发布方
    small = EventBus(maxsize=2)
    q2 = small.subscribe()
    for i in range(10):
        small.publish({"t": "note", "i": i})
    assert q2.qsize() <= 2  # 丢旧保新,发布方无感
