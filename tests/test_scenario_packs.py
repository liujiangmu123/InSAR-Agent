"""E7 场景技能包验收:闭集校验、三场景等价(与硬编码时代同值)、渐进披露、版本。"""

import re

import pytest

from insar_agent.registry.scenarios import (
    PACKS_DIR,
    SCENARIOS,
    ScenarioPackWarning,
    classify_text,
    load_scenarios,
    scenario_of,
)

# ---- 等价基准:硬编码时代 scenarios.py(b7f13fe)的全部字段值,逐字搬运 ----
EXPECTED = {
    "quake": dict(
        label="同震形变",
        match=r"地震|同震|quake|Ridgecrest|玛多",
        chain="SBAS",
        model="step",
        pick_7="mintpy_sbas",
        diag="同震形变量级大(数十 cm),HyP3 已提供解缠相位与相干性",
        reason="阶跃形变 → step(20190706) 模型;日期取自实测配置",
        data_ready=True,
        region="Ridgecrest(加州)",
        dates="2019-06-10 — 2019-08-15",
        scenes="7 个获取日期 / 11 个干涉对",
        step_overrides={
            9: {"method": "step", "params": {"step_date": "20190706T0320"}},
            11: {"method": "coherence_mask"},
        },
        cloud_completed=(2, 3, 4, 5, 6),
    ),
    "permafrost": dict(
        label="冻土季节冻融",
        match=r"冻土|permafrost|玉树|青海|青藏",
        chain="SBAS",
        model="poly_periodic",
        pick_7="mintpy_sbas",
        diag="冻土区植被与季节冻融导致时间去相干严重,点状 PS 目标稀疏",
        reason="低相干面状形变 → SBAS 优于 PS;形变含季节冻融 → poly_periodic 模型",
        data_ready=False,
        region="青海玉树",
        dates="2020-01 — 2023-12",
        scenes="数据待获取",
        step_overrides={
            9: {"method": "poly_periodic", "params": {"periods": [1, 0.5], "poly_order": 1}},
        },
        cloud_completed=(),
    ),
    "landslide": dict(
        label="滑坡点状目标",
        match=r"滑坡|landslide|雅鲁藏布",
        chain="PS",
        model="linear",
        pick_7="pystamps_ps",
        diag="陡坡地形几何畸变明显,裸岩区高相干点密集",
        reason="高相干点状目标 → PS 链;需 ISCE2→PyStamps 桥",
        data_ready=False,
        region="雅鲁藏布江",
        dates="待定",
        scenes="数据待获取",
        step_overrides={
            7: {"method": "pystamps_ps"},
            9: {"method": "linear"},
        },
        cloud_completed=(),
    ),
}


# ---------------- 三场景等价性 ----------------

def test_three_scenarios_equivalent_to_hardcoded():
    # stripmap_coseismic(priority=5)排最前;volcano=15、subsidence=25、
    # lt1_gamma=35、teaching=90 为后续新增;原三包等价性逐字段锁定,
    # 新包由下方 test_subsidence_* / test_volcano_* / test_teaching_* / test_lt1_gamma_* 验收
    assert tuple(sc.key for sc in SCENARIOS) == (
        "stripmap_coseismic", "quake", "volcano", "permafrost", "subsidence",
        "landslide", "lt1_gamma", "teaching")
    for key, want in EXPECTED.items():
        sc = scenario_of(key)
        assert sc is not None, key
        for field_name, value in want.items():
            assert getattr(sc, field_name) == value, f"{key}.{field_name}"


def test_classification_behaviour_unchanged():
    assert classify_text("我想做玉树冻土的时序分析").key == "permafrost"
    assert classify_text("Ridgecrest 地震同震形变").key == "quake"
    assert classify_text("雅鲁藏布江的滑坡隐患点").key == "landslide"
    # 多场景命中(青海→permafrost,玛多/地震→quake)时保持硬编码时代的优先序:quake 先试
    assert classify_text("青海玛多地震同震反演").key == "quake"
    assert classify_text("随便说点什么") is None  # 不猜,交给 LLM/表单


