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
    assert tuple(sc.key for sc in SCENARIOS) == ("quake", "permafrost", "landslide")
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
    text = "---\nname: foo\ndescription: 描述\nmetadata:\n  version: 0.1.0\n  label: 测试\n---\n正文\n"
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
