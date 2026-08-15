"""计划生成:registry + 可行性 + 场景 → run/steps/edges 落库(AGENT-DESIGN planner/plan)。

fork_run(absorb-E5,pi 树结构 × 我们的指纹系统):
    fork 时逐步比较 eval_hash,与父 run 一致且父步已完成的 → 直接标 done 复用产物
    (find_artifact 沿祖先链解析),其余 pending。参数试探零重算。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from insar_agent.core.stale import compute_step_hashes
from insar_agent.core.store import Store, new_run_id
from insar_agent.planner.feasibility import narrow_methods
from insar_agent.planner.score import pick_method
from insar_agent.registry.model import Capability
from insar_agent.registry.scenarios import Scenario
from insar_agent.runtime.probe import ProbeResult


@dataclass
class PlannedStep:
    step_id: int
    name: str
    method: str
    params: dict
    state: str  # pending | done(fork 复用)
    simulated: bool = False
    narrowed: list[dict] = field(default_factory=list)  # 候选收窄解释(面板9)


@dataclass
class PlanResult:
    run_id: str
    steps: list[PlannedStep]
    problems: list[str] = field(default_factory=list)  # 不可行步骤说明
    simulated: bool = False

    def runnable(self) -> bool:
        return not self.problems


def _plan_methods(registry: dict[int, Capability], probe: ProbeResult, *,
                  scenario: Scenario | None, allow_simulated: bool,
                  overrides: dict[int, dict] | None = None,
                  groups: tuple[str, ...] = ("core",)):
    """为每一步选方法/参数;返回 (choices, problems)。"""
    choices: dict[int, PlannedStep] = {}
    problems: list[str] = []
    overrides = overrides or {}
    cloud_done = set(scenario.cloud_completed) if scenario else set()
    for sid in sorted(registry):
        cap = registry[sid]
        if cap.group not in groups:
            continue  # 分组过滤:核心 run 不排分析步,分析 run 不重跑主链
        prefer = None
        params = cap.default_params()
        if scenario and sid in scenario.step_overrides:
            ov = scenario.step_overrides[sid]
            prefer = ov.get("method")
            params.update(ov.get("params", {}))
        if sid in overrides:
            # 外部覆写(next_run 干预队列直通此处,不经 apply_change)必须过 registry 校验:
            # 幻觉方法名若只是静默回退 recommend,用户会误以为覆写已生效;
            # 幻觉/越界参数若直接 update 进计划,会进指纹并流向引擎 —— 都记 problems 拒绝带病上路
            ov_method = overrides[sid].get("method")
            if ov_method is not None and cap.method(ov_method) is None:
                problems.append(f"第 {sid} 步({cap.name})覆写方法未知:{ov_method}"
                                f"(候选:{[m.id for m in cap.methods]})")
                ov_method = None
            prefer = ov_method or prefer
            patch = overrides[sid].get("params") or {}
            if patch:
                errors = cap.validate_params(patch)
                if errors:
                    problems.append(f"第 {sid} 步({cap.name})覆写参数校验失败:{errors}")
                else:
                    params.update(patch)

        if sid in cloud_done:
            # 云端已完成:不做可行性检查(本机缺 ISCE2/SNAPHU 不阻塞 HyP3 路线)
            choices[sid] = PlannedStep(
                step_id=sid, name=cap.name, method=prefer or cap.default_method,
                params=params, state="skipped",
                narrowed=[{"method": cap.default_method, "ok": True, "simulated": False,
                           "reason": "云端(HyP3)已完成"}])
            continue

        feas = narrow_methods(cap, probe, scenario=scenario.key if scenario else None,
                              allow_simulated=allow_simulated)
        picked = pick_method(feas, prefer=prefer)
        if picked is None:
            blocked = {f.method.id: f.blocked_reason for f in feas}
            problems.append(f"第 {sid} 步({cap.name})无可行方法:{blocked}")
            continue
        if (picked.method.id == "plate_motion_itrf"
                and not str(params.get("plate") or "").strip()):
            problems.append(
                f"第 {sid} 步({cap.name})选择 plate_motion_itrf 时 plate 必填"
                f"(ITRF2014-PMM 板块名,如 NorthAmerica)")
            continue
        choices[sid] = PlannedStep(
            step_id=sid, name=cap.name, method=picked.method.id, params=params,
            state="pending", simulated=picked.simulated,
            narrowed=[{"method": f.method.id, "ok": f.ok, "simulated": f.simulated,
                       "reason": f.blocked_reason} for f in feas])
    return choices, problems


def make_plan(store: Store, session_id: str, *, registry: dict[int, Capability],
              probe: ProbeResult, scenario: Scenario | None = None,
              workspace: str, intent: dict | None = None,
              overrides: dict[int, dict] | None = None,
              allow_simulated: bool = False,
              agent_hash: str | None = None,
              git_head: str | None = None, git_dirty: bool | None = None,
              groups: tuple[str, ...] = ("core",)) -> PlanResult:
    choices, problems = _plan_methods(registry, probe, scenario=scenario,
                                      allow_simulated=allow_simulated,
                                      overrides=overrides, groups=groups)
    simulated = any(c.simulated for c in choices.values())
    run_id = new_run_id()
    tool_versions = probe.tool_versions()
    store.create_run(run_id, session_id, workspace=workspace,
                     intent={**(intent or {}), "groups": list(groups)},
                     scenario=scenario.key if scenario else None, simulated=simulated,
                     tool_versions=tool_versions, agent_hash=agent_hash,
                     git_head=git_head, git_dirty=git_dirty)

    evals: dict[int, str] = {}
    for sid in sorted(choices):
        cap = registry[sid]
        c = choices[sid]
        upstream = [evals[d] for d in cap.deps if d in evals]
        hashes = compute_step_hashes(cap, c.method, c.params, upstream, tool_versions)
        evals[sid] = hashes["eval_hash"]
        store.create_step(run_id, sid, capability=str(sid), name=cap.name, method=c.method,
                          params=c.params, hashes=hashes, replay=cap.replay,
                          state=c.state if c.state == "skipped" else "pending")
        for dep in cap.deps:
            store.add_edge(run_id, dep, sid)
    store.set_run_status(run_id, "planning" if problems else "ready")
    return PlanResult(run_id=run_id, steps=[choices[s] for s in sorted(choices)],
                      problems=problems, simulated=simulated)


def fork_run(store: Store, parent_run_id: str, *, registry: dict[int, Capability],
             changes: dict[int, dict], probe: ProbeResult) -> PlanResult:
    """从父 run 分叉:改若干步的方法/参数,未受影响的已完成步骤直接复用(零重算)。"""
    parent = store.get_run(parent_run_id)
    if parent is None:
        raise KeyError(parent_run_id)
    parent_steps = {s.step_id: s for s in store.load_steps(parent_run_id)}
    tool_versions = probe.tool_versions()

    # changes 先整体校验再落库(触发场景:API /fork 直接透传外部 changes —— LLM/用户
    # 幻觉的方法名此前不经校验直接进 store;且原先边落库边校验,参数失败会留下半成品 run):
    #   - 步骤号必须存在于父 run(静默忽略会让调用方误以为变更已生效)
    #   - 方法名必须在候选集内(「只选不造」,与 stale.apply_change 同款校验)
    #   - 参数走 registry 声明校验(未声明的参数名即幻觉参数,拒绝)
    for sid, change in changes.items():
        cap = registry.get(sid)
        if cap is None or sid not in parent_steps:
            raise ValueError(f"变更指向不存在的步骤 {sid}")
        new_method = change.get("method")
        if new_method is not None and cap.method(new_method) is None:
            raise ValueError(f"未知方法 {new_method}(候选:{[m.id for m in cap.methods]})")
        patch = change.get("params")
        if patch:
            errors = cap.validate_params(patch)
            if errors:
                raise ValueError(f"参数校验失败:{errors}")
        merged_method = new_method if new_method is not None else parent_steps[sid].method
        merged_params = dict(parent_steps[sid].params)
        merged_params.update(patch or {})
        if (merged_method == "plate_motion_itrf"
                and not str(merged_params.get("plate") or "").strip()):
            raise ValueError("plate_motion_itrf 要求 plate 必填"
                             "(ITRF2014-PMM 板块名,如 NorthAmerica)")

    run_id = new_run_id("fork")
    store.create_run(run_id, parent["session_id"], workspace=parent["workspace"],
                     intent={"forked_from": parent_run_id, "changes": changes},
                     scenario=parent["scenario"], parent_run_id=parent_run_id,
                     simulated=bool(parent["simulated"]), tool_versions=tool_versions)

    planned: list[PlannedStep] = []
    evals: dict[int, str] = {}
    for sid in sorted(parent_steps):
        cap = registry[sid]
        old = parent_steps[sid]
        method = changes.get(sid, {}).get("method", old.method)
        params = dict(old.params)
        params.update(changes.get(sid, {}).get("params", {}))  # 已在前置校验通过
        upstream = [evals[d] for d in cap.deps if d in evals]
        hashes = compute_step_hashes(cap, method, params, upstream, tool_versions)
        evals[sid] = hashes["eval_hash"]

        if old.state == "skipped":
            # 云端已完成的步骤:fork 后仍是 skipped(不重算、不复用产物记录)
            state = "skipped"
        else:
            reuse = (hashes["eval_hash"] == old.eval_hash
                     and hashes["local_hash"] == old.local_hash
                     and old.state == "done")
            state = "done" if reuse else "pending"
        store.create_step(run_id, sid, capability=str(sid), name=cap.name, method=method,
                          params=params, hashes=hashes, replay=cap.replay, state=state)
        if state == "done":
            # 高水位直接置 VERIFIED:产物经 find_artifact 沿祖先链解析(absorb-E5)
            store.advance(run_id, sid, "VERIFIED", state="done", run_ok=1,
                          qa=[{"check": "reused_from_parent", "ok": True,
                               "detail": parent_run_id}])
        for dep in cap.deps:
            store.add_edge(run_id, dep, sid)
        planned.append(PlannedStep(step_id=sid, name=cap.name, method=method, params=params,
                                   state=state))
    store.set_run_status(run_id, "ready")
    return PlanResult(run_id=run_id, steps=planned, simulated=bool(parent["simulated"]))
