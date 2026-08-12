"""Phase 2 验收:可行性收窄(带理由)、场景 override、fork 复用(absorb-E5)。"""

import pytest

from insar_agent.core.actions import apply_action
from insar_agent.core.store import Store
from insar_agent.planner.feasibility import narrow_methods
from insar_agent.planner.plan import fork_run, make_plan
from insar_agent.registry.capabilities import REGISTRY
from insar_agent.registry.scenarios import classify_text, scenario_of
from insar_agent.runtime.probe import ProbeResult

FULL_TOOLS = {"isce2": "2.6.5", "mintpy": "1.6.4", "snaphu": "2.0.7", "gdal": "3.8",
              "snap": "9", "pystamps": "0.3.4", "pyaps": "0.3.6"}


def full_probe() -> ProbeResult:
    return ProbeResult(engines=dict(FULL_TOOLS),
                       credentials={"earthdata": True, "cds": True, "gacos": False})


def empty_probe() -> ProbeResult:
    return ProbeResult(engines={k: None for k in FULL_TOOLS},
                       credentials={"earthdata": False, "cds": False, "gacos": False})


def test_narrow_reports_reasons():
    feas = narrow_methods(REGISTRY[8], empty_probe())
    by_id = {f.method.id: f for f in feas}
    assert not by_id["tropo_era5_pyaps"].ok
    assert "工具链缺失" in by_id["tropo_era5_pyaps"].blocked_reason
    # CDS 凭据是条件依赖(有 ERA5 缓存则不需要),不做静态硬拦
    assert not by_id["tropo_gacos"].ok  # 缺 gacos 凭据
    assert "缺凭据" in by_id["tropo_gacos"].blocked_reason


def test_scenario_constraint_survives_simulation():
    """演示模式放行引擎缺失,但场景约束(step 只在 quake)仍生效。"""
    feas = narrow_methods(REGISTRY[9], empty_probe(), scenario="permafrost",
                          allow_simulated=True)
    by_id = {f.method.id: f for f in feas}
    assert by_id["poly_periodic"].ok and by_id["poly_periodic"].simulated
    assert not by_id["step"].ok
    assert "场景不匹配" in by_id["step"].blocked_reason


def test_scenario_classification():
    assert classify_text("我想做玉树冻土的时序分析").key == "permafrost"
    assert classify_text("Ridgecrest 地震同震形变").key == "quake"
    assert classify_text("随便说点什么") is None  # 不猜,交给 LLM/表单


def test_make_plan_real_tools(store: Store):
    store.create_session("s1", "t")
    plan = make_plan(store, "s1", registry=REGISTRY, probe=full_probe(),
                     scenario=scenario_of("quake"), workspace="ws")
    assert plan.runnable() and not plan.simulated
    steps = {p.step_id: p for p in plan.steps}
    assert steps[9].method == "step"  # 场景 override:同震 → 阶跃模型
    assert steps[9].params["step_date"] == "20190706T0320"
    assert len(store.load_steps(plan.run_id)) == 11
    assert store.edges(plan.run_id)  # DAG 已落库


def test_make_plan_no_engines_needs_simulation(store: Store):
    store.create_session("s1", "t")
    plan = make_plan(store, "s1", registry=REGISTRY, probe=empty_probe(),
                     scenario=scenario_of("quake"), workspace="ws")
    assert not plan.runnable()  # 诚实:无引擎不可跑
    plan2 = make_plan(store, "s1", registry=REGISTRY, probe=empty_probe(),
                      scenario=scenario_of("quake"), workspace="ws", allow_simulated=True)
    assert plan2.runnable() and plan2.simulated
    assert bool(store.get_run(plan2.run_id)["simulated"]) is True  # 进 provenance


def test_fork_reuses_unaffected_steps(store: Store):
    store.create_session("s1", "t")
    plan = make_plan(store, "s1", registry=REGISTRY, probe=full_probe(),
                     scenario=scenario_of("quake"), workspace="ws")
    for sid in range(1, 12):
        store.advance(plan.run_id, sid, "VERIFIED", state="done", run_ok=1)
    store.record_artifact(plan.run_id, 5, "ifg_filt", path="data/ifg_filt",
                          kind="IFG_WRAPPED", layout="isce2", policy="stat", fp="fp5")

    fork = fork_run(store, plan.run_id, registry=REGISTRY,
                    changes={6: {"method": "snaphu_smooth"}}, probe=full_probe())
    states = {p.step_id: p.state for p in fork.steps}
    assert states[1] == states[5] == "done"  # 未受影响:复用
    assert states[6] == states[7] == states[11] == "pending"  # 6 与下游:重算
    # 复用步骤的产物沿祖先链可见
    art = store.find_artifact(fork.run_id, "ifg_filt")
    assert art is not None and art["run_id"] == plan.run_id
    assert store.get_run(fork.run_id)["parent_run_id"] == plan.run_id


def test_fork_validates_params(store: Store):
    store.create_session("s1", "t")
    plan = make_plan(store, "s1", registry=REGISTRY, probe=full_probe(),
                     scenario=scenario_of("quake"), workspace="ws")
    with pytest.raises(ValueError):
        fork_run(store, plan.run_id, registry=REGISTRY,
                 changes={6: {"params": {"min_coherence": 5}}}, probe=full_probe())


def test_actions_set_method_and_reset(store: Store):
    store.create_session("s1", "t")
    plan = make_plan(store, "s1", registry=REGISTRY, probe=full_probe(),
                     scenario=scenario_of("quake"), workspace="ws")
    rid = plan.run_id
    for sid in range(1, 12):
        store.advance(rid, sid, "VERIFIED", state="done", run_ok=1)

    store.push_action(scope="step", target="6", action="SET_METHOD",
                      payload={"method": "icu"}, deliver_as="steer")
    action = store.due_actions("steer")[0]
    outcome = apply_action(store, rid, action, registry=REGISTRY, tool_versions=FULL_TOOLS)
    assert outcome.ok and outcome.impact is not None
    assert outcome.impact.affected_ids() == [6, 7, 8, 9, 10, 11]
    assert outcome.events and outcome.events[0]["t"] == "intervention"  # §7.4 留痕
    store.consume_action(action["id"])

    outcome2 = apply_action(store, rid, {"action": "RESET", "target": "7", "payload": {}},
                            registry=REGISTRY, tool_versions=FULL_TOOLS)
    assert outcome2.ok
    assert store.load_step(rid, 7).stage == "PENDING"
    assert store.load_step(rid, 8).state == "stale"  # 下游标脏
