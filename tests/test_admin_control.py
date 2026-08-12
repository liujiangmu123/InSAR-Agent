"""E3(取消 control 位)+ E6(外部终结协议)验收。

E3(pi harness「abort 是 control 位不是状态」,absorb-E3):
  - 取消意图持久化:request_cancel 落盘 runs.control;服务重启(新 driver
    实例,内存 token 全丢)后 resume 读到 cancel_requested → 不启动新步骤,
    run 收尾 interrupted,control 复位(意图已兑现);
  - 已在途的作业照常结算(不抢杀),只禁止新效果;
  - 旧库无 control 列 → db.py PRAGMA 检查 + ALTER 迁移。

E6(pi harness「外部终结协议」,absorb-E6):
  - 僵死步骤(wrapper 死且无 rc)→ orphaned + 合成结算(-255,对齐 executor);
  - 活步骤 → touch job.cancel(文件契约,不抢杀)+ interrupted,真实 rc 入账;
  - CAS 竞争:行在快照后被活跃执行器推进(如 VERIFIED)→ StageConflict,
    终结者是输者,零效果自停 —— 绝不覆写执行器的推进。
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from insar_agent.api.admin_router import (create_admin_router, terminate_run,
                                          terminate_step)
from insar_agent.brain.facade import Brain
from insar_agent.core.db import Database, _load_schema
from insar_agent.core.store import StageConflict, Store
from insar_agent.loop.driver import Driver
from insar_agent.runtime.jobs import CommandPlan, LocalJobBackend
from insar_agent.runtime.probe import ProbeResult

HASHES = {"task_hash": "t", "args_hash": "a", "local_hash": "l", "eval_hash": "e"}


# ---------------- 工具 ----------------

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


def seed_launched_step(store: Store, workspace: Path, *, run_id="r1", step_id=6) -> Path:
    """造一个「stage=LAUNCHED / state=running」的步骤行(未造作业目录文件)。

    返回约定的作业目录路径;命令意图已落盘(exit_code=NULL)。
    """
    store.create_session("s1", "test")
    if store.get_run(run_id) is None:
        store.create_run(run_id, "s1", workspace=str(workspace))
    store.create_step(run_id, step_id, capability="unwrap", name="解缠",
                      method="snaphu_mcf", params={}, hashes=HASHES)
    job_dir = workspace / ".jobs" / run_id / f"s{step_id:02d}" / "a1"
    cid = store.reserve_command(run_id, step_id, ["python", "job.py"],
                                stdout_path=str(job_dir / "job.log"))
    store.advance(run_id, step_id, "PREPARED", state="running", started_at=time.time())
    store.advance(run_id, step_id, "LAUNCHED", job_dir=str(job_dir), command_id=cid,
                  log_path=str(job_dir / "job.log"))
    store.set_run_status(run_id, "running")
    return job_dir


def fake_dead_job(job_dir: Path) -> None:
    """伪造「wrapper 曾启动但已死」现场(对齐 test_executor 的 orphaned 造法):
    有 pid / hb(将过期)/ log,无 rc。"""
    job_dir.mkdir(parents=True)
    (job_dir / "job.pid").write_text("99999")
    (job_dir / "job.hb").touch()
    (job_dir / "job.log").write_text("partial output\n")


# ---------------- E3:control 位 ----------------

def test_request_cancel_is_idempotent_control_bit(store, workspace):
    seed_launched_step(store, workspace)
    assert store.get_run("r1")["control"] == "running"
    store.request_cancel("r1")
    store.request_cancel("r1")  # 幂等
    assert store.get_run("r1")["control"] == "cancel_requested"
    assert store.cancel_requested("r1") is True
    # status 与 control 分离:置位不改 status(abort 是 control 位不是状态)
    assert store.get_run("r1")["status"] == "running"
    store.clear_cancel("r1")
    assert store.get_run("r1")["control"] == "running"


def test_cancel_intent_survives_restart(store, workspace):
    """E3 核心:重启后取消意图不丢 —— 新 driver 实例 resume 读到
    cancel_requested,不启动任何新步骤,run 直接收尾 interrupted。"""
    driver = make_driver(store, workspace)
    asyncio.run(collect(driver.turn("s1", "Ridgecrest 地震同震形变分析")))
    run_id = store.latest_run("s1")["run_id"]
    store.set_run_status(run_id, "running")  # 模拟:执行中宿主崩溃,库里遗留 running
    driver.request_cancel(run_id)            # 崩溃前用户请求过取消(意图落盘)
    assert store.get_run(run_id)["control"] == "cancel_requested"

    driver2 = make_driver(store, workspace)  # 「重启」:全新进程,内存 token 全丢
    events = asyncio.run(collect(driver2.resume("s1")))
    run_after = store.get_run(run_id)
    assert run_after["status"] == "interrupted"
    assert run_after["control"] == "running"  # 意图已兑现,control 复位
    # 不启动新步骤:所有步骤原地不动,零命令意图 = 零新效果
    steps = store.load_steps(run_id)
    assert steps and all(s.state in ("pending", "skipped") for s in steps)
    assert all(store.command_attempts(run_id, s.step_id) == 0 for s in steps)
    assert not any(e["t"] == "step.start" for e in events)
    assert not any(e["t"] == "reattach" for e in events)  # 取消意图在,不 reattach

    # 意图已消费:显式重跑不再被旧意图拦截,能跑到 done
    asyncio.run(collect(driver2.execute("s1")))
    assert store.get_run(run_id)["status"] == "done"


def test_cancel_settles_inflight_forbids_new_effects(store, workspace):
    """E3 纪律钉死:取消后已在途的作业照常结算(不抢杀,命令绝不留 NULL 账),
    只禁止新效果(后续步骤零启动)。"""
    driver = make_driver(store, workspace)
    asyncio.run(collect(driver.turn("s1", "Ridgecrest 地震同震形变分析")))
    run_id = store.latest_run("s1")["run_id"]

    async def scenario():
        events, fired = [], False
        async for e in driver.execute("s1"):
            events.append(e)
            if e["t"] == "step.start" and not fired:
                fired = True
                driver.request_cancel(run_id)  # 第一步执行中途取消
        return events

    events = asyncio.run(scenario())
    run = store.get_run(run_id)
    assert run["status"] == "interrupted"
    assert run["control"] == "running"  # 意图已兑现
    started = {e["stepId"] for e in events if e["t"] == "step.start"}
    assert len(started) == 1  # 取消后没有任何新步骤启动
    first_sid = started.pop()
    for s in store.load_steps(run_id):
        cmds = store.commands_of(run_id, s.step_id)
        # 在途效果照常结算:凡有命令意图,必有结算(不留 exit_code=NULL 的账)
        assert all(c["exit_code"] is not None for c in cmds)
        if s.step_id != first_sid:
            assert s.state in ("pending", "skipped") and not cmds  # 禁止新效果


def test_control_column_migrated_on_legacy_db(tmp_path):
    """旧库无 control 列:Database 打开时 PRAGMA table_info 检查 + ALTER 补齐。"""
    legacy_sql = "\n".join(line for line in _load_schema().splitlines()
                           if "control" not in line)
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(legacy_sql)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    assert "control" not in cols  # 前提成立:确是旧库
    conn.execute("INSERT INTO sessions(session_id,name,created_at) VALUES ('s','s',0)")
    conn.execute("INSERT INTO runs(run_id,session_id,created_at,status,workspace)"
                 " VALUES ('r','s',0,'running','w')")
    conn.commit()
    conn.close()

    db = Database(path)  # 打开旧库触发迁移
    try:
        row = db.query_one("SELECT control FROM runs WHERE run_id='r'")
        assert row["control"] == "running"  # 既有行拿到默认值
        s = Store(db)
        s.request_cancel("r")
        assert s.get_run("r")["control"] == "cancel_requested"
    finally:
        db.close()


# ---------------- E6:外部终结 ----------------

def test_terminate_dead_job_marks_orphaned_with_synthetic_settlement(store, workspace):
    """僵死步骤(wrapper 死且无 rc)→ orphaned + settle_command(-255),
    stage 高水位不被伪造推进;run 收尾 interrupted。"""
    job_dir = seed_launched_step(store, workspace)
    fake_dead_job(job_dir)
    time.sleep(0.6)  # 心跳过期(backend hb_stale=0.5)→ 判死
    backend = LocalJobBackend(hb_stale=0.5)

    report = terminate_run(store, "r1", backend=backend, reason="卡死 2 小时")
    step = store.load_step("r1", 6)
    assert step.state == "orphaned" and step.failure_class == "wsl_orphaned"
    assert step.stage == "LAUNCHED"  # 只标状态,不伪造阶段推进
    assert store.commands_of("r1", 6)[0]["exit_code"] == -255  # 合成结算,账本闭合
    run = store.get_run("r1")
    assert run["status"] == "interrupted"
    assert run["control"] == "running"  # 终结即兑现取消意图
    assert report["steps"] == [{"step_id": 6, "state": "orphaned", "job": "orphaned"}]
    assert (job_dir / "job.log").exists()  # 日志保留(§1.6)


def test_terminate_live_job_cancels_via_file_contract(store, workspace):
    """活步骤:touch job.cancel(不抢杀),wrapper 整组 kill 后写 rc=143,
    终结者把真实 rc 结算入账,步骤标 interrupted。"""
    job_dir = seed_launched_step(store, workspace)
    script = workspace / ".sim" / "live_job.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("import time\nfor i in range(200):\n"
                      "    print(f'line {i}', flush=True)\n    time.sleep(0.1)\n",
                      encoding="utf-8")
    backend = LocalJobBackend(hb_stale=3.0)
    plan = CommandPlan(argv=[sys.executable, "-X", "utf8", str(script)],
                       cwd=str(workspace), env={"PYTHONIOENCODING": "utf-8"}, files={})
    backend.prepare(job_dir, plan)
    backend.launch(job_dir)
    for _ in range(600):  # 等 wrapper 真正起来(负载下 python 启动可达秒级)
        if backend.state(job_dir).kind == "alive":
            break
        time.sleep(0.05)
    else:
        pytest.fail("wrapper 未在 30s 内启动")

    report = terminate_run(store, "r1", backend=backend, reason="运维终止")
    assert (job_dir / "job.cancel").exists()  # 文件契约取消,不是宿主抢杀
    step = store.load_step("r1", 6)
    assert step.state == "interrupted"
    cmd = store.commands_of("r1", 6)[0]
    assert cmd["exit_code"] == 143  # wrapper 写下的真实 rc 已入账(在途效果照常结算)
    assert report["steps"][0] == {"step_id": 6, "state": "interrupted",
                                  "job": "cancelled", "exit_code": 143}
    assert store.get_run("r1")["status"] == "interrupted"


def test_terminate_cas_never_overwrites_executor_progress(store, workspace):
    """E6 核心:终结者拿的是行快照;快照后活跃执行器推进到 VERIFIED →
    CAS(expect_stage)输 → StageConflict,零效果自停,绝不覆写。"""
    job_dir = seed_launched_step(store, workspace)
    job_dir.mkdir(parents=True)
    (job_dir / "job.pid").write_text("12345")
    (job_dir / "job.hb").touch()  # 心跳新鲜 → 判活(走 alive 分支,效果在 CAS 之后)

    snap = store.load_step("r1", 6)  # 终结者的快照:stage=LAUNCHED
    # 快照之后,活跃执行器把该步推进到 VERIFIED(竞争的赢家)
    store.advance("r1", 6, "VERIFIED", state="done", run_ok=1)

    with pytest.raises(StageConflict):
        terminate_step(store, LocalJobBackend(), snap, grace=0.0)
    step = store.load_step("r1", 6)
    assert step.stage == "VERIFIED" and step.state == "done" and step.run_ok == 1  # 未被覆写
    assert not (job_dir / "job.cancel").exists()  # 输者零效果:没碰别人的作业
    assert store.commands_of("r1", 6)[0]["exit_code"] is None  # 命令归执行器结算,没被合成

    # 端点级同一守卫:已终态(done)的步骤根本不进终结集合
    report = terminate_run(store, "r1", backend=LocalJobBackend(), reason="再试一次")
    assert report["steps"] == []  # 无步骤被处置
    assert store.load_step("r1", 6).state == "done"


def test_admin_endpoints_contract(store, workspace):
    """POST /api/admin/terminate 与 GET /api/admin/runs 的 HTTP 契约。"""
    job_dir = seed_launched_step(store, workspace)
    fake_dead_job(job_dir)
    time.sleep(0.6)
    app = FastAPI()
    app.include_router(create_admin_router(store, backend=LocalJobBackend(hb_stale=0.5)))
    client = TestClient(app)

    # 运维视图:终结前 —— running run + 僵死步骤判活结果可见
    view = client.get("/api/admin/runs").json()
    assert len(view) == 1
    assert view[0]["run_id"] == "r1" and view[0]["status"] == "running"
    assert view[0]["control"] == "running"
    assert view[0]["steps"] == {"running": 1}
    assert view[0]["running_steps"] == [{"step_id": 6, "stage": "LAUNCHED",
                                         "job": "orphaned"}]

    # 终结
    resp = client.post("/api/admin/terminate", json={"run_id": "r1", "reason": "hung"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == "r1" and body["status"] == "interrupted"
    assert body["reason"] == "hung"
    assert body["steps"][0]["state"] == "orphaned"

    # 不存在的 run → 404
    assert client.post("/api/admin/terminate",
                       json={"run_id": "nope", "reason": ""}).status_code == 404

    # 运维视图:终结后反映终态;trace 留痕
    view2 = client.get("/api/admin/runs").json()
    assert view2[0]["status"] == "interrupted"
    assert view2[0]["steps"] == {"orphaned": 1}
    assert view2[0]["running_steps"] == []
    assert any(t["revision_trigger"] == "external_terminate"
               for t in store.trace_of("r1"))


def test_terminate_is_idempotent(store, workspace):
    """重复终结无害:第二次调用时步骤已终态,处置集合为空,状态不变。"""
    job_dir = seed_launched_step(store, workspace)
    fake_dead_job(job_dir)
    time.sleep(0.6)
    backend = LocalJobBackend(hb_stale=0.5)
    terminate_run(store, "r1", backend=backend, reason="第一次")
    report2 = terminate_run(store, "r1", backend=backend, reason="第二次")
    assert report2["steps"] == []
    step = store.load_step("r1", 6)
    assert step.state == "orphaned"
    assert store.commands_of("r1", 6)[0]["exit_code"] == -255  # 不重结算
    assert store.get_run("r1")["status"] == "interrupted"
