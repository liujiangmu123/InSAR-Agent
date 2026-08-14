"""Geocoding tool - place name to coordinates with area of interest sizing"""

import json
import os
import urllib.request
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GeocodeResult:
    name: str
    display_name: str = ''
    lon: float = 0.0
    lat: float = 0.0
    half_span: float = 0.25
    bbox: Optional[list[float]] = None
    source: str = ''
    warnings: list[str] = field(default_factory=list)


_CITY = 0.25
_CITY_SMALL = 0.15
_PROVINCE = 2.5
_PROVINCE_LARGE = 5.0

_CITY_DB: dict[str, tuple[float, float, str, float]] = {
    # ── 直辖市 ──
    '北京': (116.407, 39.904, '北京市', _CITY),
    '北京市': (116.407, 39.904, '北京市', _CITY),
    'beijing': (116.407, 39.904, 'Beijing', _CITY),
    '上海': (121.473, 31.230, '上海市', _CITY),
    '上海市': (121.473, 31.230, '上海市', _CITY),
    'shanghai': (121.473, 31.230, 'Shanghai', _CITY),
    '天津': (117.190, 39.125, '天津市', _CITY),
    '天津市': (117.190, 39.125, '天津市', _CITY),
    'tianjin': (117.190, 39.125, 'Tianjin', _CITY),
    '重庆': (106.551, 29.563, '重庆市', _CITY),
    '重庆市': (106.551, 29.563, '重庆市', _CITY),
    'chongqing': (106.551, 29.563, 'Chongqing', _CITY),
    # ── 省会 ──
    '石家庄': (114.515, 38.042, '河北省石家庄市', _CITY),
    '太原': (112.549, 37.870, '山西省太原市', _CITY),
    '呼和浩特': (111.749, 40.842, '内蒙古呼和浩特市', _CITY),
    '沈阳': (123.432, 41.805, '辽宁省沈阳市', _CITY),
    '长春': (125.324, 43.887, '吉林省长春市', _CITY),
    '哈尔滨': (126.643, 45.804, '黑龙江省哈尔滨市', _CITY),
    '南京': (118.797, 32.060, '江苏省南京市', _CITY),
    '杭州': (120.155, 30.274, '浙江省杭州市', _CITY),
    '合肥': (117.227, 31.821, '安徽省合肥市', _CITY),
    '福州': (119.296, 26.074, '福建省福州市', _CITY),
    '南昌': (115.858, 28.683, '江西省南昌市', _CITY),
    '济南': (117.001, 36.651, '山东省济南市', _CITY),
    '郑州': (113.625, 34.746, '河南省郑州市', _CITY),
    '武汉': (114.305, 30.593, '湖北省武汉市', _CITY),
    '长沙': (112.939, 28.228, '湖南省长沙市', _CITY),
    '广州': (113.264, 23.129, '广东省广州市', _CITY),
    '南宁': (108.366, 22.817, '广西南宁市', _CITY),
    '海口': (110.199, 20.044, '海南省海口市', _CITY),
    '成都': (104.066, 30.573, '四川省成都市', _CITY),
    '贵阳': (106.630, 26.648, '贵州省贵阳市', _CITY),
    '昆明': (102.832, 24.880, '云南省昆明市', _CITY),
    '拉萨': (91.172, 29.650, '西藏拉萨市', _CITY),
    '西安': (108.940, 34.261, '陕西省西安市', _CITY),
    '兰州': (103.834, 36.061, '甘肃省兰州市', _CITY),
    '西宁': (101.778, 36.617, '青海省西宁市', _CITY),
    '银川': (106.231, 38.487, '宁夏银川市', _CITY),
    '乌鲁木齐': (87.617, 43.793, '新疆乌鲁木齐市', _CITY),
    '台北': (121.565, 25.033, '台湾台北市', _CITY),
    '香港': (114.169, 22.319, '香港', _CITY),
    '澳门': (113.549, 22.199, '澳门', _CITY_SMALL),
    # ── 英文省会名 ──
    'shijiazhuang': (114.515, 38.042, 'Shijiazhuang', _CITY),
    'taiyuan': (112.549, 37.870, 'Taiyuan', _CITY),
    'hohhot': (111.749, 40.842, 'Hohhot', _CITY),
    'shenyang': (123.432, 41.805, 'Shenyang', _CITY),
    'changchun': (125.324, 43.887, 'Changchun', _CITY),
    'harbin': (126.643, 45.804, 'Harbin', _CITY),
    'nanjing': (118.797, 32.060, 'Nanjing', _CITY),
    'hangzhou': (120.155, 30.274, 'Hangzhou', _CITY),
    'hefei': (117.227, 31.821, 'Hefei', _CITY),
    'fuzhou': (119.296, 26.074, 'Fuzhou', _CITY),
    'nanchang': (115.858, 28.683, 'Nanchang', _CITY),
    'jinan': (117.001, 36.651, 'Jinan', _CITY),
    'zhengzhou': (113.625, 34.746, 'Zhengzhou', _CITY),
    'wuhan': (114.305, 30.593, 'Wuhan', _CITY),
    'changsha': (112.939, 28.228, 'Changsha', _CITY),
    'guangzhou': (113.264, 23.129, 'Guangzhou', _CITY),
    'nanning': (108.366, 22.817, 'Nanning', _CITY),
    'haikou': (110.199, 20.044, 'Haikou', _CITY),
    'chengdu': (104.066, 30.573, 'Chengdu', _CITY),
    'guiyang': (106.630, 26.648, 'Guiyang', _CITY),
    'kunming': (102.832, 24.880, 'Kunming', _CITY),
    'lhasa': (91.172, 29.650, 'Lhasa', _CITY),
    'xian': (108.940, 34.261, "Xi'an", _CITY),
    'lanzhou': (103.834, 36.061, 'Lanzhou', _CITY),
    'xining': (101.778, 36.617, 'Xining', _CITY),
    'yinchuan': (106.231, 38.487, 'Yinchuan', _CITY),
    'urumqi': (87.617, 43.793, 'Urumqi', _CITY),
    'taipei': (121.565, 25.033, 'Taipei', _CITY),
    'hongkong': (114.169, 22.319, 'Hong Kong', _CITY),
    'macau': (113.549, 22.199, 'Macau', _CITY_SMALL),
    # ── 地级市 ──
    '青岛': (120.383, 36.067, '山东省青岛市', _CITY),
    '大连': (121.615, 38.914, '辽宁省大连市', _CITY),
    '厦门': (118.089, 24.480, '福建省厦门市', _CITY),
    '深圳': (114.058, 22.543, '广东省深圳市', _CITY),
    '苏州': (120.585, 31.299, '江苏省苏州市', _CITY),
    '宁波': (121.544, 29.869, '浙江省宁波市', _CITY),
    '桂林': (110.291, 25.274, '广西桂林市', _CITY),
    '烟台': (121.448, 37.464, '山东省烟台市', _CITY),
    '威海': (122.120, 37.513, '山东省威海市', _CITY),
    '珠海': (113.576, 22.271, '广东省珠海市', _CITY),
    '洛阳': (112.454, 34.620, '河南省洛阳市', _CITY),
    '唐山': (118.181, 39.630, '河北省唐山市', _CITY),
    '徐州': (117.284, 34.206, '江苏省徐州市', _CITY),
    '温州': (120.699, 28.002, '浙江省温州市', _CITY),
    '宜昌': (111.286, 30.691, '湖北省宜昌市', _CITY),
    '襄阳': (112.123, 32.010, '湖北省襄阳市', _CITY),
    '九江': (115.999, 29.705, '江西省九江市', _CITY),
    '大同': (113.300, 40.077, '山西省大同市', _CITY),
    '包头': (109.841, 40.657, '内蒙古包头市', _CITY),
    '延吉': (129.509, 42.891, '吉林省延吉市', _CITY),
    '大庆': (125.021, 46.597, '黑龙江省大庆市', _CITY),
    '喀什': (75.990, 39.470, '新疆喀什市', _CITY),
    '伊犁': (81.324, 43.917, '新疆伊犁', _CITY),
    # ── 小城市/镇 ──
    '三亚': (109.512, 18.252, '海南省三亚市', _CITY_SMALL),
    '大理': (100.230, 25.607, '云南省大理市', _CITY_SMALL),
    '丽江': (100.230, 26.857, '云南省丽江市', _CITY_SMALL),
    '林芝': (94.362, 29.649, '西藏林芝市', _CITY_SMALL),
    '日喀则': (88.885, 29.267, '西藏日喀则市', _CITY_SMALL),
    '阿里': (80.106, 32.501, '西藏阿里地区', _CITY_SMALL),
    # ── 省份/自治区（大尺度） ──
    '新疆': (84.902, 42.061, '新疆维吾尔自治区', _PROVINCE_LARGE),
    'xinjiang': (84.902, 42.061, 'Xinjiang', _PROVINCE_LARGE),
    '西藏': (88.770, 31.693, '西藏自治区', _PROVINCE_LARGE),
    'tibet': (88.770, 31.693, 'Tibet', _PROVINCE_LARGE),
    '内蒙古': (111.670, 41.818, '内蒙古自治区', _PROVINCE_LARGE),
    '青海': (96.058, 35.745, '青海省', _PROVINCE_LARGE),
    # ── 省份（标准） ──
    '四川': (102.919, 30.190, '四川省', _PROVINCE),
    '云南': (101.865, 25.181, '云南省', _PROVINCE),
    '山东': (117.347, 35.893, '山东省', _PROVINCE),
    '河北': (115.400, 37.969, '河北省', _PROVINCE),
    '河南': (113.467, 33.868, '河南省', _PROVINCE),
    '湖北': (112.239, 30.686, '湖北省', _PROVINCE),
    '湖南': (111.639, 27.622, '湖南省', _PROVINCE),
    '广东': (113.424, 23.325, '广东省', _PROVINCE),
    '广西': (108.793, 23.830, '广西壮族自治区', _PROVINCE),
    '贵州': (106.875, 26.918, '贵州省', _PROVINCE),
    '陕西': (109.609, 35.640, '陕西省', _PROVINCE),
    '甘肃': (103.826, 36.348, '甘肃省', _PROVINCE),
    '宁夏': (105.996, 37.309, '宁夏回族自治区', _PROVINCE),
    '黑龙江': (128.033, 47.121, '黑龙江省', _PROVINCE),
    '吉林': (125.763, 43.896, '吉林省', _PROVINCE),
    '辽宁': (122.816, 41.676, '辽宁省', _PROVINCE),
    '江苏': (119.485, 32.971, '江苏省', _PROVINCE),
    '浙江': (120.048, 29.813, '浙江省', _PROVINCE),
    '安徽': (117.383, 32.061, '安徽省', _PROVINCE),
    '福建': (118.211, 26.084, '福建省', _PROVINCE),
    '江西': (116.015, 27.290, '江西省', _PROVINCE),
    '海南': (109.849, 19.205, '海南省', _PROVINCE),
    '台湾': (121.020, 23.698, '台湾省', _PROVINCE),
    # ── 海外城市 ──
    '洛杉矶': (-118.244, 34.052, 'Los Angeles, USA', _CITY),
    'los angeles': (-118.244, 34.052, 'Los Angeles, USA', _CITY),
    '旧金山': (-122.419, 37.775, 'San Francisco, USA', _CITY),
    'san francisco': (-122.419, 37.775, 'San Francisco, USA', _CITY),
    '纽约': (-74.006, 40.714, 'New York, USA', _CITY),
    'new york': (-74.006, 40.714, 'New York, USA', _CITY),
    '东京': (139.692, 35.689, 'Tokyo, Japan', _CITY),
    'tokyo': (139.692, 35.689, 'Tokyo, Japan', _CITY),
    '伦敦': (-0.128, 51.508, 'London, UK', _CITY),
    'london': (-0.128, 51.508, 'London, UK', _CITY),
    '巴黎': (2.349, 48.853, 'Paris, France', _CITY),
    'paris': (2.349, 48.853, 'Paris, France', _CITY),
    '悉尼': (151.209, -33.868, 'Sydney, Australia', _CITY),
    'sydney': (151.209, -33.868, 'Sydney, Australia', _CITY),
    '雅加达': (106.846, -6.209, 'Jakarta, Indonesia', _CITY),
    'jakarta': (106.846, -6.209, 'Jakarta, Indonesia', _CITY),
    '曼谷': (100.502, 13.754, 'Bangkok, Thailand', _CITY),
    'bangkok': (100.502, 13.754, 'Bangkok, Thailand', _CITY),
    '新加坡': (103.820, 1.352, 'Singapore', _CITY),
    'singapore': (103.820, 1.352, 'Singapore', _CITY),
    '伊斯坦布尔': (28.979, 41.015, 'Istanbul, Turkey', _CITY),
    'istanbul': (28.979, 41.015, 'Istanbul, Turkey', _CITY),
    '墨西哥城': (-99.133, 19.433, 'Mexico City, Mexico', _CITY),
    'mexico city': (-99.133, 19.433, 'Mexico City, Mexico', _CITY),
    '圣地亚哥': (-70.648, -33.457, 'Santiago, Chile', _CITY),
    'santiago': (-70.648, -33.457, 'Santiago, Chile', _CITY),
    '罗马': (12.482, 41.893, 'Rome, Italy', _CITY),
    'rome': (12.482, 41.893, 'Rome, Italy', _CITY),
    '马德里': (-3.702, 40.417, 'Madrid, Spain', _CITY),
    'madrid': (-3.702, 40.417, 'Madrid, Spain', _CITY),
    '开罗': (31.249, 30.062, 'Cairo, Egypt', _CITY),
    'cairo': (31.249, 30.062, 'Cairo, Egypt', _CITY),
    '内罗毕': (36.817, -1.283, 'Nairobi, Kenya', _CITY),
    'nairobi': (36.817, -1.283, 'Nairobi, Kenya', _CITY),
    '德黑兰': (51.421, 35.694, 'Tehran, Iran', _CITY),
    'tehran': (51.421, 35.694, 'Tehran, Iran', _CITY),
}


