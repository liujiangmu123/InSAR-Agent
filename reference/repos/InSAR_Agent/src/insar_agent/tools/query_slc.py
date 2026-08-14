"""SLC query tool - search ASF for Sentinel-1 SLC data with burst integrity checks"""

import os
import sys
import logging
from collections import defaultdict, Counter
from dataclasses import dataclass, field
from math import floor, hypot
from pathlib import Path
from typing import Optional

import asf_search as asf
import geopandas as gpd

logging.getLogger('asf_search').setLevel(logging.ERROR)

STATISTICS_AVAILABLE = True


@dataclass
class SlcItem:
    date: str
    name: str
    area: float = 0.0
    centroid_x: float = 0.0
    centroid_y: float = 0.0
    anomaly: bool = False
    area_bad: bool = False
    loc_bad: bool = False
    loc_dist: float = 0.0


@dataclass
class StackInfo:
    path: int
    frame: int
    count: int
    start_date: str
    end_date: str
    flight_direction: str = ''
    normal_count: int = 0
    anomaly_count: int = 0
    area_bad_count: int = 0
    loc_bad_count: int = 0
    anomaly_dates: list[str] = field(default_factory=list)
    area_bad_dates: list[str] = field(default_factory=list)
    loc_bad_dates: list[str] = field(default_factory=list)
    scenes: list[dict] = field(default_factory=list)


@dataclass
class SlcQueryResult:
    total_scenes: int
    groups: dict[tuple, list[dict]]
    stacks: list[StackInfo]
    coverage_image: Optional[str] = None
    slc_gdf: Optional[gpd.GeoDataFrame] = None
    warnings: list[str] = field(default_factory=list)


from ._aoi import make_aoi_wkt


def _resolve_aoi_wkt(vector_path: Optional[str] = None,
                     lon: Optional[float] = None, lat: Optional[float] = None,
                     half_span: float = 0.25) -> str:
    if vector_path and os.path.isfile(vector_path):
        aoi = gpd.read_file(vector_path)
        return aoi.geometry.union_all().wkt
    if lon is not None and lat is not None:
        return make_aoi_wkt(lon, lat, half_span)
    return 'POLYGON((-180 -90, 180 -90, 180 90, -180 90, -180 -90))'


