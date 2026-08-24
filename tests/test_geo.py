# -*- coding: utf-8 -*-
"""api/geo.py:WGS84 ↔ UTM 纯 Python 换算的数值锚点。

期望值全部由 pyproj 3.7.2(引擎环境 E:\\miniforge3\\envs\\insar,
Transformer.from_crs(4326, <epsg>, always_xy=True))一次性生成后钉死 ——
venv 不装 pyproj,本套件不触网不依赖引擎,断言的是与权威库的一致性:
正算容差 1 mm,反算容差 1e-8 度(≈1 mm),UTM 域内 Krüger 6 阶级数的
实测误差比容差还小三个量级(fwd ≈4e-5 m)。

锚点选取:Ridgecrest 主战场(EPSG:32611,E2E 真实产物网格)、南半球
(开普敦 32733)、高纬(32601)、以及 E2E 实测 h5 的网格角点/参考点。
"""

from __future__ import annotations

import pytest

from insar_agent.api import geo

# (epsg, lat, lon, easting, northing) —— pyproj 3.7.2 生成
_FORWARD_ANCHORS = [
    (32611, 35.7700, -117.6000, 445766.5238, 3958604.5720),
    (32611, 36.2000, -118.1000, 401108.6933, 4006692.4427),
    (32611, 35.0000, -117.0000, 500000.0000, 3873043.0645),   # 中央经线
    (32611, 34.5000, -116.5000, 545901.8333, 3817710.6925),
    (32733, -33.9249, 18.4241, 816557.7956, 6240887.9956),    # 南半球假北
    (32601, 64.5000, -177.0000, 500000.0000, 7152732.4645),   # 高纬
]

# (epsg, easting, northing, lat, lon) —— E2E 实测 h5 网格角点与参考点
_INVERSE_ANCHORS = [
    (32611, 389960.0, 4000040.0, 36.1388335283, -118.2230541743),  # X_FIRST/Y_FIRST
    (32611, 509960.0, 3910040.0, 35.3335588348, -116.8904047148),  # 对角
    (32611, 450000.0, 3914960.0, 35.3767178377, -117.5504672280),  # REF_LON/REF_LAT
]


@pytest.mark.parametrize("epsg,lat,lon,easting,northing", _FORWARD_ANCHORS)
def test_forward_matches_pyproj(epsg, lat, lon, easting, northing):
    x, y = geo.latlon_to_utm(epsg, lat, lon)
    assert x == pytest.approx(easting, abs=1e-3)
    assert y == pytest.approx(northing, abs=1e-3)


@pytest.mark.parametrize("epsg,easting,northing,lat,lon", _INVERSE_ANCHORS)
def test_inverse_matches_pyproj(epsg, easting, northing, lat, lon):
    got_lat, got_lon = geo.utm_to_latlon(epsg, easting, northing)
    assert got_lat == pytest.approx(lat, abs=1e-8)
    assert got_lon == pytest.approx(lon, abs=1e-8)


@pytest.mark.parametrize("epsg,lat,lon,easting,northing", _FORWARD_ANCHORS)
def test_roundtrip_closes(epsg, lat, lon, easting, northing):
    x, y = geo.latlon_to_utm(epsg, lat, lon)
    lat2, lon2 = geo.utm_to_latlon(epsg, x, y)
    assert lat2 == pytest.approx(lat, abs=1e-9)
    assert lon2 == pytest.approx(lon, abs=1e-9)


def test_zone_string_to_epsg():
    assert geo.utm_epsg_from_zone("11N") == 32611
    assert geo.utm_epsg_from_zone("33S") == 32733
    assert geo.utm_epsg_from_zone(" 7n ") == 32607
    assert geo.utm_epsg_from_zone("0N") is None      # 带号 1-60 闭集
    assert geo.utm_epsg_from_zone("61N") is None
    assert geo.utm_epsg_from_zone("") is None
    assert geo.utm_epsg_from_zone("abc") is None


def test_epsg_is_utm_closed_set():
    assert geo.epsg_is_utm(32611)
    assert geo.epsg_is_utm(32701)
    assert not geo.epsg_is_utm(4326)     # 地理坐标
    assert not geo.epsg_is_utm(3413)     # 极地立体:诚实拒绝,不假装会算
    assert not geo.epsg_is_utm(32600)
    assert not geo.epsg_is_utm(32661)


def test_non_utm_epsg_raises():
    with pytest.raises(ValueError):
        geo.latlon_to_utm(3413, 70.0, -45.0)
    with pytest.raises(ValueError):
        geo.utm_to_latlon(4326, 500000.0, 0.0)
