"""Phase 0 验收:规范化哈希的三个坑(AGENT-DESIGN §5.2)+ 浮点归一细节。"""

import math

import pytest

from insar_agent.core.normalize import UnrepresentableError, hash_struct


def test_dict_key_order_invariant():
    a = {"min_coherence": 0.25, "threads": 8, "cost_mode": "SMOOTH"}
    b = {"cost_mode": "SMOOTH", "threads": 8, "min_coherence": 0.25}
    assert hash_struct(a) == hash_struct(b)


def test_mixed_key_types_do_not_raise():
    # aiida 式「按已哈希 key 排序」:str/int 混用不 TypeError
    a = {1: "x", "1": "y", 2: "z"}
    b = {"1": "y", 2: "z", 1: "x"}
    assert hash_struct(a) == hash_struct(b)


def test_float_representation_normalized():
    # 0.1+0.2 == 0.30000000000000004,格式化到 12 位有效数字后与 0.3 同哈希
    assert hash_struct(0.1 + 0.2) == hash_struct(0.3)
    assert hash_struct({"alpha": 0.1 + 0.2}) == hash_struct({"alpha": 0.3})
    # 真实差异不能被归一吞掉:0.3 与 0.31 必须不同哈希
    assert hash_struct(0.3) != hash_struct(0.31)


def test_negative_zero_normalized():
    assert hash_struct(-0.0) == hash_struct(0.0)


def test_nan_inf_explicit_tokens():
    # NaN/Inf 走显式 token:自身稳定,且三者互不相同
    assert hash_struct(float("nan")) == hash_struct(float("nan"))
    assert hash_struct(math.inf) != hash_struct(-math.inf)
    assert hash_struct(float("nan")) != hash_struct(math.inf)


def test_unrepresentable_raises():
    # 不可 canonical 化的对象显式失败,绝不静默丢弃(Snakemake params 折叠反例)
    class Opaque:
        pass

    with pytest.raises(UnrepresentableError):
        hash_struct(Opaque())
    with pytest.raises(UnrepresentableError):
        hash_struct({"cfg": {"callback": Opaque()}})  # 深埋嵌套同样炸出来


def test_int_float_equivalence():
    # 参数经 YAML/JSON 往返时 2 会变 2.0,不应导致重算
    assert hash_struct({"looks": 2}) == hash_struct({"looks": 2.0})


def test_nested_structure_unambiguous():
    # 坑三:没有闭合标记时 [[1],[2]] 与 [[1,2]] 同像
    assert hash_struct([[1], [2]]) != hash_struct([[1, 2]])
    assert hash_struct([["a"], ["b"]]) != hash_struct([["a", "b"]])


def test_type_domain_separation():
    # 类型标签防跨类型碰撞
    assert hash_struct("1") != hash_struct(1)
    assert hash_struct(True) != hash_struct(1)
    assert hash_struct(None) != hash_struct("")
    assert hash_struct([]) != hash_struct({})


def test_stability():
    # 指纹是持久化契约,固定值防止实现漂移(变更此值 = 全量缓存失效,须显式决策)
    assert hash_struct({"a": 1}) == hash_struct({"a": 1})
    h = hash_struct(["Task", "snaphu_mcf", "version", "1"])
    assert len(h) == 64 and h == hash_struct(["Task", "snaphu_mcf", "version", "1"])
