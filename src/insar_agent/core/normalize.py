"""规范化哈希:键序无关、浮点归一、嵌套无歧义。

解决 AGENT-DESIGN §5.2 的三个坑:
  坑一  dict 键序     —— 按「已哈希的 key」排序(aiida hashing.py:159-176 的做法),
                        key 类型混杂(str/int)时也有全序,不会 TypeError。
  坑二  浮点表示     —— 先格式化为 12 位有效数字字符串再哈希,-0.0 归一为 0.0
                        (aiida hashing.py:197-202/310-320)。0.3 与 0.1+0.2 同哈希。
  坑三  嵌套结构歧义 —— 类型标签 + 长度前缀 + 容器闭合标记(redun bencode tag 前缀
                        路线,hashing.py:47-54;不引入 blake2b,sha256 够用)。

实现说明(2026-08 性能改造,输出逐字节不变,由 tests/test_hash_semantics_lock.py
金样钉死):pre-image 累积进共享 bytearray(旧版 bytes += 在大容器上是 O(n²) 拷贝);
精确类型直接分派,子类走 _write_fallback 的 isinstance 链保持旧语义(IntEnum、
OrderedDict 等仍按旧路径编码)。dict/set 的子元素因需排序仍各自物化 pre-image。
"""

from __future__ import annotations

import hashlib
from typing import Any

_FLOAT_SIG_DIGITS = 12
_FLOAT_FMT = f".{_FLOAT_SIG_DIGITS}g"
_INT_EXACT_LIMIT = 2 ** 53  # 浮点可精确表示整数的上界


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
    # x != x 即 NaN(免 math.isnan 调用);±inf 不必单列:'.12g' 对无穷输出
    # 恰为 "inf"/"-inf",与旧显式分支逐字符一致(金样锁定)
    if x != x:
        return "nan"
    if x == 0.0:  # 同时覆盖 -0.0(-0.0 == 0.0 为 True)
        return "0"
    return format(x, _FLOAT_FMT)


def _scalar_pre(value: Any) -> bytes | None:
    """标量的独立 pre-image;容器/子类返回 None(走 normalize_value 通路)。

    dict/set 的子元素必须物化成独立字节串才能排序,此函数免去 bytearray
    往返;各分支输出与 _write 的对应标量分支逐字节一致(金样锁保证不漂移)。
    """
    t = value.__class__
    if t is str:
        b = value.encode("utf-8")
        return b"S%d:" % len(b) + b
    if t is int:
        s = str(value)
        return b"I%d:" % len(s) + s.encode()
    if t is float:
        if value.is_integer() and -_INT_EXACT_LIMIT < value < _INT_EXACT_LIMIT:
            s = str(int(value))
            return b"I%d:" % len(s) + s.encode()
        s = _normalize_float(value)
        return b"F%d:" % len(s) + s.encode()
    if t is bool:
        return b"B1" if value else b"B0"
    if value is None:
        return _T_NULL
    return None


def _write(buf: bytearray, value: Any) -> None:
    """把 value 的 pre-image 追加进 buf。热路径:按精确类型分派,频序排列。"""
    t = value.__class__
    if t is str:
        b = value.encode("utf-8")
        buf += b"S%d:" % len(b)
        buf += b
    elif t is int:
        s = str(value)  # 纯 ASCII,字节长即字符长
        buf += b"I%d:" % len(s)
        buf += s.encode()
    elif t is float:
        # 整数值浮点(2.0)与整数(2)同像:参数从 YAML/JSON 往返时类型会漂移
        if value.is_integer() and -_INT_EXACT_LIMIT < value < _INT_EXACT_LIMIT:
            s = str(int(value))
            buf += b"I%d:" % len(s)
        else:
            s = _normalize_float(value)
            buf += b"F%d:" % len(s)
        buf += s.encode()
    elif t is bool:
        buf += b"B1" if value else b"B0"
    elif t is dict:
        # 坑一:按「已规范化的 key」的字节排序,而非 key 原值;
        # value pre-image 参与决胜(NaN 键这类同像异值键仍有全序)
        pairs = []
        for k, v in value.items():
            k_pre = _scalar_pre(k)
            if k_pre is None:
                k_pre = normalize_value(k)
            v_pre = _scalar_pre(v)
            if v_pre is None:
                v_pre = normalize_value(v)
            pairs.append((k_pre, v_pre))
        pairs.sort()
        buf += _T_DICT
        for k_pre, v_pre in pairs:
            buf += k_pre
            buf += v_pre
        buf += _T_END
    elif t is list or t is tuple:
        buf += _T_LIST
        for item in value:
            _write(buf, item)
        buf += _T_END
    elif value is None:
        buf += _T_NULL
    elif t is bytes:
        buf += b"Y%d:" % len(value)
        buf += value
    elif t is set or t is frozenset:
        # 集合无序:按元素 pre-image 排序
        subs = []
        for v in value:
            pre = _scalar_pre(v)
            subs.append(normalize_value(v) if pre is None else pre)
        subs.sort()
        buf += _T_LIST
        for pre in subs:
            buf += pre
        buf += _T_END
    else:
        _write_fallback(buf, value)


def _write_fallback(buf: bytearray, value: Any) -> None:
    """慢路径:子类与鸭子类型,保持旧版 isinstance 链的判定顺序与编码。"""
    if isinstance(value, bool):  # 必须在 int 之前(bool 是 int 子类)
        buf += b"B1" if value else b"B0"
    elif isinstance(value, int):
        s = str(value)
        buf += b"I%d:" % len(s)
        buf += s.encode()
    elif isinstance(value, float):
        if value.is_integer() and abs(value) < _INT_EXACT_LIMIT:
            s = str(int(value))
            buf += b"I%d:" % len(s)
        else:
            s = _normalize_float(value)
            buf += b"F%d:" % len(s)
        buf += s.encode()
    elif isinstance(value, str):
        b = value.encode("utf-8")
        buf += b"S%d:" % len(b)
        buf += b
    elif isinstance(value, bytes):
        buf += b"Y%d:" % len(value)
        buf += value
    elif isinstance(value, (list, tuple)):
        buf += _T_LIST
        for item in value:
            _write(buf, item)
        buf += _T_END
    elif isinstance(value, (set, frozenset)):
        subs = [normalize_value(v) for v in value]
        subs.sort()
        buf += _T_LIST
        for pre in subs:
            buf += pre
        buf += _T_END
    elif isinstance(value, dict):
        pairs = [(normalize_value(k), normalize_value(v)) for k, v in value.items()]
        pairs.sort()
        buf += _T_DICT
        for k_pre, v_pre in pairs:
            buf += k_pre
            buf += v_pre
        buf += _T_END
    else:
        raise UnrepresentableError(f"unhashable value type for fingerprint: {type(value)!r}")


def normalize_value(value: Any) -> bytes:
    """把任意 JSON 风格值转成确定性字节 pre-image。"""
    buf = bytearray()
    _write(buf, value)
    return bytes(buf)


def hash_struct(value: Any) -> str:
    """规范化 sha256,返回 64 位十六进制。"""
    buf = bytearray()
    _write(buf, value)
    return hashlib.sha256(buf).hexdigest()
