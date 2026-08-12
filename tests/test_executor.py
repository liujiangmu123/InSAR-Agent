"""Phase 1 验收(AGENT-DESIGN §9 Phase 1 + §4.3/§4.4/§4.6/§4.7):

  - 五阶段推进 + 幂等重放
  - 服务重启:reattach 存活作业(不重跑)、认领未结算命令(不双启动)
  - 孤儿检测(wrapper 死亡无 rc)
  - 取消保留部分成果;复位后重跑
  - 无输出超时
  - 产物契约失败显式化
  - 质量门:OK 阈值拦停 / PENDING 阈值只警告
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

from insar_agent.audit.contract import load_contract
from insar_agent.core.fingerprint import step_hashes
from insar_agent.core.store import Store
from insar_agent.engines import default_builder
from insar_agent.registry.capabilities import REGISTRY
from insar_agent.registry.model import ArtifactSpec, Capability, Method, RunOkCheck, Timeouts
from insar_agent.runtime.executor import ExecContext, execute_step
from insar_agent.runtime.jobs import CommandPlan, LocalJobBackend
from insar_agent.runtime.stream import CancelToken

def run_async(coro):
    return asyncio.run(coro)


# ---------------- 工具 ----------------

def make_step(store: Store, cap: Capability, run_id="r1", *, simulated=True,
              method: str | None = None, params: dict | None = None, seed_inputs=True):
    store.create_session("s1", "test")
    if store.get_run(run_id) is None:
        store.create_run(run_id, "s1", workspace="ws", simulated=simulated)
    method = method or cap.default_method
    params = params if params is not None else cap.default_params()
    hashes = step_hashes(
        capability=cap.name, version=cap.version, tool_versions={},
        method=method, params=params, param_kinds=cap.param_kinds(),
        upstream_eval_hashes=[])
    store.create_step(run_id, cap.id, capability=str(cap.id), name=cap.name,
                      method=method, params=params, hashes=hashes, replay=cap.replay)
    if seed_inputs:
        for i, art in enumerate(cap.inputs):
            store.record_artifact(run_id, max(cap.id - 1, 0), art, path=f"data/{art}",
                                  kind="DATA", layout="", policy="stat", fp=f"fp-{art}")
    return run_id


def make_ctx(store: Store, workspace: Path, registry: dict[int, Capability], *,
             builder=default_builder, **overrides) -> ExecContext:
    defaults = dict(poll=0.05, startup_grace=15.0, cancel_grace=15.0)
    defaults.update(overrides)
    return ExecContext(
        store=store, backend=LocalJobBackend(hb_stale=3.0), workspace=workspace,
        registry=registry, builder=builder, contract=load_contract(), **defaults)


def script_builder(script_body: str):
    """把给定 Python 脚本体包装成 CommandPlan 构建器(测试专用)。"""

    def build(*, cap, method, params, run, workspace):
        header = "import json, os, sys, time\nfrom pathlib import Path\nWS = Path('.').resolve()\n"
        return CommandPlan(
            argv=[sys.executable, "-X", "utf8", ".sim/test_step.py"],
            cwd=str(workspace), env={"PYTHONIOENCODING": "utf-8"},
            files={".sim/test_step.py": header + script_body})

    return build


def custom_cap(step_id=6, **kw) -> Capability:
    base = dict(
        id=step_id, name="测试步", deps=(), methods=(Method("m1", "m1", "-"),),
        default_method="m1", artifacts=(), run_ok=(RunOkCheck("exit_code", equals=0),),
        timeouts=Timeouts(idle=30, total=60))
    base.update(kw)
    return Capability(**base)


# ---------------- 幂等 & 五阶段 ----------------

def test_happy_path_five_stages(store, workspace):
    cap = REGISTRY[6]
    make_step(store, cap)
    events: list[dict] = []
    ctx = make_ctx(store, workspace, REGISTRY, emit=events.append)

    result = run_async(execute_step(ctx, "r1", 6))
    assert result.outcome == "done"
    step = store.load_step("r1", 6)
    assert step.stage == "VERIFIED" and step.state == "done" and step.run_ok == 1

    # 产物已记录且带三段编码指纹(absorb-M)与记录格式版本
    arts = {a["art_id"]: a for a in store.artifacts_of("r1", 6)}
    assert "unw" in arts and arts["unw"]["fp"].startswith("stat:v1:")
    assert len(arts["unw"]["fp"].split(":", 2)[2]) == 64
    assert arts["unw"]["record_version"] == 1
    # 命令两段式:意图已结算
    cmds = store.commands_of("r1", 6)
    assert len(cmds) == 1 and cmds[0]["exit_code"] == 0 and cmds[0]["duration"] > 0
    # 等价裸命令脚本存在(§4.7 副产品)
    assert Path(step.job_dir, "cmd.sh").exists()
    # 日志流出且带 simulated 标记
    logs = [e for e in events if e["t"] == "tool.log"]
    assert logs and any("[simulated]" in e["line"] for e in logs)
    # 日志偏移已持久化(reattach 依据)
    assert step.log_offset > 0


def test_idempotent_second_execute(store, workspace):
    cap = REGISTRY[6]
    make_step(store, cap)
    ctx = make_ctx(store, workspace, REGISTRY)
    r1 = run_async(execute_step(ctx, "r1", 6))
    assert r1.outcome == "done"
    job_dir_1 = store.load_step("r1", 6).job_dir

    r2 = run_async(execute_step(ctx, "r1", 6))  # 全部守卫跳过
    assert r2.outcome == "done"
    assert store.command_attempts("r1", 6) == 1  # 没有第二次启动
    assert store.load_step("r1", 6).job_dir == job_dir_1


# ---------------- 恢复:reattach / 认领 ----------------

LONG_JOB = """
for i in range(40):
    print(f"[job] line {i}", flush=True)
    time.sleep(0.1)