def _resolve_by_adcode(name: str) -> Optional[GeocodeResult]:
    """Resolve coordinates via DataV GeoAtlas boundary centroid using adcode index."""
    try:
        try:
            from ._adcode_index import lookup_adcode
        except ImportError:
            from insar_agent.tools._adcode_index import lookup_adcode
        result = lookup_adcode(name)
        if not result:
            return None
        adcode, level = result
        padded = adcode.ljust(6, '0')
        url = f'https://geo.datav.aliyun.com/areas_v3/bound/{padded}.json'
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read().decode('utf-8'))
        features = data.get('features', [])
        if not features:
            return None
        geom = features[0].get('geometry')
        if not geom or geom.get('type') != 'MultiPolygon':
            return None
        coords = geom['coordinates'][0][0]
        if not coords:
            return None
        lons = [p[0] for p in coords]
        lats = [p[1] for p in coords]
        lon = sum(lons) / len(lons)
        lat = sum(lats) / len(lats)
        span_lon = (max(lons) - min(lons)) / 2
        span_lat = (max(lats) - min(lats)) / 2
        half_span = max(span_lon, span_lat, 0.15)
        display = data['features'][0].get('properties', {}).get('name', name)
        return GeocodeResult(
            name=name, display_name=display,
            lon=round(lon, 6), lat=round(lat, 6),
            half_span=round(half_span, 4), source='adcode',
        )
    except Exception:
        return None


