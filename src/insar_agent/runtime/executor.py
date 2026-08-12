"""五阶段执行器 + 幂等守卫(AGENT-DESIGN §4.3;aiida tasks.py 模式)。

    PREPARED ──> LAUNCHED ──> RUNNING ──> COLLECTED ──> VERIFIED
       │            │            │            │             └ run_ok 双判定通过
       │            │            │            └ 产物发现完成,指纹已算
       │            │            └ 作业已结束,exit_code 已知
       │            └ 作业已启动,job_dir/命令意图已落盘
       └ 配置已渲染,输入清单已校验

    任一阶段失败 → state=failed(保留已完成阶段的成果,§1.6)
    运行中取消   → state=interrupted(保留日志偏移,可复位重跑)
    wrapper 失踪 → state=orphaned(环境事件,非计算失败,§4.7)

纪律(吸收 pi harness,absorb-E2):
  - 意图先落盘:launch 之前 reserve_command(预留命令行 id);崩溃后恢复时
    优先认领既有作业/未结算命令,绝不盲目重启动(唯一不确定窗口的处置)。
  - 恢复 = 主键点查 + 阶段守卫,绝不扫描推断。
  - 审计 try/finally 真触发:无论成败,trace 都落一条(§1.6)。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from insar_agent.audit.contract import Threshold, load_contract
from insar_agent.audit.runok import evaluate_run_ok
from insar_agent.core.filehash import fingerprint_target
from insar_agent.core.store import StepRow, Store
from insar_agent.registry.model import Capability
from insar_agent.runtime.discover import discover_artifacts, missing_message
from insar_agent.runtime.jobs import CommandPlan, JobBackend
from insar_agent.runtime.render import render_plan_files
from insar_agent.runtime.stream import CancelToken, follow_job

Emit = Callable[[dict], None] | Callable[[dict], Awaitable[None]]
Builder = Callable[..., CommandPlan]


class StepExecutionError(RuntimeError):
    def __init__(self, failure_class: str, message: str):
        super().__init__(message)
        self.failure_class = failure_class


@dataclass
class ExecContext:
    store: Store
    backend: JobBackend
    workspace: Path
    registry: dict[int, Capability]
    builder: Builder
    contract: dict[str, Threshold] = field(default_factory=load_contract)
    emit: Emit | None = None
    poll: float = 0.5
    idle_timeout_override: float | None = None
    total_timeout_override: float | None = None
    cancel_grace: float = 15.0
    startup_grace: float = 30.0

    async def _emit(self, event: dict) -> None:
        if self.emit is None:
            return
        result = self.emit(event)
        if hasattr(result, "__await__"):
            await result  # type: ignore[misc]


@dataclass
class StepResult:
    outcome: str  # done | failed | interrupted | orphaned | gate_stop
    step: StepRow
    detail: str = ""


def _job_dir(ctx: ExecContext, run_id: str, step_id: int, attempt: int) -> Path:
    # 后端可宣告自己的作业目录根(WslJobBackend → \\wsl.localhost 下的 Linux fs,
    # §4.8 9p 红线);未宣告的维持工作区 .jobs 原布局
    job_root = getattr(ctx.backend, "job_root", None)
    root = job_root(ctx.workspace) if callable(job_root) else ctx.workspace / ".jobs"
    return root / run_id / f"s{step_id:02d}" / f"a{attempt}"


async def execute_step(
    ctx: ExecContext, run_id: str, step_id: int, token: CancelToken | None = None
) -> StepResult:
    """执行(或恢复)一个步骤。前置条件:step 行存在且不处于终态坏状态
    (interrupted/failed 的重跑由 driver 先调 reset_step_for_rerun)。"""
    store = ctx.store
    token = token or CancelToken()
    cap = ctx.registry[step_id]
    step = store.load_step(run_id, step_id)
    if step is None:
        raise KeyError(f"step {run_id}/{step_id} not found")
    if step.state in ("failed", "interrupted", "orphaned"):
        raise StepExecutionError(
            "contract_broken",
            f"step {step_id} 处于 {step.state},须先 reset 再重跑(驱动层职责)")

    run = store.get_run(run_id) or {}
    # 全链方法/参数快照:MintPy 等「整链共享配置」的引擎需要跨步骤上下文渲染 cfg
    run["chain"] = {s.step_id: {"method": s.method, "params": s.params}
                    for s in store.load_steps(run_id)}
    error_info: dict[str, str] = {}
    outcome = "done"
    detail = ""

    try:
        # ---------------- PREPARED:渲染配置 + 校验输入 ----------------
        if step.stage_lt("PREPARED"):
            plan = ctx.builder(cap=cap, method=step.method, params=step.params,
                               run=run, workspace=ctx.workspace)
            missing_inputs = [
                art for art in cap.inputs
                if store.find_artifact(run_id, art) is None
            ]
            if missing_inputs:
                raise StepExecutionError(
                    "contract_broken", f"输入产物缺失:{missing_inputs}(上游未完成或被 GC)")
            config_hash = render_plan_files(ctx.workspace, plan)
            store.advance(run_id, step_id, "PREPARED", state="running",
                          config_hash=config_hash, started_at=time.time())
            step = store.load_step(run_id, step_id)
            await ctx._emit({"t": "step.stage", "stepId": step_id, "stage": "PREPARED"})

        if token.cancelled:
            store.mark_step(run_id, step_id, state="interrupted")
            return StepResult("interrupted", store.load_step(run_id, step_id), "启动前取消")

        # ---------------- LAUNCHED:意图落盘 → 启动 ----------------
        if step.stage_lt("LAUNCHED"):
            plan = ctx.builder(cap=cap, method=step.method, params=step.params,
                               run=run, workspace=ctx.workspace)
            attempt = store.command_attempts(run_id, step_id) + 1
            job_dir = _job_dir(ctx, run_id, step_id, attempt)
            log_path = job_dir / "job.log"

            existing = store.latest_unsettled_command(run_id, step_id)
            claimed = False
            if existing is not None:
                # 意图已落盘。作业若已实际启动(崩溃在 launch 与 advance 之间),
                # 认领它,绝不重复启动(absorb-E2 唯一不确定窗口的处置)。
                prev_dir = Path(existing["stdout_path"]).parent if existing["stdout_path"] else None
                if prev_dir and prev_dir.exists() and ctx.backend.state(prev_dir).kind != "unknown":
                    job_dir = prev_dir
                    log_path = prev_dir / "job.log"
                    command_id = existing["id"]
                    claimed = True
            if not claimed:
                command_id = store.reserve_command(
                    run_id, step_id, plan.argv, cwd=plan.cwd,
                    cmd_path=str(job_dir / "cmd.sh"), stdout_path=str(log_path),
                    attempt=attempt)  # ← 意图:命令即将执行
                ctx.backend.prepare(job_dir, plan)
                ctx.backend.launch(job_dir)

            store.advance(run_id, step_id, "LAUNCHED", state="running",
                          job_dir=str(job_dir), command_id=command_id,
                          log_path=str(log_path))
            step = store.load_step(run_id, step_id)
            await ctx._emit({"t": "step.stage", "stepId": step_id, "stage": "LAUNCHED"})

        # ---------------- RUNNING:跟随日志直到结束 ----------------
        if step.stage_lt("RUNNING"):
            job_dir = Path(step.job_dir)  # type: ignore[arg-type]
            launched_at = step.started_at or time.time()

            async def on_line(line: str) -> None:
                await ctx._emit({"t": "tool.log", "id": f"s{step_id}", "line": line,
                                 "tone": _tone_of(line)})

            def on_offset(offset: int) -> None:
                store.update_log_offset(run_id, step_id, offset)  # 供 reattach 续读

            out = await follow_job(
                ctx.backend, job_dir,
                offset=step.log_offset,
                idle_timeout=ctx.idle_timeout_override or cap.timeouts.idle,
                total_timeout=ctx.total_timeout_override or cap.timeouts.total,
                token=token, on_line=on_line, on_offset=on_offset,
                poll=ctx.poll, cancel_grace=ctx.cancel_grace,
                startup_grace=ctx.startup_grace)

            duration = time.time() - launched_at
            if out.kind == "finished":
                if step.command_id:
                    store.settle_command(step.command_id, exit_code=out.exit_code or 0,
                                         duration=duration)
                store.advance(run_id, step_id, "RUNNING", exit_code=out.exit_code)
                step = store.load_step(run_id, step_id)
            elif out.kind == "cancelled":
                if step.command_id and out.exit_code is not None:
                    store.settle_command(step.command_id, exit_code=out.exit_code,
                                         duration=duration)
                store.mark_step(run_id, step_id, state="interrupted", ended_at=time.time())
                return StepResult("interrupted", store.load_step(run_id, step_id),
                                  "用户取消;已完成阶段与日志已保留")
            elif out.kind in ("idle_timeout", "total_timeout"):
                if step.command_id and out.exit_code is not None:
                    store.settle_command(step.command_id, exit_code=out.exit_code,
                                         duration=duration)
                store.mark_step(run_id, step_id, state="failed", failure_class="timeout",
                                ended_at=time.time(),
                                qa=[{"check": out.kind, "ok": False,
                                     "detail": f"超时终止({out.kind})"}])
                error_info = {"type": "timeout", "message": out.kind}
                return StepResult("failed", store.load_step(run_id, step_id), out.kind)
            else:  # orphaned:环境事件,非计算失败(§4.7)
                if step.command_id:
                    # 合成结算(pi:在预留 id 下写合成结果,对话/账本保持闭合)
                    store.settle_command(step.command_id, exit_code=-255, duration=duration)
                store.mark_step(run_id, step_id, state="orphaned",
                                failure_class="wsl_orphaned", ended_at=time.time())
                error_info = {"type": "wsl_orphaned",
                              "message": "执行环境停止(WSL 关机/进程被杀),产物与日志已保留"}
                return StepResult("orphaned", store.load_step(run_id, step_id),
                                  "执行环境停止导致中断 —— 非计算失败,可续跑")

        # ---------------- COLLECTED:产物发现 + 指纹 ----------------
        if step.stage_lt("COLLECTED"):
            found, missing = discover_artifacts(ctx.workspace, cap.artifacts)
            if missing:
                raise StepExecutionError("contract_broken", missing_message(missing))
            for f in found:
                fp = fingerprint_target(f.path, f.spec.policy)
                st = f.path.stat() if f.path.exists() and f.path.is_file() else None
                store.record_artifact(
                    run_id, step_id, f.spec.id,
                    path=str(f.path.relative_to(ctx.workspace)).replace("\\", "/"),
                    kind=f.spec.kind, layout=f.spec.layout, policy=f.spec.policy, fp=fp,
                    size=st.st_size if st else None,
                    mtime_ns=st.st_mtime_ns if st else None)
            store.advance(run_id, step_id, "COLLECTED")
            step = store.load_step(run_id, step_id)
            await ctx._emit({"t": "step.stage", "stepId": step_id, "stage": "COLLECTED"})

        # ---------------- VERIFIED:run_ok 双判定 + 质量门 ----------------
        if step.stage_lt("VERIFIED"):
            artifacts = {
                a["art_id"]: ctx.workspace / a["path"]
                for a in store.artifacts_of(run_id, step_id)
            }
            metrics = _extract_metrics(store, run_id, artifacts)
            result = evaluate_run_ok(
                cap, exit_code=step.exit_code, artifacts=artifacts,
                log_path=Path(step.log_path) if step.log_path else None,
                metrics=metrics, contract=ctx.contract)

            if result.gate_stop:
                # 质量门拦截 ≠ 错误(§7.4 gate_stop 条目)
                store.mark_step(run_id, step_id, state="failed",
                                failure_class="data_quality", run_ok=0,
                                qa=result.to_qa(), ended_at=time.time())
                error_info = {"type": "data_quality", "message": "质量门拦停"}
                return StepResult("gate_stop", store.load_step(run_id, step_id),
                                  "质量门拦停 —— 这不是错误,是质量标准未达")
            if not result.ok:
                store.mark_step(run_id, step_id, state="failed", run_ok=0,
                                qa=result.to_qa(), ended_at=time.time())
                error_info = {"type": "run_ok", "message": "run_ok 双判定未通过"}
                return StepResult("failed", store.load_step(run_id, step_id),
                                  "run_ok 双判定未通过(退出码或领域校验)")
            store.advance(run_id, step_id, "VERIFIED", state="done", run_ok=1,
                          qa=result.to_qa(), ended_at=time.time())
            step = store.load_step(run_id, step_id)
            await ctx._emit({"t": "step.stage", "stepId": step_id, "stage": "VERIFIED"})
            if result.warnings:
                await ctx._emit({"t": "note", "tone": "warn",
                                 "text": "; ".join(result.warnings)})

        return StepResult("done", store.load_step(run_id, step_id))

    except StepExecutionError as exc:
        outcome = "failed"
        detail = str(exc)
        error_info = {"type": exc.failure_class, "message": str(exc)}
        store.mark_step(run_id, step_id, state="failed", failure_class=exc.failure_class,
                        ended_at=time.time())
        return StepResult("failed", store.load_step(run_id, step_id), detail)
    finally:
        # 审计必须 try/finally 真触发(§1.6):取消/失败同样留痕
        final = store.load_step(run_id, step_id)
        store.append_trace(
            run_id=run_id, step_no=step_id, phase=cap.phase or cap.name,
            thought="", action={"type": "execute_step", "tool": cap.name,
                                "input": {"method": final.method if final else None}},
            observation=f"stage={final.stage if final else '?'} state={final.state if final else '?'}",
            error_occurred=bool(error_info),
            error_type=error_info.get("type", ""),
            error_message=error_info.get("message", ""))


def _tone_of(line: str) -> str:
    lowered = line.lower()
    if "error" in lowered or "fail" in lowered or "traceback" in lowered:
        return "err"
    if "warn" in lowered:
        return "warn"
    if line.startswith("$"):
        return "cmd"
    if line.startswith("✓") or " ok" in lowered:
        return "ok"
    return "dim"


def _extract_metrics(store: Store, run_id: str, artifacts: dict[str, Path]) -> dict[str, float]:
    """从 qa_report 类 JSON 产物提取数值指标并入账(来源可追溯,§7.1)。"""
    metrics: dict[str, float] = {}
    qa = artifacts.get("qa_report")
    if qa and qa.exists() and qa.suffix == ".json":
        try:
            data = json.loads(qa.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return metrics
        for key, value in data.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                metrics[key] = float(value)
                store.record_metric(run_id, key, value=float(value),
                                    source_artifact=str(qa.name), source_field=key,
                                    reparsed_ok=None)  # verify.py 重解析后回填
    return metrics