(WS / "data").mkdir(parents=True, exist_ok=True)
(WS / "data" / "out.dat").write_bytes(b"OK")
print("[job] done", flush=True)
"""


def _long_cap():
    return custom_cap(
        artifacts=(ArtifactSpec("out", ("data/out.dat",), policy="stat"),),
        run_ok=(RunOkCheck("exit_code", equals=0), RunOkCheck("artifact_exists", id="out")))


def test_reattach_after_service_restart(store, workspace):
    """§9 Phase 1 验收:服务重启但子进程存活时能 reattach(不重跑)。"""
    cap = _long_cap()
    registry = {cap.id: cap}
    make_step(store, cap)
    ctx = make_ctx(store, workspace, registry, builder=script_builder(LONG_JOB))

    async def scenario():
        task = asyncio.create_task(execute_step(ctx, "r1", cap.id))
        # 等作业真正跑起来(有日志偏移)再"杀死服务"
        for _ in range(200):
            await asyncio.sleep(0.05)
            step = store.load_step("r1", cap.id)
            if step.stage == "LAUNCHED" and step.log_offset > 0:
                break
        task.cancel()  # 模拟宿主进程死亡 —— detached wrapper 继续运行
        try:
            await task
        except asyncio.CancelledError:
            pass
        offset_at_crash = store.load_step("r1", cap.id).log_offset
        assert offset_at_crash > 0

        # “重启后”恢复:同一 step 重新执行 → 走 RUNNING 守卫接回日志
        result = await execute_step(make_ctx(store, workspace, registry,
                                             builder=script_builder(LONG_JOB)),
                                    "r1", cap.id)
        return offset_at_crash, result

    offset_at_crash, result = run_async(scenario())
    assert result.outcome == "done"
    step = store.load_step("r1", cap.id)
    assert step.log_offset > offset_at_crash  # 从断点续读,不清零
    assert store.command_attempts("r1", cap.id) == 1  # 绝没有第二次启动


def test_claim_launched_job_before_advance(store, workspace):
    """崩溃在 launch 之后、advance(LAUNCHED) 之前:恢复认领既有作业,不双启动。"""
    cap = _long_cap()
    registry = {cap.id: cap}
    make_step(store, cap)
    ctx = make_ctx(store, workspace, registry, builder=script_builder(LONG_JOB))

    # 手工重放 LAUNCHED 的前半段(意图落盘 + 启动),然后"崩溃"
    plan = ctx.builder(cap=cap, method="m1", params={}, run={}, workspace=workspace)
    from insar_agent.runtime.render import render_plan_files
    render_plan_files(workspace, plan)
    store.advance("r1", cap.id, "PREPARED", state="running", started_at=time.time())
    job_dir = workspace / ".jobs" / "r1" / f"s{cap.id:02d}" / "a1"
    store.reserve_command("r1", cap.id, plan.argv, cwd=plan.cwd,
                          cmd_path=str(job_dir / "cmd.sh"),
                          stdout_path=str(job_dir / "job.log"))
    ctx.backend.prepare(job_dir, plan)
    ctx.backend.launch(job_dir)
    # 轮询等 wrapper 真正起来(负载下 python 启动可达秒级,固定 sleep 会
    # 在「pid 已写、心跳未建」窗口误入 orphaned)—— 认领场景的前提本就是
    # 「作业确实已在跑」
    for _ in range(600):
        if ctx.backend.state(job_dir).kind in ("alive", "finished"):
            break
        time.sleep(0.05)
    else:
        pytest.fail("wrapper 未在 30s 内启动")

    result = run_async(execute_step(ctx, "r1", cap.id))
    assert result.outcome == "done"
    assert store.command_attempts("r1", cap.id) == 1  # 认领,未新增命令行


# ---------------- 孤儿 / 取消 / 超时 ----------------

def test_orphaned_wrapper(store, workspace):
    cap = _long_cap()
    registry = {cap.id: cap}
    make_step(store, cap)
    backend = LocalJobBackend(hb_stale=0.5)
    ctx = make_ctx(store, workspace, registry, builder=script_builder(LONG_JOB))
    ctx.backend = backend

    # 伪造「wrapper 曾启动但已死」现场:有 pid/hb(过期)/log,无 rc
    job_dir = workspace / ".jobs" / "r1" / f"s{cap.id:02d}" / "a1"
    job_dir.mkdir(parents=True)
    (job_dir / "job.pid").write_text("99999")
    (job_dir / "job.hb").touch()
    (job_dir / "job.log").write_text("partial output\n")
    cid = store.reserve_command("r1", cap.id, ["python"], stdout_path=str(job_dir / "job.log"))
    store.advance("r1", cap.id, "PREPARED", state="running", started_at=time.time())
    store.advance("r1", cap.id, "LAUNCHED", job_dir=str(job_dir), command_id=cid,
                  log_path=str(job_dir / "job.log"))
    time.sleep(0.6)  # 心跳过期

    result = run_async(execute_step(ctx, "r1", cap.id))
    assert result.outcome == "orphaned"
    step = store.load_step("r1", cap.id)
    assert step.state == "orphaned" and step.failure_class == "wsl_orphaned"
    assert (job_dir / "job.log").exists()  # 日志保留(§1.6)
    assert store.commands_of("r1", cap.id)[0]["exit_code"] == -255  # 合成结算


def test_cancel_preserves_partial_then_rerun(store, workspace):
    cap = _long_cap()
    registry = {cap.id: cap}
    make_step(store, cap)
    ctx = make_ctx(store, workspace, registry, builder=script_builder(LONG_JOB))
    token = CancelToken()

    async def scenario():
        task = asyncio.create_task(execute_step(ctx, "r1", cap.id, token))
        for _ in range(200):
            await asyncio.sleep(0.05)
            if store.load_step("r1", cap.id).log_offset > 0:
                break
        token.cancel()
        return await task

    result = run_async(scenario())
    assert result.outcome == "interrupted"
    step = store.load_step("r1", cap.id)
    assert step.state == "interrupted" and step.log_offset > 0
    assert store.commands_of("r1", cap.id)[0]["exit_code"] == 143  # wrapper 整组终止

    # 复位重跑(driver 语义):新 attempt,跑到完成
    store.reset_step_for_rerun("r1", cap.id)
    result2 = run_async(execute_step(ctx, "r1", cap.id))
    assert result2.outcome == "done"
    assert store.command_attempts("r1", cap.id) == 2


def test_idle_timeout(store, workspace):
    cap = custom_cap(artifacts=(), run_ok=(RunOkCheck("exit_code", equals=0),))
    registry = {cap.id: cap}
    make_step(store, cap)
    silent = "print('[job] start', flush=True)\ntime.sleep(30)\n"
    ctx = make_ctx(store, workspace, registry, builder=script_builder(silent),
                   idle_timeout_override=1.0)

    result = run_async(execute_step(ctx, "r1", cap.id))
    assert result.outcome == "failed" and result.detail == "idle_timeout"
    step = store.load_step("r1", cap.id)
    assert step.state == "failed" and step.failure_class == "timeout"


# ---------------- 契约失败 / 质量门 ----------------

def test_missing_required_artifact_is_contract_broken(store, workspace):
    cap = custom_cap(
        artifacts=(ArtifactSpec("must", ("data/never.h5", "data/alt/never.h5")),),)
    registry = {cap.id: cap}
    make_step(store, cap)
    ok_script = "print('[job] ok', flush=True)\n"
    ctx = make_ctx(store, workspace, registry, builder=script_builder(ok_script))

    result = run_async(execute_step(ctx, "r1", cap.id))
    assert result.outcome == "failed"
    step = store.load_step("r1", cap.id)
    assert step.failure_class == "contract_broken"
    assert "data/never.h5" in result.detail  # 显式列出尝试过的候选(§1.4)
    trace = store.trace_of("r1")
    assert trace and trace[-1]["error_occurred"] == 1  # try/finally 审计真触发


GATE_JOB = """
(WS / "products").mkdir(parents=True, exist_ok=True)
(WS / "products" / "qa.json").write_text(json.dumps({"gate_metric": 0.5}), encoding="utf-8")
print("[job] qa written", flush=True)
"""


def _gate_cap(threshold_key: str):
    return custom_cap(
        artifacts=(ArtifactSpec("qa_report", ("products/qa.json",), policy="content"),),
        run_ok=(RunOkCheck("exit_code", equals=0),
                RunOkCheck("artifact_exists", id="qa_report")),
        quality_gate=(RunOkCheck("metric_min", metric="gate_metric",
                                 threshold_key=threshold_key, on_fail="stop"),))


def test_quality_gate_stops_on_ok_threshold(store, workspace):
    # esd_coherence_threshold: OK 0.85;指标 0.5 < 0.85 → 拦停(不是错误)
    cap = _gate_cap("esd_coherence_threshold")
    registry = {cap.id: cap}
    make_step(store, cap)
    ctx = make_ctx(store, workspace, registry, builder=script_builder(GATE_JOB))

    result = run_async(execute_step(ctx, "r1", cap.id))
    assert result.outcome == "gate_stop"
    step = store.load_step("r1", cap.id)
    assert step.failure_class == "data_quality" and step.run_ok == 0


def test_pending_threshold_only_warns(store, workspace):
    # corr_threshold: PENDING → 不参与硬 gate,只警告(§4.13 纪律 2)
    cap = _gate_cap("corr_threshold")
    registry = {cap.id: cap}
    make_step(store, cap)
    events: list[dict] = []
    ctx = make_ctx(store, workspace, registry, builder=script_builder(GATE_JOB),
                   emit=events.append)

    result = run_async(execute_step(ctx, "r1", cap.id))
    assert result.outcome == "done"
    step = store.load_step("r1", cap.id)
    assert step.state == "done" and step.run_ok == 1
    warns = [e for e in events if e["t"] == "note" and e.get("tone") == "warn"]
    assert warns and "PENDING" in warns[0]["text"]


def test_metrics_extracted_from_qa_report(store, workspace):
    cap = _gate_cap("corr_threshold")
    registry = {cap.id: cap}
    make_step(store, cap)
    ctx = make_ctx(store, workspace, registry, builder=script_builder(GATE_JOB))
    run_async(execute_step(ctx, "r1", cap.id))
    metrics = {m["name"]: m for m in store.metrics_of("r1")}
    assert "gate_metric" in metrics and metrics["gate_metric"]["value"] == 0.5


# ---------------- 后端 prepare/launch 失败分诊(WSL P2) ----------------

class _PrepareBoomBackend(LocalJobBackend):
    def prepare(self, job_dir, plan):
        raise RuntimeError("WSL 侧创建作业目录失败(rc=1):mount failed")


class _LaunchBoomBackend(LocalJobBackend):
    def launch(self, job_dir):
        raise RuntimeError("WSL 侧启动 wrapper 失败(rc=127):setsid not found")


class _LaunchOsErrorBackend(LocalJobBackend):
    def launch(self, job_dir):
        raise OSError("spawn failed: interpreter missing")


@pytest.mark.parametrize("backend_cls,expected_class", [
    (_PrepareBoomBackend, "wsl_orphaned"),
    (_LaunchBoomBackend, "wsl_orphaned"),
    (_LaunchOsErrorBackend, "tool_missing"),
])
def test_backend_prepare_launch_failure_is_triaged(store, workspace,
                                                   backend_cls, expected_class):
    """后端 prepare/launch 抛错 → execute_step 正常返回 failed + 分类合理,
    绝不裸冒泡(WSL P2:修复前 WslJobBackend 的 RuntimeError 直接穿透
    execute_step,driver 的 exec_task.result() 重抛,崩断整个执行回合流)。

    RuntimeError(WSL 环境不可用)→ wsl_orphaned:环境事件、修复环境后续跑;
    OSError(宿主 spawn/写盘失败)→ tool_missing:宿主环境问题,停链报修。
    """
    cap = custom_cap()
    registry = {cap.id: cap}
    make_step(store, cap)
    ctx = make_ctx(store, workspace, registry,
                   builder=script_builder("print('never runs', flush=True)\n"))
    ctx.backend = backend_cls(hb_stale=3.0)

    result = run_async(execute_step(ctx, "r1", cap.id))  # 正常返回,不抛异常
    assert result.outcome == "failed"
    assert "prepare/launch" in result.detail or "作业启动失败" in result.detail
    step = store.load_step("r1", cap.id)
    assert step.state == "failed" and step.failure_class == expected_class
    # 审计 try/finally 真触发(§1.6):错误类型进 trace
    trace = store.trace_of("r1")
    assert trace and trace[-1]["error_type"] == expected_class
