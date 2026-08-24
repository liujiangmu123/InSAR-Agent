# -*- coding: utf-8 -*-
"""WGS84 ↔ UTM 坐标换算(纯 Python,零第三方依赖)。

HyP3 装载的 MintPy 产物是 UTM 投影(EPSG=326xx/327xx,X/Y 单位米),
X_FIRST/Y_FIRST 乃至 REF_LAT/REF_LON 属性里装的都是投影坐标(米)——
E2E 4 景实测:timeseries.h5 attrs EPSG=32611,REF_LAT=3914960(northing),
REF_LON=450000(easting)。端点若把这些米坐标当经纬度,经纬度查询必然
out_of_coverage,展示的"经纬度"也是假的。

venv 层不装 pyproj(引擎依赖分层纪律),这里按 Karney (2011)
"Transverse Mercator with an accuracy of a few nanometers" 的 Krüger
级数(6 阶)自实现正/反算。UTM 有效域内误差亚毫米级,远超 80 m 像元
定位需求;数值锚点与 pyproj 对照,钉死在 tests/test_geo.py。

只做 UTM(横轴墨卡托 + 标准 UTM 参数):其他投影(极地立体等)不假装
支持 —— epsg_is_utm 返回 False,调用方须诚实降级(让用户改用 row/col)。
"""

from __future__ import annotations

import math
import re

# WGS84 椭球
_A = 6378137.0                    # 长半轴(米)
_F = 1.0 / 298.257223563          # 扁率
_N = _F / (2.0 - _F)              # 第三扁率 n
_K0 = 0.9996                      # UTM 中央经线比例因子
_FE = 500_000.0                   # UTM 假东(米)
_FN_SOUTH = 10_000_000.0          # 南半球假北(米)

# 等距长半径 A(Karney 2011 eq.14)
_RA = _A / (1.0 + _N) * (1.0 + _N**2 / 4.0 + _N**4 / 64.0 + _N**6 / 256.0)

# Krüger 正算系数 α(Karney 2011 eq.35;6 阶)
_ALPHA = (
    _N / 2 - 2 * _N**2 / 3 + 5 * _N**3 / 16 + 41 * _N**4 / 180
    - 127 * _N**5 / 288 + 7891 * _N**6 / 37800,
    13 * _N**2 / 48 - 3 * _N**3 / 5 + 557 * _N**4 / 1440 + 281 * _N**5 / 630
    - 1983433 * _N**6 / 1935360,
    61 * _N**3 / 240 - 103 * _N**4 / 140 + 15061 * _N**5 / 26880
    + 167603 * _N**6 / 181440,
    49561 * _N**4 / 161280 - 179 * _N**5 / 168 + 6601661 * _N**6 / 7257600,
    34729 * _N**5 / 80640 - 3418889 * _N**6 / 1995840,
    212378941 * _N**6 / 319334400,
)

# Krüger 反算系数 β(Karney 2011 eq.36;6 阶)
_BETA = (
    _N / 2 - 2 * _N**2 / 3 + 37 * _N**3 / 96 - _N**4 / 360
    - 81 * _N**5 / 512 + 96199 * _N**6 / 604800,
    _N**2 / 48 + _N**3 / 15 - 437 * _N**4 / 1440 + 46 * _N**5 / 105
    - 1118711 * _N**6 / 3870720,
    17 * _N**3 / 480 - 37 * _N**4 / 840 - 209 * _N**5 / 4480
    + 5569 * _N**6 / 90720,
    4397 * _N**4 / 161280 - 11 * _N**5 / 504 - 830251 * _N**6 / 7257600,
    4583 * _N**5 / 161280 - 108847 * _N**6 / 3991680,
    20648693 * _N**6 / 638668800,
)

# 保形纬度 χ → 大地纬度 φ 级数(Karney 2011 eq.15-16 反向;6 阶)
_DELTA = (
    2 * _N - 2 * _N**2 / 3 - 2 * _N**3 + 116 * _N**4 / 45 + 26 * _N**5 / 45
    - 2854 * _N**6 / 675,
    7 * _N**2 / 3 - 8 * _N**3 / 5 - 227 * _N**4 / 45 + 2704 * _N**5 / 315
    + 2323 * _N**6 / 945,
    56 * _N**3 / 15 - 136 * _N**4 / 35 - 1262 * _N**5 / 105
    + 73814 * _N**6 / 2835,
    4279 * _N**4 / 630 - 332 * _N**5 / 35 - 399572 * _N**6 / 14175,
    4174 * _N**5 / 315 - 144838 * _N**6 / 6237,
    601676 * _N**6 / 22275,
)