def test_scenario_of_unknown_key():
    assert scenario_of("nonexistent") is None


# ---------------- version(provenance)----------------

def test_version_present_for_provenance():
    for sc in SCENARIOS:
        assert re.fullmatch(r"\d+\.\d+\.\d+", sc.version), f"{sc.key} version={sc.version!r}"


# ---------------- knowledge() 渐进披露 ----------------

def test_knowledge_lazy_load():
    scs = load_scenarios(PACKS_DIR)  # 独立实例,避免污染模块级单例的缓存
    quake = next(s for s in scs if s.key == "quake")
    assert "_knowledge" not in quake.__dict__  # 加载阶段未读正文(描述常驻,全文按需)
    body = quake.knowledge()
    assert "20190706T0320" in body  # 选参依据(Ridgecrest 实测)
    assert "ERA5" in body  # 缓存路径约定
    assert not body.startswith("---")  # frontmatter 已剥离
    assert "name: quake" not in body
    assert quake.knowledge() is body  # 二次调用命中缓存
    assert "_knowledge" in quake.__dict__


def test_knowledge_missing_file_returns_empty():
    quake = scenario_of("quake")
    broken = type(quake)(**{**{f: getattr(quake, f) for f in EXPECTED["quake"]},
                            "key": "quake", "skill_path": "Z:/不存在/SKILL.md"})
    assert broken.knowledge() == ""


# ---------------- 闭集校验(坏 frontmatter 拒载/警告)----------------

GOOD_SKILL = """---
name: {name}
description: 测试场景描述
metadata:
  version: 0.1.0
  label: 测试
  match: 'test-pack-{name}'
  chain: SBAS
  model: linear
  pick_7: mintpy_sbas
---

正文知识。
"""


def write_pack(root, dirname, skill_text, overrides_text=None):
    d = root / dirname
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(skill_text, encoding="utf-8")
    if overrides_text is not None:
        (d / "overrides.yaml").write_text(overrides_text, encoding="utf-8")


def test_valid_minimal_pack_loads(tmp_path):
    write_pack(tmp_path, "foo", GOOD_SKILL.format(name="foo"))
    scs = load_scenarios(tmp_path)
    assert [s.key for s in scs] == ["foo"]
    assert scs[0].version == "0.1.0"
    assert scs[0].step_overrides == {} and scs[0].cloud_completed == ()


def test_missing_description_rejected(tmp_path):
    write_pack(tmp_path, "foo",
               "---\nname: foo\nmetadata:\n  version: 0.1.0\n---\n正文\n")
    with pytest.warns(ScenarioPackWarning, match="缺 name/description"):
        assert load_scenarios(tmp_path) == ()


def test_missing_name_rejected(tmp_path):
    write_pack(tmp_path, "foo",
               "---\ndescription: 只有描述\nmetadata:\n  version: 0.1.0\n---\n正文\n")
    with pytest.warns(ScenarioPackWarning, match="缺 name/description"):
        assert load_scenarios(tmp_path) == ()


def test_no_frontmatter_rejected(tmp_path):
    write_pack(tmp_path, "foo", "没有 frontmatter 的普通文档\n")
    with pytest.warns(ScenarioPackWarning, match="frontmatter"):
        assert load_scenarios(tmp_path) == ()


def test_unparseable_frontmatter_rejected(tmp_path):
    write_pack(tmp_path, "foo", "---\nname: [未闭合\n---\n正文\n")
    with pytest.warns(ScenarioPackWarning, match="frontmatter"):
        assert load_scenarios(tmp_path) == ()


def test_name_directory_mismatch_rejected(tmp_path):
    write_pack(tmp_path, "bar", GOOD_SKILL.format(name="foo"))
    with pytest.warns(ScenarioPackWarning, match="目录名不一致"):
        assert load_scenarios(tmp_path) == ()


