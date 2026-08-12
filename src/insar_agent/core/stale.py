"""失效传播 + 原因分类(AGENT-DESIGN §5.1/§5.4)—— 主 novelty。

五类失效原因(接到 UI,不只是日志):
    method_changed     换了方法          args_hash 变且 method 不同
    param_changed      改了科学/呈现参数  args_hash 或 local_hash 变且 method 相同
    upstream_changed   上游重跑了        自身 args 未变,上游 eval_hash 变
    tool_upgraded      工具版本变了      task_hash 变
    artifact_missing   产物被删/被改     产物指纹与记录不符

参数三分类的行为差异(§5.4,真正的差异化点):
    science       → 标脏自身 + 全部下游
    presentation  → 只标脏自身(eval_hash 不变,不传播)
    resource      → 不标脏,仅记 provenance
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path

from insar_agent.core.filehash import fingerprint_target
from insar_agent.core.fingerprint import step_hashes
from insar_agent.core.store import Store
from insar_agent.registry.model import Capability


@dataclass
class StaleImpact:
    changed_step: int
    reason: str  # 触发点的原因
    affected: list[dict] = field(default_factory=list)  # [{step_id, reason, state_before}]
    rerun_minutes: float | None = None  # None = 未知(历史样本 <3,§7.5)
    rerun_basis: str = ""  # 预估依据说明

    def affected_ids(self) -> list[int]:
        return [a["step_id"] for a in self.affected]


def _tool_versions_for(cap: Capability, method_id: str, tool_versions: dict) -> dict:
    """只有该步骤所选方法用到的引擎版本进 task_hash —— 升级 mintpy 不应标脏 snaphu 步骤。"""
    method = cap.method(method_id)
    engines = set(method.requires_engines) if method else set()
    if method and method.engine not in ("-", ""):
        engines.add(method.engine)
    return {e: v for e, v in tool_versions.items() if e in engines}


def compute_step_hashes(cap: Capability, method: str, params: dict,
                        upstream_evals: list[str], tool_versions: dict) -> dict:
    return step_hashes(
        capability=str(cap.id), version=cap.version,
        tool_versions=_tool_versions_for(cap, method, tool_versions),
        method=method, params=params, param_kinds=cap.param_kinds(),
        upstream_eval_hashes=upstream_evals)


def _classify(old, new_hashes: dict, method: str) -> str | None:
    """对单步分类失效原因;None = 无变化。"""
    if old.task_hash != new_hashes["task_hash"]:
        return "tool_upgraded"
    if old.args_hash != new_hashes["args_hash"]:
        return "method_changed" if method != old.method else "param_changed"
    if old.local_hash != new_hashes["local_hash"]:
        return "param_changed"  # 呈现参数(只影响本步)
    if old.eval_hash != new_hashes["eval_hash"]:
        return "upstream_changed"
    return None


def refresh_run(store: Store, run_id: str, registry: dict[int, Capability],
                tool_versions: dict, *, changes: dict[int, dict] | None = None,
                apply: bool = True) -> list[dict]:
    """按拓扑序全量重算指纹,分类并(可选)落盘 stale 标记。

    changes: {step_id: {method?, params?}} —— 视图层的待应用变更;
    apply=False 时是纯预览(审批卡数据源),不写库。
    返回 [{step_id, reason}] 受影响列表。
    """
    changes = changes or {}
    steps = {s.step_id: s for s in store.load_steps(run_id)}
    affected: list[dict] = []
    new_evals: dict[int, str] = {}

    for sid in sorted(steps):
        cap = registry[sid]
        old = steps[sid]
        method = changes.get(sid, {}).get("method", old.method)
        params = dict(old.params)
        params.update(changes.get(sid, {}).get("params", {}))
        upstream_evals = [new_evals[d] for d in cap.deps if d in new_evals]
        # 上游不在本 run(fork 部分链)时,沿用旧 eval 的上游部分:直接用存量
        for d in cap.deps:
            if d not in new_evals and d in steps:
                upstream_evals.append(steps[d].eval_hash)
        hashes = compute_step_hashes(cap, method, params, upstream_evals, tool_versions)
        new_evals[sid] = hashes["eval_hash"]

        reason = _classify(old, hashes, method)
        if reason:
            affected.append({"step_id": sid, "reason": reason, "state_before": old.state})
            if apply:
                store.set_step_config(run_id, sid, method=method, params=params, hashes=hashes)
                if old.state in ("done", "stale"):
                    store.set_stale(run_id, sid, True, reason)
        elif apply and sid in changes:
            # 资源参数变更:不标脏,但配置与 provenance 要记录(§5.4)
            store.set_step_config(run_id, sid, method=method, params=params, hashes=hashes)
    return affected


def estimate_rerun(store: Store, run_id: str, step_ids: list[int],
                   registry: dict[int, Capability]) -> tuple[float | None, str]:
    """预估重跑时长(分钟)。任一步骤历史样本 <3 → 整体「未知」(§7.5:没有依据不给数)。"""
    total = 0.0
    counted = 0
    for sid in step_ids:
        step = store.load_step(run_id, sid)
        if step is None:
            continue
        history = store.duration_history(step.capability, step.method)
        if len(history) < 3:
            return None, f"步骤 {sid} 历史运行不足 3 次,时长未知"
        total += statistics.median(history)
        counted += 1
    if counted == 0:
        return None, "无待跑步骤"
    return total / 60.0, f"基于本机历史运行中位数({counted} 步)"


def apply_change(store: Store, run_id: str, step_id: int, *, registry: dict[int, Capability],
                 tool_versions: dict, method: str | None = None,
                 params_patch: dict | None = None) -> StaleImpact:
    """改方法/参数 → 校验 → 级联标脏 → 返回影响(§1.2:前置确认的数据源)。"""
    cap = registry[step_id]
    if params_patch:
        errors = cap.validate_params(params_patch)
        if errors:
            raise ValueError(f"参数校验失败:{errors}")
    if method is not None and cap.method(method) is None:
        raise ValueError(f"未知方法 {method}(候选:{[m.id for m in cap.methods]})")

    change = {}
    if method is not None:
        change["method"] = method
    if params_patch:
        change["params"] = params_patch
    affected = refresh_run(store, run_id, registry, tool_versions,
                           changes={step_id: change}, apply=True)
    trigger = next((a for a in affected if a["step_id"] == step_id), None)
    reason = trigger["reason"] if trigger else "no_change"

    rerun_ids = [a["step_id"] for a in affected
                 if a["state_before"] in ("done", "stale")]
    minutes, basis = estimate_rerun(store, run_id, rerun_ids, registry)
    return StaleImpact(changed_step=step_id, reason=reason, affected=affected,
                       rerun_minutes=minutes, rerun_basis=basis)


def preview_change(store: Store, run_id: str, step_id: int, *, registry, tool_versions,
                   method: str | None = None, params_patch: dict | None = None) -> StaleImpact:
    """纯预览:审批卡显示「影响范围 + 预估时长」,不写库(§7.6)。"""
    change = {}
    if method is not None:
        change["method"] = method
    if params_patch:
        change["params"] = params_patch
    affected = refresh_run(store, run_id, registry, tool_versions,
                           changes={step_id: change}, apply=False)
    trigger = next((a for a in affected if a["step_id"] == step_id), None)
    rerun_ids = [a["step_id"] for a in affected if a["state_before"] in ("done", "stale")]
    minutes, basis = estimate_rerun(store, run_id, rerun_ids, registry)
    return StaleImpact(changed_step=step_id,
                       reason=trigger["reason"] if trigger else "no_change",
                       affected=affected, rerun_minutes=minutes, rerun_basis=basis)


def verify_artifacts(store: Store, run_id: str, workspace: Path,
                     *, apply: bool = True) -> list[dict]:
    """产物指纹复核:被删/被改 → 标脏所属步骤(artifact_missing)。

    只标所属步骤:下游既有产物是当时用有效输入算出的,物理上仍有效;
    「删除后哪些步骤从跳过变成需重算」正是 GC 前置提示的数据源(§4.10)。
    """
    hits: list[dict] = []
    for art in store.artifacts_of(run_id):
        p = workspace / art["path"]
        fp_now = fingerprint_target(p, art["policy"])
        if fp_now != art["fp"]:
            hits.append({"step_id": art["step_id"], "art_id": art["art_id"],
                         "reason": "artifact_missing",
                         "detail": "产物被删除" if not p.exists() else "产物内容与记录不符"})
            if apply:
                store.set_stale(run_id, art["step_id"], True, "artifact_missing")
    return hits