def _lookup_local(name: str) -> Optional[GeocodeResult]:
    key = name.strip().lower()
    if key in _CITY_DB:
        lon, lat, display, half_span = _CITY_DB[key]
        return GeocodeResult(
            name=name, display_name=display,
            lon=lon, lat=lat, half_span=half_span, source='local',
        )
    return None


def _geocode_nominatim(name: str) -> Optional[GeocodeResult]:
    try:
        from geopy.geocoders import Nominatim
        geolocator = Nominatim(user_agent='insar_agent')
        location = geolocator.geocode(name, timeout=10)
        if location is None:
            location = geolocator.geocode(name, country_codes='cn', timeout=10)
        if location is None:
            return None

        bbox = None
        half_span = 0.25
        if hasattr(location, 'raw') and 'boundingbox' in location.raw:
            bbox_raw = location.raw['boundingbox']
            bbox = [float(v) for v in bbox_raw]
            half_span = max(
                abs(bbox[3] - bbox[2]) / 2,
                abs(bbox[1] - bbox[0]) / 2,
                0.15,
            )

        return GeocodeResult(
            name=name,
            display_name=location.address,
            lon=location.longitude,
            lat=location.latitude,
            bbox=bbox,
            half_span=half_span,
            source='nominatim',
        )
    except ImportError:
        return None
    except Exception:
        return None


def resolve_location(name: str) -> GeocodeResult:
    """Convert a human-readable place name to geographic coordinates with AOI sizing.

    Lookup order: adcode index (fast, covers all Chinese divisions) ->
    local dictionary (hardcoded cache) -> geopy/Nominatim (global fallback).

    Args:
        name: Place name in Chinese or English (e.g. '北京', 'Beijing', '成都', '济宁市', '曲阜')

    Returns:
        GeocodeResult with lon, lat, half_span (degrees), and source info.
    """
    result = _lookup_local(name)
    if result:
        return result

    result = _resolve_by_adcode(name)
    if result:
        return result

    result = _geocode_nominatim(name)
    if result:
        return result

    return GeocodeResult(
        name=name,
        warnings=[f"Unable to resolve location: '{name}'. Try a nearby major city name."],
    )
