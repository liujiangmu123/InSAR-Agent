"""ISCE2 基线 → GAMMA 风格 TCN .base(DESIGN.md §4.2 难点 2)。

ISCE2 产物通常给垂直基线 B⊥(bperp)与平行基线 B∥(bpar,沿视线)。
PyStamps / GAMMA ISP 的 ``.base`` 要卫星固连 TCN 分量:
  T 沿轨, C 交轨(右视 +C), N 天底(指向地球)。

缺 .base 时 PyStamps ``mtprep.py`` 会静默丢干涉图 —— 本模块拒绝在缺
基线输入时写出残缺产物。
"""

from __future__ import annotations

import math
import re
from pathlib import Path


def tcn_from_perp_par(*, bperp: float, bpar: float, look_rad: float,
                      along_track: float = 0.0) -> tuple[float, float, float]:
    """ISCE2 (B⊥, B∥) → GAMMA (T, C, N),单位米。

    TCN 定义在卫星处,故 θ 用 look 角(不是地面入射角)。视线在 CN 平面::

        û_los = sin(θ) Ĉ + cos(θ) N̂

    ISCE2::

        B∥ = B · û_los = C sinθ + N cosθ
        B⊥ = C cosθ − N sinθ     (CN 平面内右手垂直分量)

    反演(正交, det = −1)::

        C = B∥ sinθ + B⊥ cosθ
        N = B∥ cosθ − B⊥ sinθ
        T = along_track   # ISCE2 平均基线通常不含沿轨;缺省 0
    """
    s = math.sin(look_rad)
    c = math.cos(look_rad)
    c_comp = bpar * s + bperp * c
    n_comp = bpar * c - bperp * s
    return (float(along_track), float(c_comp), float(n_comp))


def perp_par_from_tcn(*, c: float, n: float, look_rad: float) -> tuple[float, float]:
    """``tcn_from_perp_par`` 的逆;返回 (bperp, bpar)。T 不进入 2D 分解。"""
    s = math.sin(look_rad)
    co = math.cos(look_rad)
    bpar = c * s + n * co
    bperp = c * co - n * s
    return (float(bperp), float(bpar))


def format_base(*, t: float, c: float, n: float) -> str:
    """GAMMA ISP baseline 文件;测试用 ``initial_baseline(TCN): T C N`` 解析。"""
    return (
        "Gamma ISP Baseline File (ISCE2→PyStamps TCN)\n"
        f"initial_baseline(TCN):        {t:14.5f} {c:14.5f} {n:14.5f}   m\n"
        "initial_baseline_rate:              0.00000        0.00000        0.00000   m/s\n"
        f"precision_baseline(TCN):      {t:14.5f} {c:14.5f} {n:14.5f}   m\n"
        "precision_baseline_rate:            0.00000        0.00000        0.00000   m/s\n"
    )


def write_base(path: Path, *, t: float, c: float, n: float) -> None:
    path.write_text(format_base(t=t, c=c, n=n), encoding="utf-8")


_TCN_LINE = re.compile(
    r"initial_baseline\(TCN\):\s+"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s+"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s+"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)",
)


def parse_base_tcn(text: str) -> tuple[float, float, float]:
    m = _TCN_LINE.search(text)
    if not m:
        raise ValueError("无法从 .base 解析 initial_baseline(TCN)")
    return float(m.group(1)), float(m.group(2)), float(m.group(3))