def test_missing_required_metadata_rejected(tmp_path):
    text = ("---\nname: foo\ndescription: 描述\nmetadata:\n"
            "  version: 0.1.0\n  label: 测试\n---\n正文\n")
    write_pack(tmp_path, "foo", text)
    with pytest.warns(ScenarioPackWarning, match="缺必填字段"):
        assert load_scenarios(tmp_path) == ()


def test_bad_regex_rejected(tmp_path):
    text = GOOD_SKILL.format(name="foo").replace("'test-pack-foo'", "'te[st'")
    write_pack(tmp_path, "foo", text)
    with pytest.warns(ScenarioPackWarning, match="正则不合法"):
        assert load_scenarios(tmp_path) == ()


def test_unknown_top_level_field_warns_but_loads(tmp_path):
    text = GOOD_SKILL.format(name="foo").replace(
        "description: 测试场景描述", "description: 测试场景描述\nbanana: 1")
    write_pack(tmp_path, "foo", text)
    with pytest.warns(ScenarioPackWarning, match="未知 frontmatter 字段.*banana"):
        scs = load_scenarios(tmp_path)
    assert [s.key for s in scs] == ["foo"]


def test_unknown_metadata_field_warns_but_loads(tmp_path):
    text = GOOD_SKILL.format(name="foo").replace(
        "  label: 测试", "  label: 测试\n  magic: true")
    write_pack(tmp_path, "foo", text)
    with pytest.warns(ScenarioPackWarning, match="未知 metadata 字段.*magic"):
        scs = load_scenarios(tmp_path)
    assert [s.key for s in scs] == ["foo"]


def test_missing_version_warns_but_loads(tmp_path):
    text = GOOD_SKILL.format(name="foo").replace("  version: 0.1.0\n", "")
    write_pack(tmp_path, "foo", text)
    with pytest.warns(ScenarioPackWarning, match="version"):
        scs = load_scenarios(tmp_path)
    assert [s.key for s in scs] == ["foo"]
    assert scs[0].version == ""


def test_bad_overrides_yaml_rejected(tmp_path):
    write_pack(tmp_path, "foo", GOOD_SKILL.format(name="foo"),
               overrides_text="step_overrides: {9: {method: linear}")
    with pytest.warns(ScenarioPackWarning, match="overrides.yaml 解析失败"):
        assert load_scenarios(tmp_path) == ()


def test_overrides_unknown_field_warns_but_loads(tmp_path):
    write_pack(tmp_path, "foo", GOOD_SKILL.format(name="foo"),
               overrides_text="step_overrides: {}\nextra_key: 1\n")
    with pytest.warns(ScenarioPackWarning, match="overrides.yaml 未知字段.*extra_key"):
        scs = load_scenarios(tmp_path)
    assert [s.key for s in scs] == ["foo"]


def test_overrides_step_ids_coerced_to_int(tmp_path):
    overrides = 'step_overrides:\n  "9":\n    method: linear\ncloud_completed: [2]\n'
    write_pack(tmp_path, "foo", GOOD_SKILL.format(name="foo"), overrides_text=overrides)
    scs = load_scenarios(tmp_path)
    assert scs[0].step_overrides == {9: {"method": "linear"}}
    assert scs[0].cloud_completed == (2,)


def test_rejected_pack_does_not_block_others(tmp_path):
    """一个坏包拒载,不影响同目录其余好包(闭集校验按包隔离)。"""
    write_pack(tmp_path, "bad", "没有 frontmatter\n")
    write_pack(tmp_path, "good", GOOD_SKILL.format(name="good"))
    with pytest.warns(ScenarioPackWarning):
        scs = load_scenarios(tmp_path)
    assert [s.key for s in scs] == ["good"]


