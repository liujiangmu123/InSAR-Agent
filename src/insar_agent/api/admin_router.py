"""外部终结管理端点(absorb-E6,pi harness「外部终结协议」)。

场景:执行器宿主崩溃/挂死后,run 卡在 running,步骤僵在中间阶段(state=running
但没人跟随)。外部管理者(运维/桌面壳)对这种 run 写「终结事务」:

  - POST /api/admin/terminate {run_id, reason}
      对每个僵在 running 的步骤,按作业目录文件契约判活(§4.7):
        · 活着   → touch job.cancel(wrapper 整组 kill 后自己写 rc,在途效果
                   照常结算,不抢杀)+ 标 interrupted;
        · 死了且无 rc → 标 orphaned(环境事件,非计算失败)+ 合成结算
                   settle_command(-255)(对齐 executor 的 orphaned 语义);
        · 已写 rc 但没人收账 → 补结算真实 rc + 标 interrupted(可续跑)。
      全程 CAS(expect_stage=快照 stage):活跃执行器若仍在推进,终结者是
      竞争的输者(StageConflict),跳过该步 —— 绝不覆写别人的推进(E6 核心)。
      终结前先置 control=cancel_requested(E3):活着的 driver 在步骤之间的
      control 检查就是「条件事务发现后自停」的发现点。

  - GET /api/admin/runs
      运维视图:所有 run 的 status/control/步骤态汇总 + 僵死步骤的作业判活。
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from insar_agent.core.store import StageConflict, StepRow, Store
from insar_agent.runtime.jobs import JobBackend, LocalJobBackend

#: touch job.cancel 后等 wrapper 写 rc 的宽限(秒);超时不结算,留给下次认领
CANCEL_SETTLE_GRACE = 10.0

#: 合成结算的退出码(对齐 executor orphaned 路径:runtime/executor.py)
SYNTHETIC_RC = -255


def terminate_step(store: Store, backend: JobBackend, snap: StepRow, *,
                   grace: float = CANCEL_SETTLE_GRACE, poll: float = 0.2) -> dict:
    """对单个僵死步骤写终结事务。

    snap 是调用方读到的行快照;所有写入都以 expect_stage=snap.stage 做 CAS,
    行在快照后被活跃执行器推进过 → StageConflict 抛给调用方(输者自停)。
    """
    now = time.time()
    duration = max(0.0, now - snap.started_at) if snap.started_at else 0.0
    job_dir = Path(snap.job_dir) if snap.job_dir else None

    if job_dir is None or not job_dir.exists():
        # 从未真正启动(或作业目录已被清):没有在途效果,直接标 interrupted;
        # 未结算的命令意图合成结算,账本闭合
        store.mark_step(snap.run_id, snap.step_id, state="interrupted",
                        expect_stage=snap.stage, ended_at=now)
        if snap.command_id:
            store.settle_command(snap.command_id, exit_code=SYNTHETIC_RC,
                                 duration=duration)
        return {"step_id": snap.step_id, "state": "interrupted", "job": "missing"}

    st = backend.state(job_dir)
    if st.kind == "alive":
        # CAS 先行:赢了才允许产生效果(touch job.cancel);输了立刻 StageConflict,
        # 不去动活跃执行器正在跟随的作业
        store.mark_step(snap.run_id, snap.step_id, state="interrupted",
                        expect_stage=snap.stage, ended_at=now)
        backend.cancel(job_dir)  # 文件契约取消:wrapper 轮询到后整组 kill 并写 rc
        settled_rc: int | None = None
        deadline = time.time() + grace
        while time.time() < deadline:
            st2 = backend.state(job_dir)
            if st2.kind == "finished":
                settled_rc = st2.exit_code
                break
            time.sleep(poll)
        if snap.command_id and settled_rc is not None:
            # 在途效果照常结算:入账的是 wrapper 写下的真实 rc,不是合成值
            store.settle_command(snap.command_id, exit_code=settled_rc,
                                 duration=max(0.0, time.time() - (snap.started_at or now)))
        return {"step_id": snap.step_id, "state": "interrupted",
                "job": "cancelled", "exit_code": settled_rc}

    if st.kind == "finished":
        # 作业已自行结束但执行器没来得及收账:补结算真实 rc,标 interrupted(可续跑)
        store.mark_step(snap.run_id, snap.step_id, state="interrupted",
                        expect_stage=snap.stage, ended_at=now)
        if snap.command_id:
            store.settle_command(
                snap.command_id,
                exit_code=st.exit_code if st.exit_code is not None else -1,
                duration=duration)
        return {"step_id": snap.step_id, "state": "interrupted",
                "job": "finished", "exit_code": st.exit_code}

    # orphaned / unknown:wrapper 死了且没写 rc —— 环境事件,非计算失败(§4.7)
    store.mark_step(snap.run_id, snap.step_id, state="orphaned",
                    failure_class="wsl_orphaned", expect_stage=snap.stage, ended_at=now)
    if snap.command_id:
        store.settle_command(snap.command_id, exit_code=SYNTHETIC_RC, duration=duration)
    return {"step_id": snap.step_id, "state": "orphaned", "job": st.kind}


def terminate_run(store: Store, run_id: str, *, backend: JobBackend,
                  reason: str = "", grace: float = CANCEL_SETTLE_GRACE,
                  poll: float = 0.2) -> dict:
    """外部终结一个挂起/僵死的 run(幂等:重复调用只会跳过已终态的步骤)。"""
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    # 先立 control 位(E3):若还有活着的 driver,它在步骤之间的 control 检查
    # 会发现取消意图并自停 —— 这就是外部终结协议的「发现点」
    store.request_cancel(run_id)

    steps_report: list[dict] = []
    for snap in store.load_steps(run_id):
        if snap.state != "running":
            continue  # pending 无效果不必动;done/failed/interrupted/orphaned 已终态
        try:
            steps_report.append(
                terminate_step(store, backend, snap, grace=grace, poll=poll))
        except StageConflict as exc:
            # 竞争输了:行在快照后被活跃执行器推进 —— 自停,绝不覆写(E6 核心)
            steps_report.append({"step_id": snap.step_id,
                                 "state": "skipped_conflict", "detail": str(exc)})
    store.set_run_status(run_id, "interrupted")
    store.clear_cancel(run_id)  # 终结即兑现取消意图;之后允许显式续跑
    store.append_trace(
        run_id=run_id, phase="admin", revision_trigger="external_terminate",
        observation=f"外部终结(reason={reason or '未给出'}):{steps_report}")
    return {"run_id": run_id, "status": "interrupted", "reason": reason,
            "steps": steps_report}


class TerminateBody(BaseModel):
    run_id: str
    reason: str = ""


def create_admin_router(store: Store, *, backend: JobBackend | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/admin", tags=["admin"])
    job_backend = backend or LocalJobBackend()

    @router.post("/terminate")
    def terminate(body: TerminateBody):
        try:
            return terminate_run(store, body.run_id, backend=job_backend,
                                 reason=body.reason)
        except KeyError:
            raise HTTPException(404, f"run {body.run_id} 不存在")

    @router.get("/runs")
    def runs():
        out = []
        for run in store.list_runs():
            steps = store.load_steps(run["run_id"])
            by_state: dict[str, int] = {}
            for s in steps:
                by_state[s.state] = by_state.get(s.state, 0) + 1
            running = []
            for s in steps:
                if s.state != "running":
                    continue
                jd = Path(s.job_dir) if s.job_dir else None
                job = job_backend.state(jd).kind if jd and jd.exists() else "missing"
                running.append({"step_id": s.step_id, "stage": s.stage, "job": job})
            out.append({
                "run_id": run["run_id"], "session_id": run["session_id"],
                "status": run["status"],
                "control": run.get("control") or "running",
                "created_at": run["created_at"],
                "steps": by_state, "running_steps": running,
            })
        return out

    return router
