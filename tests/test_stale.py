"""Phase 2 验收:级联标脏 + 原因分类 + 参数三分类行为差异(AGENT-DESIGN §5.1/§5.4)。"""

import pytest

from insar_agent.core.stale import apply_change, preview_change, refresh_run, verify_artifacts
from insar_agent.core.store import Store
from insar_agent.planner.plan import make_plan
from insar_agent.registry.capabilities import REGISTRY
from insar_agent.registry.scenarios import scenario_of
from insar_agent.runtime.probe import ProbeResult


def seeded_run(store: Store, *, tools=None) -> str:
    """建一个全 11 步、全部标 done 的 run(模拟已跑完的处理)。"""
    probe = ProbeResult(engines=tools or {"isce2": "2.6.5", "mintpy": "1.6.4",
                                          "snaphu": "2.0.7", "gdal": "3.8", "snap": "9",
                                          "pystamps": "0.3.4", "pyaps": "0.3.6"},
                        credentials={"earthdata": True, "cds": True, "gacos": False})
    store.create_session("s1", "t")
    plan = make_plan(store, "s1", registry=REGISTRY, probe=probe,
                     scenario=scenario_of("quake"), workspace="ws")
    assert plan.runnable(), plan.problems
    for sid in range(1, 12):
        store.advance(plan.run_id, sid, "VERIFIED", state="done", run_ok=1)
    return plan.run_id


TOOLS = {"isce2": "2.6.5", "mintpy": "1.6.4", "snaphu": "2.0.7", "gdal": "3.8",
         "snap": "9", "pystamps": "0.3.4", "pyaps": "0.3.6"}


def test_science_param_cascades_downstream(store):
    rid = seeded_run(store)
    impact = apply_change(store, rid, 6, registry=REGISTRY, tool_versions=TOOLS,
                          params_patch={"min_coherence": 0.30})
    assert impact.reason == "param_changed"
    assert impact.affected_ids() == [6, 7, 8, 9, 10, 11]
    # 上游 1-5 不动
    for sid in range(1, 6):
        assert store.load_step(rid, sid).stale is False
    # 下游原因是 upstream_changed(可解释性)
    reasons = {a["step_id"]: a["reason"] for a in impact.affected}
    assert reasons[6] == "param_changed"
    assert all(reasons[s] == "upstream_changed" for s in (7, 8, 9, 10, 11))
    assert store.load_step(rid, 7).state == "stale"


def test_resource_param_no_stale(store):
    """改 threads 8→20:资源参数变更,不影响结果,无需重跑(§5.4 的用户体验差异)。"""
    rid = seeded_run(store)
    impact = apply_change(store, rid, 6, registry=REGISTRY, tool_versions=TOOLS,
                          params_patch={"threads": 20})
    assert impact.reason == "no_change" and impact.affected == []
    assert store.load_step(rid, 6).stale is False
    assert store.load_step(rid, 6).params["threads"] == 20  # 但配置已记录(provenance)


def test_presentation_param_only_own_step(store):
    """改 dpi:只标脏第 10 步,第 11 步不动(呈现参数不传播)。"""
    rid = seeded_run(store)
    impact = apply_change(store, rid, 10, registry=REGISTRY, tool_versions=TOOLS,
                          params_patch={"dpi": 300})
    assert impact.affected_ids() == [10]
    assert store.load_step(rid, 10).state == "stale"
    assert store.load_step(rid, 11).stale is False


def test_method_change_classified(store):
    rid = seeded_run(store)
    impact = apply_change(store, rid, 6, registry=REGISTRY, tool_versions=TOOLS,
                          method="snaphu_smooth")
    reasons = {a["step_id"]: a["reason"] for a in impact.affected}
    assert reasons[6] == "method_changed"


def test_tool_upgrade_marks_affected_steps_only(store):
    """升级 mintpy:只有用 mintpy 的步骤(7/8/9…)标 tool_upgraded,snaphu 步骤(6)不动。"""
    rid = seeded_run(store)
    tools2 = dict(TOOLS, mintpy="1.6.5")
    affected = refresh_run(store, rid, REGISTRY, tools2, apply=True)
    reasons = {a["step_id"]: a["reason"] for a in affected}
    assert reasons.get(7) == "tool_upgraded"
    assert 6 not in reasons  # snaphu 版本没变
    assert store.load_step(rid, 6).stale is False
    # 下游因上游级联(9 依赖 8 依赖 7)
    assert store.load_step(rid, 11).stale is True


def test_param_validation_rejected(store):
    rid = seeded_run(store)
    with pytest.raises(ValueError):
        apply_change(store, rid, 6, registry=REGISTRY, tool_versions=TOOLS,
                     params_patch={"min_coherence": 99})  # 越界(0-1)
    with pytest.raises(ValueError):
        apply_change(store, rid, 6, registry=REGISTRY, tool_versions=TOOLS,
                     method="not_a_method")


def test_preview_does_not_write(store):
    rid = seeded_run(store)
    impact = preview_change(store, rid, 6, registry=REGISTRY, tool_versions=TOOLS,
                            params_patch={"min_coherence": 0.35})
    assert impact.affected_ids() == [6, 7, 8, 9, 10, 11]
    assert store.load_step(rid, 6).stale is False  # 预览不落盘
    assert store.load_step(rid, 6).params["min_coherence"] == 0.25


def test_rerun_estimate_unknown_without_history(store):
    """历史样本 <3 → 时长未知,不编数字(§7.5)。"""
    rid = seeded_run(store)
    impact = apply_change(store, rid, 6, registry=REGISTRY, tool_versions=TOOLS,
                          params_patch={"min_coherence": 0.30})
    assert impact.rerun_minutes is None
    assert "未知" in impact.rerun_basis


def test_artifact_missing_marks_owner_only(store, workspace):
    rid = seeded_run(store)
    art_dir = workspace / "data" / "unw"
    art_dir.mkdir(parents=True)
    (art_dir / "a.unw").write_bytes(b"x")
    from insar_agent.core.filehash import fingerprint_target
    fp = fingerprint_target(art_dir, "stat")
    store.record_artifact(rid, 6, "unw", path="data/unw", kind="IFG_UNWRAPPED",
                          layout="isce2", policy="stat", fp=fp)
    assert verify_artifacts(store, rid, workspace) == []  # 完好

    (art_dir / "a.unw").unlink()  # 删产物
    hits = verify_artifacts(store, rid, workspace)
    assert hits and hits[0]["step_id"] == 6 and hits[0]["reason"] == "artifact_missing"
    assert store.load_step(rid, 6).stale_reason == "artifact_missing"
    assert store.load_step(rid, 7).stale is False  # 只标所属步骤
