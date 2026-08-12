"""stripmap_coseismic 场景包验收:包加载、规则意图、计划组链、区间映射、命令构建。

对拍基准:docs/VALIDATION-isce2-wsl.md(2026-08 ALOS Baja WSL 实测)。
不执行引擎:planner 只做「registry 声明 + 场景覆写」的组合,引擎侧只验证
CommandPlan/XML 渲染与产物声明。
"""

from __future__ import annotations

import re

from insar_agent.brain.facade import Brain
from insar_agent.engines import resolve_builder
from insar_agent.engines import isce2 as isce2_engine
from insar_agent.engines.isce2 import _STRIPMAP_RANGES, STRIPMAP_STEPS
from insar_agent.planner.feasibility import narrow_methods
from insar_agent.planner.plan import make_plan
from insar_agent.registry.capabilities import REGISTRY
from insar_agent.registry.scenarios import SCENARIOS, classify_text, scenario_of
from insar_agent.runtime.discover import discover_artifacts
from insar_agent.runtime.probe import ProbeResult

from tests.test_isce2_stripmap import assert_ranges_contiguous

RUN = {"simulated": 0}

# 步骤 → (方法 id, stripmapApp 区间起, 区间终):与 registry 声明及实测分段对拍
STRIPMAP_CHAIN = {
    3: ("isce2_stripmap_xcorr", "startup", "fine_resample"),
    4: ("isce2_stripmap_ifg", "split_range_spectrum", "filter"),
    5: ("isce2_stripmap_filter", "filter", "filter"),
    6: ("isce2_stripmap_unwrap_snaphu", "filter_low_band", "geocode"),
}


def probe_with_isce2() -> ProbeResult:
    """假 probe:isce2 可用(条带链所需);snaphu 故意缺席 —— 解缠由 stripmapApp
    内置 snaphu 驱动,不需要独立可执行;7-11 步的 mintpy/pyaps 给全,聚焦 3-6 组链。"""
    return ProbeResult(
        engines={"isce2": "2.6.5", "mintpy": "1.6.4", "snaphu": None, "gdal": None,
                 "snap": None, "pystamps": None, "pyaps": "present"},
        credentials={"earthdata": False, "cds": False, "gacos": False})


# ---------------- 场景包加载(闭集校验通过,字段完整) ----------------

def test_pack_loaded_with_complete_fields():
    assert "stripmap_coseismic" in {sc.key for sc in SCENARIOS}
    sc = scenario_of("stripmap_coseismic")
    assert sc.label == "条带同震干涉"
    assert sc.match == "ALOS|条带|stripmap|L波段"
    assert sc.chain == "stripmap"
    assert sc.model == "linear"  # 单对两景无法区分阶跃与线性 → 唯一可辨识参数化
    assert sc.pick_7 == "mintpy_sbas"
    assert sc.diag and sc.reason and sc.region  # 领域字段非空
    assert sc.data_ready is False  # 数据本地已有但需装配(raw 布局 + DEM 转换)
    assert re.fullmatch(r"\d+\.\d+\.\d+", sc.version)  # provenance 需要
    assert sc.cloud_completed == ()  # 全链本机执行,无云端已完成步骤
    assert sc.step_overrides == {
        3: {"method": "isce2_stripmap_xcorr"},
        4: {"method": "isce2_stripmap_ifg"},
        5: {"method": "isce2_stripmap_filter"},
        6: {"method": "isce2_stripmap_unwrap_snaphu"},
        11: {"method": "coherence_mask"},  # 单对无双链,交叉验证诚实降级
    }


def test_pack_knowledge_covers_measured_lessons():
    """渐进披露正文:FBS/FBD 配对、dem.grd 转换、pickle 链约束都写进了领域知识。"""
    body = scenario_of("stripmap_coseismic").knowledge()
    assert "dual2single" in body                        # FBS/FBD 配对 → RESAMPLE_FLAG
    assert "dem.grd" in body and "dem.wgs84" in body    # DEM 转换(GMT netCDF → ISCE)
    assert "pickle" in body and "filter_low_band" in body  # 分段续跑硬规则(教训 2)
    assert not body.startswith("---")                   # frontmatter 已剥离


# ---------------- 规则意图分类(Brain 规则路径,零 LLM) ----------------

def test_rule_intent_classifies_stripmap_text():
    brain = Brain(None)  # provider 整个拔掉:只可能走规则路径
    r = brain.intent("用 ALOS 条带数据做同震干涉")
    assert r.ok and r.source == "rules" and not r.need_form
    assert r.scenario is not None and r.scenario.key == "stripmap_coseismic"