def test_priority_orders_classification(tmp_path):
    """priority 小者先参与 classify_text 匹配(歧义消解顺序外置到包)。"""
    first = GOOD_SKILL.format(name="aaa").replace("  version: 0.1.0",
                                                  "  version: 0.1.0\n  priority: 50")
    second = GOOD_SKILL.format(name="zzz").replace("'test-pack-zzz'", "'test-pack'").replace(
        "  version: 0.1.0", "  version: 0.1.0\n  priority: 1")
    write_pack(tmp_path, "aaa", first.replace("'test-pack-aaa'", "'test-pack'"))
    write_pack(tmp_path, "zzz", second)
    scs = load_scenarios(tmp_path)
    assert [s.key for s in scs] == ["zzz", "aaa"]  # 目录名倒序,但 priority 生效


# ---------------- 沉降 / 火山包(Phase 15) ----------------

def test_eight_packs_keywords_do_not_preempt():
    """每个包的每个 match 关键词只命中自己,闭集互不重叠(classify_text 先到先得)。

    「国产L波段」含子串「L波段」(stripmap_coseismic, priority=5),单独拿出来
    会被条带包先吃 —— 这是正则先到先得,不是漏配。lt1_gamma 靠「陆探/LT-1/
    LT1/gamma布局」命中;本循环跳过该词,下方 test_lt1_gamma_* 覆盖真实用户句。
    """
    assert len(SCENARIOS) == 8
    substring_preempted = {"国产L波段"}
    for sc in SCENARIOS:
        for kw in sc.match.split("|"):
            if kw in substring_preempted:
                continue
            hit = classify_text(f"请分析{kw}相关形变")
            assert hit is not None and hit.key == sc.key, (
                f"{sc.key} 关键词 {kw!r} 命中了 {getattr(hit, 'key', None)}")


def test_subsidence_pack_fields_and_overrides():
    sc = scenario_of("subsidence")
    assert sc is not None
    assert sc.label == "城市地面沉降"
    assert sc.match == "沉降|subsidence|抽水沉降|沉降漏斗"
    assert sc.chain == "SBAS" and sc.model == "poly_periodic" and sc.pick_7 == "mintpy_sbas"
    assert sc.data_ready is False
    assert sc.cloud_completed == ()
    assert sc.step_overrides[7]["params"]["max_temporal_baseline"] == 90
    assert sc.step_overrides[8]["params"]["ramp"] == "linear"
    assert sc.step_overrides[9]["method"] == "poly_periodic"
    assert sc.step_overrides[9]["params"]["periods"] == [1]
    assert sc.step_overrides[10]["params"]["figure_set"] == [
        "velocity", "coherence", "mask", "points_timeseries"]
    body = sc.knowledge()
    assert "90" in body and "年周期" in body
    assert "稳定基岩" in body or "稳定区" in body
    assert "漏斗" in body


def test_volcano_pack_fields_and_deramp_redline():
    sc = scenario_of("volcano")
    assert sc is not None
    assert sc.label == "火山形变"
    assert sc.match == "火山|volcano|岩浆房|喷发"
    assert sc.chain == "SBAS" and sc.model == "exponential" and sc.pick_7 == "mintpy_sbas"
    assert sc.data_ready is False
    assert sc.cloud_completed == ()
    assert sc.step_overrides[8]["params"]["ramp"] == "no"
    assert sc.step_overrides[9]["method"] == "exponential"
    body = sc.knowledge()
    assert "deramp=no" in body
    assert "红线" in body
    assert "长波长" in body


def test_mixed_text_keeps_existing_pack_priority():
    """新包不得抢走既有包的关键词;混合文本仍按 priority 消解。"""
    assert classify_text("冻土融沉监测").key == "permafrost"          # 冻土先于沉降
    assert classify_text("监测某市地面沉降").key == "subsidence"
    assert classify_text("火山形变时间序列").key == "volcano"
    assert classify_text("火山地震同震").key == "quake"               # 同震/地震仍归 quake
    assert classify_text("Ridgecrest 地震同震形变").key == "quake"
    assert classify_text("雅鲁藏布江的滑坡隐患点").key == "landslide"
    assert classify_text("冻土教学演示").key == "permafrost"          # 教学包 priority=90 不抢冻土
    assert classify_text("滑坡课堂作业").key == "landslide"           # 不抢滑坡


