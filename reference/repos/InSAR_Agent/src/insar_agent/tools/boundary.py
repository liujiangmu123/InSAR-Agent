"""Admin boundary tool - fetch GeoJSON boundaries from DataV GeoAtlas API

Downloads precise administrative polygon boundaries for Chinese provinces,
cities, and districts. The GeoJSON file can be passed as a vector to
query_slc for exact spatial intersection (more accurate than bbox).
"""

import os
import json
from dataclasses import dataclass, field
from typing import Optional

import requests


@dataclass
class BoundaryResult:
    name: str
    adcode: str
    level: str = ''
    geojson_path: str = ''
    bounds: Optional[list[float]] = None
    warnings: list[str] = field(default_factory=list)


_NAME_TO_ADCODE: dict[str, tuple[str, str]] = {
    # ── 直辖市 (province level) ──
    '北京': ('110000', 'province'), '北京市': ('110000', 'province'),
    'beijing': ('110000', 'province'),
    '上海': ('310000', 'province'), '上海市': ('310000', 'province'),
    'shanghai': ('310000', 'province'),
    '天津': ('120000', 'province'), '天津市': ('120000', 'province'),
    'tianjin': ('120000', 'province'),
    '重庆': ('500000', 'province'), '重庆市': ('500000', 'province'),
    'chongqing': ('500000', 'province'),
    # ── 直辖市 (city level, used for InSAR AOI) ──
    '北京市区': ('110100', 'city'), '上海市区': ('310100', 'city'),
    '天津市区': ('120100', 'city'), '重庆市区': ('500100', 'city'),
    # ── 省会城市 ──
    '石家庄': ('130100', 'city'), '石家庄市': ('130100', 'city'),
    '太原': ('140100', 'city'), '太原市': ('140100', 'city'),
    '呼和浩特': ('150100', 'city'), '呼和浩特市': ('150100', 'city'),
    '沈阳': ('210100', 'city'), '沈阳市': ('210100', 'city'),
    '长春': ('220100', 'city'), '长春市': ('220100', 'city'),
    '哈尔滨': ('230100', 'city'), '哈尔滨市': ('230100', 'city'),
    '南京': ('320100', 'city'), '南京市': ('320100', 'city'),
    '杭州': ('330100', 'city'), '杭州市': ('330100', 'city'),
    '合肥': ('340100', 'city'), '合肥市': ('340100', 'city'),
    '福州': ('350100', 'city'), '福州市': ('350100', 'city'),
    '南昌': ('360100', 'city'), '南昌市': ('360100', 'city'),
    '济南': ('370100', 'city'), '济南市': ('370100', 'city'),
    '郑州': ('410100', 'city'), '郑州市': ('410100', 'city'),
    '武汉': ('420100', 'city'), '武汉市': ('420100', 'city'),
    '长沙': ('430100', 'city'), '长沙市': ('430100', 'city'),
    '广州': ('440100', 'city'), '广州市': ('440100', 'city'),
    '南宁': ('450100', 'city'), '南宁市': ('450100', 'city'),
    '海口': ('460100', 'city'), '海口市': ('460100', 'city'),
    '成都': ('510100', 'city'), '成都市': ('510100', 'city'),
    '贵阳': ('520100', 'city'), '贵阳市': ('520100', 'city'),
    '昆明': ('530100', 'city'), '昆明市': ('530100', 'city'),
    '拉萨': ('540100', 'city'), '拉萨市': ('540100', 'city'),
    '西安': ('610100', 'city'), '西安市': ('610100', 'city'),
    '兰州': ('620100', 'city'), '兰州市': ('620100', 'city'),
    '西宁': ('630100', 'city'), '西宁市': ('630100', 'city'),
    '银川': ('640100', 'city'), '银川市': ('640100', 'city'),
    '乌鲁木齐': ('650100', 'city'), '乌鲁木齐市': ('650100', 'city'),
    '台北': ('710100', 'city'), '台北市': ('710100', 'city'),
    '香港': ('810000', 'province'), 'hongkong': ('810000', 'province'),
    '澳门': ('820000', 'province'), 'macau': ('820000', 'province'),
    # ── 省份 ──
    '河北': ('130000', 'province'), '河北省': ('130000', 'province'),
    '山西': ('140000', 'province'), '山西省': ('140000', 'province'),
    '内蒙古': ('150000', 'province'),
    '辽宁': ('210000', 'province'), '辽宁省': ('210000', 'province'),
    '吉林': ('220000', 'province'), '吉林省': ('220000', 'province'),
    '黑龙江': ('230000', 'province'),
    '江苏': ('320000', 'province'), '江苏省': ('320000', 'province'),
    '浙江': ('330000', 'province'), '浙江省': ('330000', 'province'),
    '安徽': ('340000', 'province'), '安徽省': ('340000', 'province'),
    '福建': ('350000', 'province'), '福建省': ('350000', 'province'),
    '江西': ('360000', 'province'), '江西省': ('360000', 'province'),
    '山东': ('370000', 'province'), '山东省': ('370000', 'province'),
    '河南': ('410000', 'province'), '河南省': ('410000', 'province'),
    '湖北': ('420000', 'province'), '湖北省': ('420000', 'province'),
    '湖南': ('430000', 'province'), '湖南省': ('430000', 'province'),
    '广东': ('440000', 'province'), '广东省': ('440000', 'province'),
    '广西': ('450000', 'province'),
    '海南': ('460000', 'province'), '海南省': ('460000', 'province'),
    '四川': ('510000', 'province'), '四川省': ('510000', 'province'),
    '贵州': ('520000', 'province'), '贵州省': ('520000', 'province'),
    '云南': ('530000', 'province'), '云南省': ('530000', 'province'),
    '西藏': ('540000', 'province'),
    '陕西': ('610000', 'province'), '陕西省': ('610000', 'province'),
    '甘肃': ('620000', 'province'), '甘肃省': ('620000', 'province'),
    '青海': ('630000', 'province'), '青海省': ('630000', 'province'),
    '宁夏': ('640000', 'province'),
    '新疆': ('650000', 'province'),
    '台湾': ('710000', 'province'), '台湾省': ('710000', 'province'),
    # ── 重要地级市 ──
    '青岛': ('370200', 'city'), '青岛市': ('370200', 'city'),
    '大连': ('210200', 'city'), '大连市': ('210200', 'city'),
    '厦门': ('350200', 'city'), '厦门市': ('350200', 'city'),
    '深圳': ('440300', 'city'), '深圳市': ('440300', 'city'),
    '苏州': ('320500', 'city'), '苏州市': ('320500', 'city'),
    '宁波': ('330200', 'city'), '宁波市': ('330200', 'city'),
    '桂林': ('450300', 'city'), '桂林市': ('450300', 'city'),
    '烟台': ('370600', 'city'), '烟台市': ('370600', 'city'),
    '威海': ('371000', 'city'), '威海市': ('371000', 'city'),
    '珠海': ('440400', 'city'), '珠海市': ('440400', 'city'),
    '洛阳': ('410300', 'city'), '洛阳市': ('410300', 'city'),
    '唐山': ('130200', 'city'), '唐山市': ('130200', 'city'),
    '徐州': ('320300', 'city'), '徐州市': ('320300', 'city'),
    '温州': ('330300', 'city'), '温州市': ('330300', 'city'),
    '宜昌': ('420500', 'city'), '宜昌市': ('420500', 'city'),
    '襄阳': ('420600', 'city'), '襄阳市': ('420600', 'city'),
    '九江': ('360400', 'city'), '九江市': ('360400', 'city'),
    '大同': ('140200', 'city'), '大同市': ('140200', 'city'),
    '包头': ('150200', 'city'), '包头市': ('150200', 'city'),
    '延吉': ('222400', 'city'), '延吉市': ('222400', 'city'),
    '大庆': ('230600', 'city'), '大庆市': ('230600', 'city'),
    '喀什': ('653100', 'city'), '喀什市': ('653100', 'city'),
    '伊犁': ('654000', 'city'),
    '三亚': ('460200', 'city'), '三亚市': ('460200', 'city'),
    '大理': ('532900', 'city'),
    '丽江': ('530700', 'city'), '丽江市': ('530700', 'city'),
    '林芝': ('540400', 'city'),
    '日喀则': ('540200', 'city'),
    '阿里': ('542500', 'city'),
}

DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'boundaries')


def _resolve_adcode_dynamic(name: str) -> Optional[tuple[str, str]]:
    """Try to resolve adcode via the auto-built index when name is not in the hardcoded dict."""
    try:
        try:
            from ._adcode_index import lookup_adcode
        except ImportError:
            from insar_agent.tools._adcode_index import lookup_adcode
        return lookup_adcode(name)
    except Exception:
        return None


def fetch_boundary(name: str, output_dir: Optional[str] = None) -> BoundaryResult:
    """Download administrative boundary GeoJSON from DataV GeoAtlas.

    Accepts Chinese administrative division names (province, city, district).
    Supports all Chinese divisions via auto-built adcode index.
    Fetches from https://geo.datav.aliyun.com/areas_v3/bound/.

    Args:
        name: Place name (e.g. '北京市', '成都', '广州市', '济宁市', '曲阜市')
        output_dir: Directory to save GeoJSON file

    Returns:
        BoundaryResult with geojson_path for use as vector input
    """
    key = name.strip().lower()
    adcode = level = None

    if key in _NAME_TO_ADCODE:
        adcode, level = _NAME_TO_ADCODE[key]
    else:
        dynamic = _resolve_adcode_dynamic(name)
        if dynamic:
            adcode, level = dynamic
        else:
            return BoundaryResult(
                name=name,
                adcode='',
                warnings=[f"Unknown boundary: '{name}'. Try a province, city, or county-level name."],
            )

    # Province/city level uses _full.json suffix; district level (6 digits) doesn't
    if len(adcode) == 6:
        url = f'https://geo.datav.aliyun.com/areas_v3/bound/{adcode}.json'
    else:
        padded = adcode.ljust(6, '0')
        url = f'https://geo.datav.aliyun.com/areas_v3/bound/{padded}_full.json'

    save_dir = output_dir or DIR
    os.makedirs(save_dir, exist_ok=True)
    safe_name = name.replace('/', '_').replace('\\', '_')
    geojson_path = os.path.join(save_dir, f'{safe_name}_boundary.geojson')

    if os.path.exists(geojson_path):
        return BoundaryResult(name=name, adcode=adcode, level=level, geojson_path=geojson_path)

    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        if not data.get('features'):
            return BoundaryResult(name=name, adcode=adcode, level=level,
                                  warnings=[f'No geometry data returned for {adcode}'])

        with open(geojson_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)

        bounds = None
        props = data['features'][0].get('properties', {})
        actual_name = props.get('name', name)

        return BoundaryResult(
            name=actual_name, adcode=adcode, level=level,
            geojson_path=geojson_path, bounds=bounds,
        )
    except requests.RequestException as e:
        return BoundaryResult(name=name, adcode=adcode, level=level,
                              warnings=[f'Failed to fetch boundary: {e}'])
    except Exception as e:
        return BoundaryResult(name=name, adcode=adcode, level=level,
                              warnings=[f'Error processing boundary: {e}'])