def test_rule_intent_priority_disambiguation():
    """含条带关键词的同震请求归本包(priority=5 先试);纯同震请求仍归 quake。"""
    assert classify_text("用 ALOS 条带数据做同震干涉").key == "stripmap_coseismic"
    assert classify_text("Ridgecrest 地震同震形变").key == "quake"
    for kw in ("ALOS", "条带", "stripmap", "L波段"):
        assert classify_text(f"请帮我处理{kw}数据").key == "stripmap_coseismic", kw


def test_stripmap_methods_scenario_gated():
    """stripmap 方法只在本场景可行:quake 场景下被收窄排除且理由列出允许场景。"""
    for sid, (method, _s, _e) in STRIPMAP_CHAIN.items():
        blocked = {f.method.id: f for f in
                   narrow_methods(REGISTRY[sid], probe_with_isce2(), scenario="quake")}
        assert not blocked[method].ok, f"{method} 不应在 quake 场景可行"
        assert "场景不匹配" in blocked[method].blocked_reason
        assert "stripmap_coseismic" in blocked[method].blocked_reason
        allowed = {f.method.id: f for f in
                   narrow_methods(REGISTRY[sid], probe_with_isce2(),
                                  scenario="stripmap_coseismic")}
        assert allowed[method].ok, allowed[method].blocked_reason


# ---------------- make_plan:3-6 步组出全 stripmap 链 ----------------

def test_make_plan_assembles_stripmap_chain(store):
    store.create_session("s1", "t")
    plan = make_plan(store, "s1", registry=REGISTRY, probe=probe_with_isce2(),
                     scenario=scenario_of("stripmap_coseismic"), workspace="ws")
    assert plan.runnable(), plan.problems
    assert not plan.simulated
    methods = {p.step_id: p.method for p in plan.steps}
    for sid, (method, _s, _e) in STRIPMAP_CHAIN.items():
        assert methods[sid] == method, f"第 {sid} 步应为 {method},实际 {methods[sid]}"
    # 无 cloud_completed:3-6 步都是 pending 待执行,不是 skipped
    states = {p.step_id: p.state for p in plan.steps}
    assert all(states[s] == "pending" for s in (3, 4, 5, 6))
    # snaphu 独立可执行缺席不阻塞第 6 步(stripmapApp 内置 snaphu,只依赖 isce2)
    assert methods[6] == "isce2_stripmap_unwrap_snaphu"
    # 单对两景 → 9 步 linear;11 步诚实降级 coherence_mask;场景进 provenance
    assert methods[9] == "linear" and methods[11] == "coherence_mask"
    assert store.get_run(plan.run_id)["scenario"] == "stripmap_coseismic"
    assert len(store.load_steps(plan.run_id)) == 11
    # 包不钉死 FBS/FBD 配对形态:resample_flag 保持默认空,由数据决定
    params3 = next(p.params for p in plan.steps if p.step_id == 3)
    assert params3["resample_flag"] == ""
    assert params3["dem_path"] == "data/dem/dem.wgs84"


# ---------------- 区间映射与连续性(pickle 链约束) ----------------

def test_ranges_cover_exactly_declared_stripmap_steps():
    """方法 id 映射核对:registry 声明 stripmap 方法的步骤集合 == 区间表键集合。"""
    declared = {cap.id for cap in REGISTRY.values()
                if any("stripmap" in m.id for m in cap.methods)}
    assert declared == set(_STRIPMAP_RANGES) == set(STRIPMAP_CHAIN)


def test_ranges_still_contiguous_with_new_methods():
    """新方法映射后区间表仍首尾相接(tests/test_isce2_stripmap.py 的硬约束)。"""
    assert_ranges_contiguous(STRIPMAP_STEPS, _STRIPMAP_RANGES)
    for sid, (_method, start, end) in STRIPMAP_CHAIN.items():
        assert _STRIPMAP_RANGES[sid] == (start, end), f"cap{sid} 分段漂移"


# ---------------- cap4/5/6 新方法的 CommandPlan(argv/xml/脚本) ----------------

def _build(cap_id: int, method: str, workspace, params=None):
    if params is None:
        params = REGISTRY[cap_id].default_params()  # 与 planner 的传参路径一致
    return isce2_engine.build(cap=REGISTRY[cap_id], method=method, params=params,
                              run=RUN, workspace=workspace)


def test_new_methods_route_to_isce2_builder():
    for sid, (method, _s, _e) in STRIPMAP_CHAIN.items():
        assert resolve_builder(REGISTRY[sid], method,
                               simulated=False) is isce2_engine.build