def test_subsidence_and_volcano_plan_applies_overrides(store):
    """场景包覆写穿透到计划:沉降 ramp=linear/periods=[1];火山 ramp=no/exponential。"""
    from insar_agent.planner.plan import make_plan
    from insar_agent.registry.capabilities import REGISTRY
    from insar_agent.runtime.probe import ProbeResult

    probe = ProbeResult(
        engines={"isce2": "2.6.5", "mintpy": "1.6.4", "snaphu": "2.0.7", "gdal": "3.8",
                 "snap": "9", "pystamps": "0.3.4", "pyaps": "0.3.6"},
        credentials={"earthdata": True, "cds": True, "gacos": False})
    store.create_session("s1", "t")

    sub = make_plan(store, "s1", registry=REGISTRY, probe=probe,
                    scenario=scenario_of("subsidence"), workspace="ws")
    assert sub.runnable()
    sub_steps = {p.step_id: p for p in sub.steps}
    assert sub_steps[7].params["max_temporal_baseline"] == 90
    assert sub_steps[7].params["processor"] == "hyp3"  # 数据面默认仍是 HyP3
    assert sub_steps[8].params["ramp"] == "linear"
    assert sub_steps[9].method == "poly_periodic"
    assert sub_steps[9].params["periods"] == [1]
    assert sub_steps[10].params["figure_set"] == [
        "velocity", "coherence", "mask", "points_timeseries"]

    vol = make_plan(store, "s1", registry=REGISTRY, probe=probe,
                    scenario=scenario_of("volcano"), workspace="ws")
    assert vol.runnable()
    vol_steps = {p.step_id: p for p in vol.steps}
    assert vol_steps[8].params["ramp"] == "no"
    assert vol_steps[9].method == "exponential"
    # 核心仍是 11 步,没有顺手把分析步 20-28 拉进来
    assert [p.step_id for p in vol.steps] == list(range(1, 12))


# ---------------- 教学 / 陆探一号包(R6 / R2c) ----------------

_TEACHING_FIGURES = ["velocity", "coherence", "mask", "network", "points_timeseries"]


def test_teaching_pack_fields_and_overrides():
    sc = scenario_of("teaching")
    assert sc is not None
    assert sc.key == "teaching"
    assert sc.label == "教学演示"
    assert sc.match == "教学|课堂|演示|homework|teaching|tutorial"
    assert sc.chain == "SBAS" and sc.model == "linear" and sc.pick_7 == "mintpy_sbas"
    assert sc.data_ready is False
    assert sc.cloud_completed == ()
    assert "demo" not in sc.match.split("|")  # 单独 demo 太宽,故意不用
    assert sc.step_overrides[10]["params"]["figure_set"] == _TEACHING_FIGURES
    assert sc.step_overrides[11]["method"] == "coherence_mask"
    assert 7 not in sc.step_overrides  # 不改数据面,不假装 HyP3/GAMMA 已就绪
    body = sc.knowledge()
    assert "小数据" in body and "快节奏" in body
    assert "图件全开" in body
    assert "模式 C" in body and "零" in body
    assert "strict" in body and "free" in body
    assert "编造" in body
    assert "coherence_mask" in body


def test_teaching_classify():
    assert classify_text("给我做一个课堂演示用的教学流程").key == "teaching"
    assert classify_text("Ridgecrest 地震").key == "quake"
    assert classify_text("homework tutorial for class").key == "teaching"
    assert classify_text("run a demo workflow") is None  # 不含单独 demo


