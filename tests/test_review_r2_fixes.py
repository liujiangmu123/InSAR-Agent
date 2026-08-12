"""REVIEW-2026-08-12-r2 五项接缝 P1 的回归锁。

修复位置:loop/driver.py(P1-1 显式列表复位 failed、P1-2 空待跑集合防翻绿、
P1-5 租约续租失败自停)、core/store.py + driver 消费点(P1-3 严格 run 归属)、
runtime/executor.py(P1-4 认领比对 argv)。
"""

from __future__ import annotations

import asyncio
import json

from insar_agent.brain.facade import Brain
from insar_agent.loop.driver import Driver
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


def _fail_step(store, run_id: int | str, sid: int) -> None:
    store.mark_step(run_id, sid, state="failed", failure_class="tool_failed")
    store.set_run_status(run_id, "failed")


def test_p1_2_failed_run_rerun_does_not_turn_green(store, workspace):
    """P1-2r2:质量门拦停/失败的 run,默认重跑不得洗成 done。"""
    driver = make_driver(store, workspace)
    asyncio.run(collect(driver.turn("s1", "Ridgecrest 地震")))
    run = store.latest_run("s1")
    asyncio.run(collect(driver.execute("s1")))  # 全链跑完(simulated)
    _fail_step(store, run["run_id"], 9)  # 人工造失败终态

    events = asyncio.run(collect(driver.execute("s1")))
    kinds = [e["t"] for e in events]
    # 不得出现终态事件,run 状态保持 failed
    assert "result" not in kinds and "report" not in kinds
    assert any(e["t"] == "note" and "没有可执行步骤" in e.get("text", "") for e in events)
    assert store.get_run(run["run_id"])["status"] == "failed"
    assert store.load_step(run["run_id"], 9).state == "failed"


def test_p1_1_explicit_failed_step_is_reset_and_rerun(store, workspace):
    """P1-1r2:失败卡「从断点继续」= 显式列表含 failed 步骤 → 复位重跑而非崩溃。"""
    driver = make_driver(store, workspace)
    asyncio.run(collect(driver.turn("s1", "Ridgecrest 地震")))
    run = store.latest_run("s1")
    asyncio.run(collect(driver.execute("s1")))
    _fail_step(store, run["run_id"], 9)

    events = asyncio.run(collect(driver.execute("s1", step_ids=[9, 10, 11])))
    kinds = [e["t"] for e in events]
    assert "result" in kinds and "report" in kinds  # 回合正常收尾,未崩流
    assert store.load_step(run["run_id"], 9).state == "done"
    assert store.get_run(run["run_id"])["status"] == "done"


def test_p1_3_unattributed_action_not_consumed_by_execution(store, workspace):
    """P1-3r2:无 run 归属(NULL)的 steer 不再被任意回合吞掉。"""
    driver = make_driver(store, workspace)
    asyncio.run(collect(driver.turn("s1", "Ridgecrest 地震")))
    run = store.latest_run("s1")
    # 模拟旧数据/直接写库的未定向动作
    store.push_action(scope="run", target=run["run_id"], action="PAUSE",
                      deliver_as="steer")  # 无 run_id
    asyncio.run(collect(driver.execute("s1")))
    # 动作未被消费(严格归属),run 正常完成而非被暂停
    leftover = store.due_actions("steer")
    assert len(leftover) == 1 and leftover[0]["run_id"] is None
    assert store.get_run(run["run_id"])["status"] == "done"


def test_p1_4_claim_requires_same_argv(store, workspace):
    """P1-4r2:崩溃窗口的认领必须比对 argv,配置已变时旧意图关账、新意图重立。

    直接在 store 层构造「未结算旧意图」再走一次执行:执行器发现 argv 不同,
    应合成结算旧行(-255)并 reserve 新行,绝不把旧作业产物入账新配置。
    """
    driver = make_driver(store, workspace)
    asyncio.run(collect(driver.turn("s1", "Ridgecrest 地震")))
    run = store.latest_run("s1")
    # 伪造一条与真实计划 argv 必然不同的未结算意图(旧配置遗留)
    stale_cmd = store.reserve_command(
        run["run_id"], 7, ["python", "OLD_CONFIG.py"],
        cwd=str(workspace), cmd_path=str(workspace / "old_cmd.sh"),
        stdout_path=str(workspace / "old" / "job.log"), attempt=1)

    asyncio.run(collect(driver.execute("s1")))
    cmds = store.commands_of(run["run_id"], 7)
    by_id = {c["id"]: c for c in cmds}
    # 旧意图被合成结算关账(-255),而非被认领
    assert by_id[stale_cmd]["exit_code"] == -255
    # 新意图独立存在且 argv 是真实计划(不是旧配置)
    newer = [c for c in cmds if c["id"] != stale_cmd]
    assert newer and all(json.loads(c["argv"]) != ["python", "OLD_CONFIG.py"] for c in newer)
    assert store.load_step(run["run_id"], 7).state == "done"
