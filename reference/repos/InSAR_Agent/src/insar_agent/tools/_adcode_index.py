"""Adcode index — build a cached name→adcode mapping from DataV GeoAtlas.

Downloads the full Chinese administrative division tree (provinces + all cities
+ all districts) on first use, caches to disk, and provides O(1) name lookups
for any Chinese place name at any administrative level.

Cache lifetime: 7 days, auto-refreshed on expiry.
Index version bumped to 2 to invalidate old province+city-only caches.
"""

import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

INDEX_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'adcode_index')
INDEX_FILE = os.path.join(INDEX_DIR, 'name_to_adcode.json')
ROOT_URL = 'https://geo.datav.aliyun.com/areas_v3/bound/100000_full.json'
CACHE_TTL = 365 * 24 * 3600  # 1 year, only refresh on version bump
INDEX_VERSION = 2  # bump when format changes


def _normalize(name: str) -> str:
    n = name.strip()
    for suffix in ('市', '省', '自治区', '特别行政区', '地区', '州', '盟', '县', '区'):
        if n.endswith(suffix) and len(n) > len(suffix):
            n = n[:-len(suffix)]
    return n


def _register(index: dict, name: str, adcode: str, level: str):
    if name:
        index[name.strip()] = (adcode, level)
        norm = _normalize(name)
        if norm != name:
            index[norm] = (adcode, level)


def _fetch_json(url: str, timeout: int = 15) -> Optional[dict]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception:
        return None


def _download_index() -> dict[str, tuple[str, str]]:
    os.makedirs(INDEX_DIR, exist_ok=True)
    index: dict[str, tuple[str, str]] = {}

    # Step 1: provinces + cities
    root = _fetch_json(ROOT_URL, timeout=30)
    if not root:
        return {}

    province_codes: list[str] = []
    city_codes: list[str] = []
    for f in root.get('features', []):
        p = f['properties']
        adcode = str(p['adcode'])
        _register(index, p.get('name', ''), adcode, p.get('level', 'province'))
        if p.get('level', '') in ('province', 'municipality'):
            province_codes.append(adcode)

    for prov_code in province_codes:
        prov_data = _fetch_json(f'https://geo.datav.aliyun.com/areas_v3/bound/{prov_code}_full.json', timeout=15)
        if not prov_data:
            continue
        for f in prov_data.get('features', []):
            p = f['properties']
            adcode = str(p['adcode'])
            _register(index, p.get('name', ''), adcode, 'city')
            city_codes.append(adcode)

    # Step 2: districts (parallel download for speed)
    def _fetch_city_districts(city_code: str) -> dict[str, tuple[str, str]]:
        local: dict[str, tuple[str, str]] = {}
        data = _fetch_json(f'https://geo.datav.aliyun.com/areas_v3/bound/{city_code}_full.json', timeout=15)
        if data:
            for f in data.get('features', []):
                p = f['properties']
                _register(local, p.get('name', ''), str(p['adcode']), 'district')
        return local

    if city_codes:
        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = {pool.submit(_fetch_city_districts, c): c for c in city_codes}
            for future in as_completed(futures):
                try:
                    index.update(future.result())
                except Exception:
                    pass

    # Save cache
    cache_data = {
        'version': INDEX_VERSION,
        'built_at': time.time(),
        'count': len(index),
        'entries': {k: list(v) for k, v in index.items()},
    }
    with open(INDEX_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache_data, f, ensure_ascii=False)

    return index


def _load_cached_index() -> Optional[dict[str, tuple[str, str]]]:
    if not os.path.isfile(INDEX_FILE):
        return None
    try:
        with open(INDEX_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if data.get('version', 0) < INDEX_VERSION:
            return None
        if time.time() - data.get('built_at', 0) > CACHE_TTL:
            return None
        return {k: tuple(v) for k, v in data.get('entries', {}).items()}
    except Exception:
        return None


def get_index() -> dict[str, tuple[str, str]]:
    cached = _load_cached_index()
    if cached:
        return cached
    return _download_index()


def lookup_adcode(name: str) -> Optional[tuple[str, str]]:
    index = get_index()
    key = name.strip()
    if key in index:
        return index[key]
    norm = _normalize(key)
    if norm in index:
        return index[norm]
    return None