_ZONE_RE = re.compile(r"^\s*(\d{1,2})\s*([NnSs])\s*$")


def epsg_is_utm(epsg: int) -> bool:
    """WGS84 UTM 的 EPSG 闭集:32601-32660(北)/ 32701-32760(南)。"""
    return 32601 <= epsg <= 32660 or 32701 <= epsg <= 32760


def utm_epsg_from_zone(zone: str) -> int | None:
    """MintPy UTM_ZONE 属性('11N'/'33S')→ EPSG;不合法 → None。"""
    m = _ZONE_RE.match(zone or "")
    if not m:
        return None
    num = int(m.group(1))
    if not 1 <= num <= 60:
        return None
    return (32600 if m.group(2).upper() == "N" else 32700) + num


def _zone_params(epsg: int) -> tuple[float, float]:
    """EPSG → (中央经线弧度, 假北米)。调用方保证 epsg_is_utm 已通过。"""
    zone = epsg % 100
    lon0 = math.radians(zone * 6 - 183)
    fn = _FN_SOUTH if epsg >= 32701 else 0.0
    return lon0, fn


def latlon_to_utm(epsg: int, lat: float, lon: float) -> tuple[float, float]:
    """WGS84 经纬度(度)→ UTM (easting, northing)(米)。

    不做分带裁剪:任意经度都按该带中央经线投影(跨带查询时结果仍是
    该带坐标系里的合法值,与 pyproj always_xy 行为一致)。
    """
    if not epsg_is_utm(epsg):
        raise ValueError(f"EPSG {epsg} 不是 WGS84 UTM 带")
    lon0, fn = _zone_params(epsg)
    phi = math.radians(lat)
    lam = math.radians(lon) - lon0
    lam = math.atan2(math.sin(lam), math.cos(lam))  # 归一到 (-π, π]

    s = math.sin(phi)
    c2 = 2.0 * math.sqrt(_N) / (1.0 + _N)
    t = math.sinh(math.atanh(s) - c2 * math.atanh(c2 * s))
    xi_p = math.atan2(t, math.cos(lam))
    eta_p = math.asinh(math.sin(lam) / math.hypot(t, math.cos(lam)))

    xi = xi_p
    eta = eta_p
    for j, a in enumerate(_ALPHA, start=1):
        xi += a * math.sin(2 * j * xi_p) * math.cosh(2 * j * eta_p)
        eta += a * math.cos(2 * j * xi_p) * math.sinh(2 * j * eta_p)
    easting = _FE + _K0 * _RA * eta
    northing = fn + _K0 * _RA * xi
    return easting, northing


def utm_to_latlon(epsg: int, easting: float, northing: float) -> tuple[float, float]:
    """UTM (easting, northing)(米)→ WGS84 (lat, lon)(度)。"""
    if not epsg_is_utm(epsg):
        raise ValueError(f"EPSG {epsg} 不是 WGS84 UTM 带")
    lon0, fn = _zone_params(epsg)
    xi = (northing - fn) / (_K0 * _RA)
    eta = (easting - _FE) / (_K0 * _RA)

    xi_p = xi
    eta_p = eta
    for j, b in enumerate(_BETA, start=1):
        xi_p -= b * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
        eta_p -= b * math.cos(2 * j * xi) * math.sinh(2 * j * eta)

    chi = math.asin(max(-1.0, min(1.0, math.sin(xi_p) / math.cosh(eta_p))))
    phi = chi
    for j, d in enumerate(_DELTA, start=1):
        phi += d * math.sin(2 * j * chi)
    lam = math.atan2(math.sinh(eta_p), math.cos(xi_p))
    lat = math.degrees(phi)
    lon = math.degrees(lam + lon0)
    if lon > 180.0:
        lon -= 360.0
    elif lon < -180.0:
        lon += 360.0
    return lat, lon
