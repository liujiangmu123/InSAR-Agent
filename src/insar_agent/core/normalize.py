"""规范化哈希:键序无关、浮点归一、嵌套无歧义。

解决 AGENT-DESIGN §5.2 的三个坑:
  坑一  dict 键序     —— 按「已哈希的 key」排序(aiida hashing.py:159-176 的做法),
                        key 类型混杂(str/int)时也有全序,不会 TypeError。
  坑二  浮点表示     —— 先格式化为 12 位有效数字字符串再哈希,-0.0 归一为 0.0
                        (aiida hashing.py:197-202/310-320)。0.3 与 0.1+0.2 同哈希。
  坑三  嵌套结构歧义 —— 类型标签 + 长度前缀 + 容器闭合标记(redun bencode tag 前缀
                        路线,hashing.py:47-54;不引入 blake2b,sha256 够用)。
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

_FLOAT_SIG_DIGITS = 12


class UnrepresentableError(TypeError):
    """值无法确定性 canonical 化。

    显式失败,绝不静默丢弃 —— Snakemake 的 params repr 集合比较会把
    不可比对象悄悄折叠成同一形态(reference/AGENT_PRODUCTS_LEARNING §6
    「明确不照搬」),漏检直接毒化指纹。子类化 TypeError 保持既有捕获语义。
    """


# 类型标签(单字节),避免跨类型 pre-image 碰撞
_T_NULL = b"N"
_T_BOOL = b"B"
_T_INT = b"I"
_T_FLOAT = b"F"
_T_STR = b"S"
_T_BYTES = b"Y"
_T_LIST = b"L"
_T_DICT = b"D"
_T_END = b"E"  # 容器闭合,防 [[1],[2]] 与 [[1,2]] 同像


def _normalize_float(x: float) -> str:
    if math.isnan(x):
        return "nan"
    if math.isinf(x):
        return "inf" if x > 0 else "-inf"
    if x == 0.0:  # 同时覆盖 -0.0(-0.0 == 0.0 为 True)
        return "0"
    return f"{x:.{_FLOAT_SIG_DIGITS}g}"


def normalize_value(value: Any) -> bytes:
    """把任意 JSON 风格值转成确定性字节 pre-image。"""
    if value is None:
        return _T_NULL
    if isinstance(value, bool):  # 必须在 int 之前(bool 是 int 子类)
        return _T_BOOL + (b"1" if value else b"0")
    if isinstance(value, int):
        b = str(value).encode()
        return _T_INT + str(len(b)).encode() + b":" + b
    if isinstance(value, float):
        # 整数值浮点(2.0)与整数(2)同像:参数从 YAML/JSON 往返时类型会漂移
        if value.is_integer() and abs(value) < 2**53:
            return normalize_value(int(value))
        b = _normalize_float(value).encode()
        return _T_FLOAT + str(len(b)).encode() + b":" + b
    if isinstance(value, str):
        b = value.encode("utf-8")
        return _T_STR + str(len(b)).encode() + b":" + b
    if isinstance(value, bytes):
        return _T_BYTES + str(len(value)).encode() + b":" + value
    if isinstance(value, (list, tuple)):
        out = _T_LIST
        for item in value:
            out += normalize_value(item)
        return out + _T_END
    if isinstance(value, (set, frozenset)):
        # 集合无序:按元素 pre-image 排序
        out = _T_LIST
        for pre in sorted(normalize_value(v) for v in value):
            out += pre
        return out + _T_END
    if isinstance(value, dict):
        # 坑一:按「已规范化的 key」的字节排序,而非 key 原值
        pairs = sorted(
            (normalize_value(k), normalize_value(v)) for k, v in value.items()
        )
        out = _T_DICT
        for k_pre, v_pre in pairs:
            out += k_pre + v_pre
        return out + _T_END
    raise UnrepresentableError(f"unhashable value type for fingerprint: {type(value)!r}")


def hash_struct(value: Any) -> str:
    """规范化 sha256,返回 64 位十六进制。"""
    return hashlib.sha256(normalize_value(value)).hexdigest()