def test_commandplan_argv_xml_per_new_method(workspace):
    """cap4/5/6(新声明)逐个验收:文件集合、argv、分段脚本、XML 实测形态。"""
    for sid, (method, start, end) in STRIPMAP_CHAIN.items():
        if sid == 3:
            continue  # cap3 已由 tests/test_isce2_stripmap.py 覆盖
        plan = _build(sid, method, workspace)
        xml_rel = f"isce2/stripmapApp_s{sid:02d}.xml"
        script_rel = f"isce2/run_s{sid:02d}.sh"
        assert plan.argv == ["bash", script_rel]
        assert plan.cwd == str(workspace)
        assert plan.env == {"OMP_NUM_THREADS": "8"}
        assert set(plan.files) == {xml_rel, script_rel}
        script = plan.files[script_rel]
        assert "cd isce2" in script
        assert (f"stripmapApp.py stripmapApp_s{sid:02d}.xml "
                f"--start={start} --end={end}") in script
        assert plan.shell_line == f"bash {script_rel}"
        xml = plan.files[xml_rel]
        assert '<component name="insar">' in xml
        assert '<property name="sensor name">ALOS</property>' in xml
        assert '<property name="do unwrap">True</property>' in xml
        assert '<property name="unwrapper name">snaphu</property>' in xml
        assert '<property name="demFilename">data/dem/dem.wgs84</property>' in xml
        assert "topsApp" not in xml


def test_xml_consistent_across_segments(workspace):
    """分段共用同一份 stripmapApp 配置(实测形态):默认参数下各段 XML 内容一致。"""
    xml3 = _build(3, "isce2_stripmap_xcorr", workspace).files["isce2/stripmapApp_s03.xml"]
    for sid, (method, _s, _e) in STRIPMAP_CHAIN.items():
        if sid == 3:
            continue
        xml = _build(sid, method, workspace).files[f"isce2/stripmapApp_s{sid:02d}.xml"]
        assert xml == xml3, f"cap{sid} 段 XML 与配准段不一致"


# ---------------- registry 产物声明(run_ok 判据的真实路径) ----------------

def test_artifacts_declare_measured_stripmap_products():
    """4/5/6 步产物候选含 VALIDATION 报告实测路径;首候选不变(simulate 兼容)。"""
    arts = {sid: {a.id: a for a in REGISTRY[sid].artifacts} for sid in (4, 5, 6)}
    assert arts[4]["ifg"].candidates[0] == "data/ifg"
    assert "isce2/interferogram/topophase.flat" in arts[4]["ifg"].candidates
    assert "isce2/interferogram/filt_topophase.flat" in arts[4]["ifg"].candidates
    assert arts[5]["ifg_filt"].candidates[0] == "data/ifg_filt"
    assert "isce2/interferogram/filt_topophase.flat" in arts[5]["ifg_filt"].candidates
    assert arts[6]["unw"].candidates[0] == "data/unw"
    assert "isce2/interferogram/filt_topophase.unw" in arts[6]["unw"].candidates
    assert "isce2/interferogram/filt_topophase.unw.geo" in arts[6]["unw"].candidates
    # run_ok 声明了对应产物的存在检查(exit_code 之外的领域判定)
    for sid, art_id in ((4, "ifg"), (5, "ifg_filt"), (6, "unw")):
        checks = {(c.check, c.id) for c in REGISTRY[sid].run_ok}
        assert ("artifact_exists", art_id) in checks


def test_discover_finds_stripmap_layout(workspace):
    """条带布局(isce2/interferogram/*)下 4/5/6 的产物发现命中实测路径。"""
    igram = workspace / "isce2" / "interferogram"
    igram.mkdir(parents=True)
    for name in ("topophase.flat", "filt_topophase.flat",
                 "filt_topophase.unw", "filt_topophase.unw.geo"):
        (igram / name).write_bytes(b"x")
    for sid, art_id, rel in (
            (4, "ifg", "isce2/interferogram/topophase.flat"),
            (5, "ifg_filt", "isce2/interferogram/filt_topophase.flat"),
            (6, "unw", "isce2/interferogram/filt_topophase.unw")):
        found, missing = discover_artifacts(workspace, REGISTRY[sid].artifacts)
        by_id = {f.spec.id: f for f in found}
        assert art_id in by_id, f"cap{sid} 的 {art_id} 未被发现"
        assert by_id[art_id].path == workspace / rel
        assert not [m for m in missing if m.spec.id == art_id]
