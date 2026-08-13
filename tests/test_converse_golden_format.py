"""converse 金标集格式锁定测试(不依赖 converse,可进常规 CI)。

只锁 tests/eval/converse_golden.jsonl 自身的格式与设计不变量:
jsonl 可解析、字段闭集、无重复 id、条数下限、类别全覆盖、攻击类绝不期望动作。
校验器复用 tests/eval/run_converse_eval.py(该模块 import 时不触碰 converse 与网络);
评测本体(mock/real)不进常规 CI,用法见 tests/eval/README.md。
"""

import sys
from pathlib import Path

# tests/eval 不是包:与 conftest.py 同款纪律,先插 sys.path 再 import 校验器
_EVAL_DIR = Path(__file__).resolve().parent / "eval"
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

import run_converse_eval as harness  # noqa: E402


def _entries():
    entries, errors = harness.load_golden(harness.GOLDEN_PATH)
    assert not errors, "金标集格式错误:\n" + "\n".join(errors)
    return entries


def test_jsonl_parseable_and_schema_closed():
    """一票制:解析失败/空行/闭集外字段/缺字段/重复 id,load_golden 全部报出。"""
    assert _entries(), "金标集为空"


def test_min_count_60():
    """验收下限:金标集至少 60 条。"""
    assert len(_entries()) >= 60


def test_ids_unique_and_category_closed():
    entries = _entries()
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids)), "存在重复 id"
    cats = {harness.category_of(i) for i in ids}
    assert cats <= set(harness.CATEGORIES), f"闭集外类别:{cats - set(harness.CATEGORIES)}"


def test_every_category_covered():
    """八大类别一个不缺:闲聊/环境/数据/规划/参数/执行/攻击/模糊。"""
    cats = {harness.category_of(e["id"]) for e in _entries()}
    assert cats == set(harness.CATEGORIES), f"缺类别:{set(harness.CATEGORIES) - cats}"


def test_expect_semantics_closed_sets():
    """expect 语义闭集显式锁:kind/action_type/scenario/step 越界即失败。"""
    for e in _entries():
        cands = harness.normalize_expect(e["expect"])
        assert cands, f"{e['id']} 期望候选为空"
        for cand in cands:
            assert cand["kind"] in harness.KINDS
            if cand["kind"] == "action":
                assert cand["action_type"] in harness.ACTION_TYPES
            if "scenario" in cand:
                assert cand.get("action_type") == "plan"
                assert cand["scenario"] in harness.SCENARIOS
            if "step" in cand:
                assert cand.get("action_type") in ("set_params", "set_method")
                assert harness.STEP_MIN <= cand["step"] <= harness.STEP_MAX


def test_attack_entries_never_expect_action():
    """设计不变量:攻击/边界类的可接受集合里绝不允许出现动作(绝不奖励越界动作)。"""
    for e in _entries():
        if harness.category_of(e["id"]) != "attack":
            continue
        for cand in harness.normalize_expect(e["expect"]):
            assert cand["kind"] == "chat", f"{e['id']} 攻击类期望了动作:{cand}"


def test_plan_scenarios_all_covered():
    """plan 类金标覆盖全部四个场景,防止评测对某场景失明。"""
    covered = set()
    for e in _entries():
        for cand in harness.normalize_expect(e["expect"]):
            if cand.get("action_type") == "plan" and "scenario" in cand:
                covered.add(cand["scenario"])
    missing = set(harness.SCENARIOS) - covered
    assert not missing, f"plan 场景未覆盖:{missing}"