def query_slc(
    vector: Optional[str] = None,
    lon: Optional[float] = None,
    lat: Optional[float] = None,
    start: str = '2017-01-01',
    end: str = '2023-12-31',
    flight_dir: Optional[str] = None,
    area_ratio_threshold: float = 0.95,
    location_deviation_km: float = 5.0,
    half_span: float = 0.25,
    save_coverage: bool = True,
    output_dir: Optional[str] = None,
    path: Optional[int] = None,
    frame: Optional[int] = None,
) -> SlcQueryResult:
    """Search ASF for Sentinel-1 SLC data and perform burst integrity checks.

    Returns:
        SlcQueryResult with stacks and metadata. Each stack has scene lists
        with anomaly flags for filtering before InSAR submission.
    """
    warnings: list[str] = []
    wkt = _resolve_aoi_wkt(vector, lon, lat, half_span)

    search_kwargs = {
        'platform': asf.PLATFORM.SENTINEL1,
        'start': start, 'end': end,
        'processingLevel': asf.PRODUCT_TYPE.SLC,
        'beamMode': asf.BEAMMODE.IW,
    }
    if path is not None:
        search_kwargs['relativeOrbit'] = path
    if frame is not None:
        search_kwargs['frame'] = frame
    # Only add AOI if no path/frame specified, or if explicit vector/lon/lat
    if not (path is not None and frame is not None) or vector or (lon is not None and lat is not None):
        search_kwargs['intersectsWith'] = wkt
    else:
        print(f'[query_slc] using relativeOrbit={path} frame={frame}, skipping AOI', flush=True)

    results = asf.geo_search(**search_kwargs)

    if flight_dir:
        results = [r for r in results if r.properties.get('flightDirection') == flight_dir]

    if not results:
        return SlcQueryResult(total_scenes=0, groups={}, stacks=[],
                              warnings=['No SLC data found for the given parameters'])

    gdf = gpd.GeoDataFrame(
        {'geometry': [__import__('shapely.geometry').geometry.shape(r.geometry) for r in results]},
        crs='EPSG:4326',
    )
    gdf['sceneName'] = [r.properties['sceneName'] for r in results]
    gdf['date'] = [r.properties['startTime'][:10] for r in results]
    gdf['pathNumber'] = [r.properties['pathNumber'] for r in results]
    gdf['frameNumber'] = [r.properties['frameNumber'] for r in results]
    gdf['flightDirection'] = [r.properties.get('flightDirection', '') for r in results]

    cen_lon = gdf.geometry.centroid.x.median()
    utm_zone = int(floor((cen_lon + 180) / 6)) + 1
    utm_epsg = 32601 + utm_zone if cen_lon >= 0 else 32701 + utm_zone
    gdf_area = gdf.to_crs(epsg=utm_epsg)

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for i, (_, row) in enumerate(gdf.iterrows()):
        geom = gdf_area.iloc[i]['geometry']
        groups[(row['pathNumber'], row['frameNumber'])].append({
            'date': row['date'],
            'area': geom.area,
            'centroid_x': geom.centroid.x,
            'centroid_y': geom.centroid.y,
            'name': row['sceneName'],
            'flight_direction': results[i].properties.get('flightDirection', ''),
        })

    # Burst integrity checks on main groups
    max_count = max(len(v) for v in groups.values())
    main_threshold = max(max_count * 0.3, 3)
    main_groups = {k: v for k, v in groups.items() if len(v) >= main_threshold}

    for (p, f), items in main_groups.items():
        area_rounded = Counter(round(it['area'] / 1e6, 0) for it in items)
        ref_area_rounded = area_rounded.most_common(1)[0][0]
        ref_area = ref_area_rounded * 1e6

        for it in items:
            ratio = it['area'] / ref_area
            it['_area_bad'] = ratio < area_ratio_threshold or ratio > 1 / area_ratio_threshold

        import statistics
        area_ok = [it for it in items if not it['_area_bad']]
        if area_ok:
            ref_cx = statistics.median(it['centroid_x'] for it in area_ok)
            ref_cy = statistics.median(it['centroid_y'] for it in area_ok)
        for it in items:
            if not it['_area_bad']:
                dx = it['centroid_x'] - ref_cx
                dy = it['centroid_y'] - ref_cy
                it['_loc_dist'] = hypot(dx, dy) / 1000
                it['_loc_bad'] = it['_loc_dist'] > location_deviation_km
            else:
                it['_loc_bad'] = False

        for it in items:
            it['_anomaly'] = it.get('_area_bad', False) or it.get('_loc_bad', False)

    stacks = []
    for (p, f), items in sorted(groups.items(), key=lambda x: -len(x[1])):
        dates = sorted(it['date'] for it in items)
        normal = [it for it in items if not it.get('_anomaly', False)]
        area_bad = [it for it in items if it.get('_area_bad')]
        loc_bad = [it for it in items if it.get('_loc_bad')]

        flight_val = items[0].get('flight_direction', '') if items else ''
        stacks.append(StackInfo(
            path=p, frame=f,
            count=len(items),
            start_date=dates[0], end_date=dates[-1],
            flight_direction=flight_val,
            normal_count=len(normal),
            anomaly_count=len(area_bad) + len(loc_bad),
            area_bad_count=len(area_bad),
            loc_bad_count=len(loc_bad),
            anomaly_dates=[it['date'] for it in area_bad + loc_bad],
            area_bad_dates=[it['date'] for it in area_bad],
            loc_bad_dates=[it['date'] for it in loc_bad],
            scenes=items,
        ))

    coverage_image = None
    if save_coverage:
        try:
            coverage_image = _generate_coverage_map(
                gdf, groups, output_dir or '.', vector, start, end, flight_dir,
            )
        except Exception as e:
            warnings.append(f'Failed to generate coverage map: {e}')

    return SlcQueryResult(
        total_scenes=len(gdf),
        groups=dict(groups),
        stacks=stacks,
        coverage_image=coverage_image,
        slc_gdf=gdf,
        warnings=warnings,
    )