def test_lt1_gamma_pack_fields_and_overrides():
    sc = scenario_of("lt1_gamma")
    assert sc is not None
    assert sc.key == "lt1_gamma"
    assert sc.label == "陆探一号(GAMMA 布局)"
    assert sc.match == "陆探|LT-1|LT1|国产L波段|gamma布局"
    assert sc.chain == "SBAS" and sc.model == "linear" and sc.pick_7 == "mintpy_sbas"
    assert sc.data_ready is False
    assert sc.cloud_completed == ()
    assert sc.step_overrides[7]["params"]["processor"] == "gamma"
    assert "unw_pattern" not in sc.step_overrides[7].get("params", {})
    assert "cor_pattern" not in sc.step_overrides[7].get("params", {})
    assert sc.step_overrides[9]["method"] == "linear"
    body = sc.knowledge()
    assert "2023-12" in body
    assert "8 天" in body and "4 天" in body
    assert "自然资源卫星遥感云服务平台" in body
    assert "SARscape" in body and "prep_gamma" in body
    assert "2.7" in body and "8.6" in body and "3.7" in body
    assert "ISPRS" in body
    assert "硬门" in body
    assert "contract.yaml" in body
    # 惯例 glob 是相对 stack 的模式,不是某台机器上的绝对数据路径
    assert "diff*rlks.unw" in body and "*filt*rlks.cor" in body


def test_lt1_gamma_classify():
    """「沉降」是 subsidence 关键词且 priority 25 先于 lt1_gamma(35)——先到先得。

    「用陆探一号做沉降」因此归沉降,不是漏配。不含「沉降」的陆探/LT-1/GAMMA
    布局句才命中本包。单独「国产L波段」含子串「L波段」,归条带包。
    """
    assert classify_text("城市地面沉降").key == "subsidence"
    assert classify_text("用陆探一号做沉降").key == "subsidence"
    assert classify_text("LT-1 GAMMA 布局导入").key == "lt1_gamma"
    assert classify_text("用陆探一号做时序").key == "lt1_gamma"
    assert classify_text("gamma布局").key == "lt1_gamma"
    assert classify_text("国产L波段").key == "stripmap_coseismic"


def test_teaching_and_lt1_plan_applies_overrides(store):
    """场景包覆写穿透到计划:教学图件全开+coherence_mask;陆探 processor=gamma。"""
    from insar_agent.planner.plan import make_plan
    from insar_agent.registry.capabilities import REGISTRY
    from insar_agent.runtime.probe import ProbeResult

    probe = ProbeResult(
        engines={"isce2": "2.6.5", "mintpy": "1.6.4", "snaphu": "2.0.7", "gdal": "3.8",
                 "snap": "9", "pystamps": "0.3.4", "pyaps": "0.3.6"},
        credentials={"earthdata": True, "cds": True, "gacos": False})
    store.create_session("s2", "t")

    tea = make_plan(store, "s2", registry=REGISTRY, probe=probe,
                    scenario=scenario_of("teaching"), workspace="ws")
    assert tea.runnable()
    tea_steps = {p.step_id: p for p in tea.steps}
    assert tea_steps[10].params["figure_set"] == _TEACHING_FIGURES
    assert tea_steps[11].method == "coherence_mask"
    assert tea_steps[7].params["processor"] == "hyp3"  # 教学不改数据面
    assert [p.step_id for p in tea.steps] == list(range(1, 12))
    assert all(p.state != "skipped" for p in tea.steps)  # 无 cloud_completed

    lt1 = make_plan(store, "s2", registry=REGISTRY, probe=probe,
                    scenario=scenario_of("lt1_gamma"), workspace="ws")
    assert lt1.runnable()
    lt1_steps = {p.step_id: p for p in lt1.steps}
    assert lt1_steps[7].params["processor"] == "gamma"
    assert lt1_steps[9].method == "linear"
    assert [p.step_id for p in lt1.steps] == list(range(1, 12))
    assert all(p.state != "skipped" for p in lt1.steps)