def _generate_coverage_map(gdf, groups, out_dir, vector_path, start, end, flight_dir):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib import cm

    os.makedirs(out_dir, exist_ok=True)

    fig, ax = plt.subplots(1, 1, figsize=(16, 14), dpi=150)
    gdf_wgs = gdf.to_crs('EPSG:4326')

    try:
        import contextily as cx
        xmin, ymin, xmax, ymax = gdf_wgs.total_bounds
        ax.set_xlim(xmin - 0.3, xmax + 0.3)
        ax.set_ylim(ymin - 0.2, ymax + 0.2)
        cx.add_basemap(ax, crs='EPSG:4326', source=cx.providers.OpenStreetMap.Mapnik,
                       zoom='auto', attribution=False)
    except Exception:
        ax.grid(True, alpha=0.3)

    if vector_path and os.path.isfile(vector_path):
        try:
            aoi = gpd.read_file(vector_path).to_crs('EPSG:4326')
            aoi.boundary.plot(ax=ax, color='black', linewidth=1.5, linestyle='-')
            aoi.plot(ax=ax, facecolor='none', edgecolor='black', linewidth=1.5,
                     alpha=0.3, hatch='////')
        except Exception:
            pass

    key_list = sorted(groups.items(), key=lambda x: -len(x[1]))
    n_groups = len(key_list)
    colors = cm.tab20.colors if n_groups <= 20 else cm.tab20b.colors
    legend_handles = []

    for gi, ((p, f), items) in enumerate(key_list):
        color = colors[gi % len(colors)]
        names = {it['name'] for it in items}
        rows = gdf_wgs[gdf_wgs['sceneName'].isin(names)]

        normal_names = {it['name'] for it in items if not it.get('_anomaly')}
        anomaly_names = {it['name'] for it in items if it.get('_anomaly')}

        normal_rows = rows[rows['sceneName'].isin(normal_names)]
        anomaly_rows = rows[rows['sceneName'].isin(anomaly_names)]

        if len(normal_rows) > 0:
            normal_rows.boundary.plot(ax=ax, color=color, linewidth=0.8, alpha=0.8)
        if len(anomaly_rows) > 0:
            anomaly_rows.boundary.plot(ax=ax, color=color, linewidth=0.8,
                                        alpha=0.5, linestyle='--')

        if len(rows) > 0:
            centroid = rows.geometry.centroid.unary_union.centroid
            ax.annotate(f'P{p}F{f}\n{len(items)}\u666f',
                        xy=(centroid.x, centroid.y), fontsize=10,
                        ha='center', va='center', color='black',
                        bbox=dict(boxstyle='round,pad=0.3', facecolor=color,
                                  alpha=0.8, edgecolor='none'))

        legend_handles.append(mpatches.Patch(
            color=color, label=f'Path {p} Frame {f} ({len(items)}景)'))

    ax.legend(handles=legend_handles, fontsize=9, loc='lower left',
              ncol=max(1, n_groups // 8), framealpha=0.35, edgecolor='#cccccc')
    ax.set_xlabel('经度 (°)', fontsize=12)
    ax.set_ylabel('纬度 (°)', fontsize=12)
    ax.set_title(f'Sentinel-1 SLC 覆盖范围\n{start} ~ {end}  |  '
                 f'{flight_dir or "全部轨道"}  |  '
                 f'{len(gdf)} 景 / {len(groups)} 组', fontsize=14)

    plt.tight_layout()
    img_path = os.path.join(out_dir, 'slc_coverage.png')
    fig.savefig(img_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    return img_path


def _export_slc_geojson(gdf, groups, out_dir):
    """Export SLC footprints as GeoJSON with path/frame/anomaly properties for frontend map rendering."""
    import json as _json

    os.makedirs(out_dir, exist_ok=True)
    gdf_wgs = gdf.to_crs('EPSG:4326')

    # Assign color index per path+frame
    key_list = sorted(groups.items(), key=lambda x: -len(x[1]))
    color_idx: dict[tuple, int] = {}
    for gi, (k, _) in enumerate(key_list):
        color_idx[k] = gi

    features = []
    for i, (_, row) in enumerate(gdf_wgs.iterrows()):
        pn = row['pathNumber']
        fn = row['frameNumber']
        key = (pn, fn)
        items = groups.get(key, [])
        it = next((x for x in items if x['name'] == row['sceneName']), None)
        anomaly = it.get('_anomaly', False) if it else False

        props = {
            'sceneName': row['sceneName'],
            'date': row['date'],
            'path': int(pn),
            'frame': int(fn),
            'anomaly': anomaly,
            'colorIdx': color_idx.get(key, 0),
            'totalColorIdx': len(key_list),
        }
        try:
            geom = row['geometry'].__geo_interface__
        except AttributeError:
            try:
                from shapely.geometry import mapping as _mapping
                geom = _mapping(row['geometry'])
            except ImportError:
                geom = _json.loads(row['geometry'].ExportToJson()) if hasattr(row['geometry'], 'ExportToJson') else None
        if geom is None:
            continue
        features.append({
            'type': 'Feature',
            'properties': props,
            'geometry': geom,
        })

    geojson = {
        'type': 'FeatureCollection',
        'features': features,
        'metadata': {
            'total': len(features),
            'groups': len(key_list),
            'groupKeys': [{'path': k[0], 'frame': k[1], 'count': len(groups[k]), 'colorIdx': i}
                           for i, k in enumerate(key_list)],
        },
    }

    geo_path = os.path.join(out_dir, 'slc_footprints.geojson')
    with open(geo_path, 'w', encoding='utf-8') as f:
        _json.dump(geojson, f, ensure_ascii=False)
    return os.path.abspath(geo_path)
