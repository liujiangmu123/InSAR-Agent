"""InSAR results catalog - spatial searchable archive of processed results

Stores metadata for completed InSAR processing runs. When a user queries
a new area, the agent first searches this catalog for pre-existing results
before triggering new processing.
"""

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_CATALOG_DIR = Path(__file__).resolve().parent.parent / 'data' / 'catalog'
_INDEX_FILE = _CATALOG_DIR / 'index.json'


@dataclass
class CatalogResult:
    query_bbox: Optional[list[float]]
    query_date_start: str
    query_date_end: str
    matches: list[dict] = field(default_factory=list)
    total: int = 0
    footprints: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _load_index() -> list[dict]:
    if _INDEX_FILE.is_file():
        try:
            with open(_INDEX_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_index(entries: list[dict]):
    _CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(_INDEX_FILE, 'w', encoding='utf-8') as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def _bbox_overlaps(a: list[float], b: list[float]) -> bool:
    """Check if two bounding boxes [minx, miny, maxx, maxy] overlap."""
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _date_overlaps(start1: str, end1: str, start2: str, end2: str) -> bool:
    """Check if two date ranges overlap. Dates are YYYY-MM-DD strings."""
    return not (end1 < start2 or end2 < start1)


def register_result(
    name: str,
    center_lon: float,
    center_lat: float,
    bbox: tuple[float, float, float, float],
    date_start: str,
    date_end: str,
    result_files: dict,
    path: int = 0,
    frame: int = 0,
    preview: str = '',
    parameters: Optional[dict] = None,
) -> dict:
    """Register a processed result in the catalog.

    Args:
        name: Display name (e.g. '北京2023年升轨InSAR')
        center_lon/lat: Center coordinates
        bbox: (west, south, east, north) in degrees
        date_start/end: Date range strings (YYYY-MM-DD)
        result_files: Dict mapping label to file path (e.g. {'velocity':'...velocity.h5'})
        path/frame: Sentinel-1 path and frame numbers
        preview: Path to preview image
        parameters: Optional dict of processing parameters

    Returns:
        The registered entry dict
    """
    entries = _load_index()
    eid = f'result_{len(entries) + 1:04d}'

    entry = {
        'id': eid,
        'name': name,
        'center': [center_lon, center_lat],
        'bbox': list(bbox),
        'date_start': date_start,
        'date_end': date_end,
        'path': path,
        'frame': frame,
        'files': result_files,
        'preview': preview,
        'parameters': parameters or {},
        'created': time.strftime('%Y-%m-%d %H:%M'),
    }
    entries.append(entry)
    _save_index(entries)
    return entry


def search_catalog(
    lon: Optional[float] = None,
    lat: Optional[float] = None,
    bbox: Optional[list[float]] = None,
    vector: Optional[str] = None,
    date_start: str = '',
    date_end: str = '',
    name_query: str = '',
    top_k: int = 5,
) -> CatalogResult:
    """Search the results catalog for matching entries.

    Filter by spatial intersection (vector GeoJSON or bbox),
    date range overlap, and name keyword.

    Args:
        lon/lat: Center point (fallback if no bbox/vector)
        bbox: [west, south, east, north] (fallback if no vector)
        vector: Path to boundary GeoJSON for precise polygon intersection
        date_start/end: Date range filter (YYYY-MM-DD)
        name_query: Keyword search in entry name
        top_k: Max results to return

    Returns:
        CatalogResult with matching entries
    """
    warnings = []
    entries = _load_index()

    if not entries:
        return CatalogResult(
            query_bbox=bbox, query_date_start=date_start, query_date_end=date_end,
            warnings=['Catalog is empty. Register results with register_result().'],
        )

    query_bbox = None
    _query_poly = None
    if vector and os.path.isfile(vector):
        try:
            import geopandas as gpd
            aoi = gpd.read_file(vector)
            _query_poly = aoi.geometry.union_all()
            qb = _query_poly.bounds
            query_bbox = [qb[0], qb[1], qb[2], qb[3]]
        except Exception:
            pass
    if query_bbox is None:
        query_bbox = bbox
    if query_bbox is None and lon is not None and lat is not None:
        query_bbox = [lon - 0.5, lat - 0.5, lon + 0.5, lat + 0.5]

    # Pre-load footprints for ALL entries so polygon intersection uses real S1 geometry
    footprint_updated = False
    seen_fetch: set[tuple] = set()
    for entry in entries:
        pf = (entry.get('path'), entry.get('frame'))
        if pf in seen_fetch or not pf[0] or not pf[1]:
            continue
        seen_fetch.add(pf)
        if not entry.get('footprint'):
            fp = _fetch_frame_footprint(pf[0], pf[1])
            if fp:
                entry['footprint'] = fp
                footprint_updated = True
    if footprint_updated:
        _save_index(entries)

    # Shapely imports for polygon intersection
    _use_poly = False
    try:
        from shapely import from_wkt, Polygon
        _swkt = from_wkt
        _use_poly = True
    except ImportError:
        try:
            from shapely.wkt import loads as _swkt
            from shapely.geometry import Polygon
            _use_poly = True
        except ImportError:
            pass

    matched = []
    for entry in entries:
        if _use_poly and _query_poly is not None:
            fp = entry.get('footprint', {})
            entry_geom = None
            if fp and fp.get('wkt'):
                try:
                    entry_geom = _swkt(fp['wkt'])
                except Exception:
                    pass
            if entry_geom is None:
                eb = entry.get('bbox')
                if eb and len(eb) == 4:
                    try:
                        entry_geom = Polygon([
                            (eb[0], eb[1]), (eb[2], eb[1]),
                            (eb[2], eb[3]), (eb[0], eb[3]),
                        ])
                    except Exception:
                        pass
            if entry_geom is None or not _query_poly.intersects(entry_geom):
                continue
        elif query_bbox:
            entry_bbox = entry.get('bbox')
            if entry_bbox and not _bbox_overlaps(query_bbox, entry_bbox):
                continue

        if date_start and date_end:
            entry_start = entry.get('date_start', '')
            entry_end = entry.get('date_end', '')
            if entry_start and entry_end:
                if not _date_overlaps(date_start, date_end, entry_start, entry_end):
                    continue

        entry['_score'] = (
            (1 if query_bbox else 0) * 10
            + (1 if date_start and date_end and _date_overlaps(date_start, date_end, entry.get('date_start', ''), entry.get('date_end', '')) else 0) * 5
        )
        matched.append(entry)

    matched.sort(key=lambda x: -x.get('_score', 0))
    for m in matched:
        m.pop('_score', None)

    # Build footprints with real S1 frame GeoJSON (dedup by path/frame)
    footprints: list[dict] = []
    seen_pf: set[tuple] = set()

    try:
        from shapely import from_wkt as _parse_wkt, to_geojson
        _to_gj = lambda g: __import__('json').loads(to_geojson(g))
    except ImportError:
        from shapely.wkt import loads as _parse_wkt
        from shapely.geometry import mapping as _to_gj

    for m in matched:
        pf = (m.get('path'), m.get('frame'))
        if pf in seen_pf or not pf[0] or not pf[1]:
            continue
        seen_pf.add(pf)
        fpd = m.get('footprint', {})
        if fpd and fpd.get('wkt'):
            try:
                gj = _to_gj(_parse_wkt(fpd['wkt']))
                footprints.append({
                    'path': pf[0], 'frame': pf[1],
                    'geojson': gj, 'name': m.get('name', ''),
                    'date_start': m.get('date_start', ''), 'date_end': m.get('date_end', ''),
                })
            except Exception:
                pass
    # Build matches list from footprints (ensures 1:1 correspondence)
    matched_pf = {(fp['path'], fp['frame']) for fp in footprints}
    cleaned = []
    for m in matched:
        pf = (m.get('path'), m.get('frame'))
        if pf not in matched_pf:
            continue
        matched_pf.discard(pf)
        e = {k: v for k, v in m.items()}
        files = e.get('files', {})
        flat_files = {k: str(v) for k, v in files.items()}
        e['files'] = flat_files
        cleaned.append(e)

    return CatalogResult(
        query_bbox=query_bbox,
        query_date_start=date_start,
        query_date_end=date_end,
        matches=cleaned,
        total=len(footprints),
        footprints=footprints,
        warnings=warnings,
    )


def _to_float(v) -> float | None:
    """Safely convert to float, returning None on failure."""
    if v is None or (isinstance(v, str) and v.strip() == '') or (isinstance(v, bytes) and v.strip() == b''):
        return None
    try:
        if isinstance(v, bytes):
            v = v.decode('utf-8')
        return float(v)
    except (ValueError, TypeError, AttributeError):
        return None


def _to_int(v) -> int | None:
    if v is None or (isinstance(v, str) and v.strip() == '') or (isinstance(v, bytes) and v.strip() == b''):
        return None
    try:
        if isinstance(v, bytes):
            v = v.decode('utf-8')
        return int(v)
    except (ValueError, TypeError, AttributeError):
        return None


def _extract_meta_hdf5(h5_path: str) -> dict:
    """Extract spatial extent and dates from MintPy HDF5."""
    try:
        import h5py
        with h5py.File(h5_path, 'r') as f:
            attrs = dict(f.attrs)
    except Exception:
        return {}

    info = {}
    for prefix in ('', '_REF', 'LON_REF', 'LAT_REF'):
        lons = [attrs.get(f'LON{prefix}1'), attrs.get(f'LON{prefix}2'),
                attrs.get(f'LON{prefix}3'), attrs.get(f'LON{prefix}4')]
        lats = [attrs.get(f'LAT{prefix}1'), attrs.get(f'LAT{prefix}2'),
                attrs.get(f'LAT{prefix}3'), attrs.get(f'LAT{prefix}4')]
        if all(v is not None and (not isinstance(v, str) or v.strip()) for v in lons + lats):
            try:
                lons_f = [_to_float(v) for v in lons]; lats_f = [_to_float(v) for v in lats]
                if all(x is not None for x in lons_f + lats_f):
                    info['bbox'] = [min(lons_f), min(lats_f), max(lons_f), max(lats_f)]
                    info['center_lon'] = sum(lons_f) / 4
                    info['center_lat'] = sum(lats_f) / 4
                    break
            except Exception:
                continue

    if 'bbox' not in info:
        xf, yf = attrs.get('X_FIRST'), attrs.get('Y_FIRST')
        xs, ys = attrs.get('X_STEP'), attrs.get('Y_STEP')
        w, h = attrs.get('WIDTH'), attrs.get('FILE_LENGTH')
        if all(v is not None and (not isinstance(v, str) or v.strip()) for v in [xf, yf, xs, ys, w, h]):
            xf, yf, xs, ys = _to_float(xf), _to_float(yf), _to_float(xs), _to_float(ys)
            w, h = _to_int(w), _to_int(h)
            if all(v is not None for v in [xf, yf, xs, ys, w, h]):
                xmin, xmax = xf, xf + w * xs
                ymin, ymax = (yf, yf + h * ys) if ys > 0 else (yf + h * ys, yf)
                info['bbox'] = [xmin, ymin, xmax, ymax]
                info['center_lon'] = (xmin + xmax) / 2
                info['center_lat'] = (ymin + ymax) / 2

    ds, de = attrs.get('START_DATE'), attrs.get('END_DATE')
    for key, val in [('date_start', ds), ('date_end', de)]:
        if val is not None and (not isinstance(val, (str, bytes)) or (isinstance(val, str) and val.strip()) or (isinstance(val, bytes) and val.strip())):
            try:
                s = val.decode('utf-8') if isinstance(val, bytes) else str(val)
                info[key] = s.strip("b'").split('T')[0].strip()
            except Exception:
                pass
    if 'date_start' not in info:
        dates = attrs.get('date')
        if dates is not None and len(dates) > 0:
            try:
                d0, d1 = dates[0], dates[-1]
                info['date_start'] = (d0.decode('utf-8') if isinstance(d0, bytes) else str(d0)).strip("b'")
                info['date_end'] = (d1.decode('utf-8') if isinstance(d1, bytes) else str(d1)).strip("b'")
            except Exception:
                pass
    return info


def _extract_meta_tiff(tif_path: str) -> dict:
    """Extract bbox in WGS84 lon/lat from GeoTIFF (handles projected CRS)."""
    try:
        from osgeo import gdal, osr
        ds = gdal.Open(tif_path)
        if ds is None: return {}
        gt = ds.GetGeoTransform()
        w, h = ds.RasterXSize, ds.RasterYSize
        xmin = gt[0]
        ymax = gt[3]
        xmax = xmin + w * gt[1] + h * gt[2]
        ymin = ymax + w * gt[4] + h * gt[5]
        if ymin > ymax:
            ymin, ymax = ymax, ymin

        corners = [(xmin, ymin), (xmin, ymax), (xmax, ymax), (xmax, ymin)]
        proj_wkt = ds.GetProjection()
        if proj_wkt:
            src_srs = osr.SpatialReference()
            src_srs.ImportFromWkt(proj_wkt)
            if src_srs.IsProjected():
                tgt_srs = osr.SpatialReference()
                tgt_srs.ImportFromEPSG(4326)
                tgt_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
                transform = osr.CoordinateTransformation(src_srs, tgt_srs)
                corners = [transform.TransformPoint(x, y)[:2] for x, y in corners]

        lons = [p[0] for p in corners]
        lats = [p[1] for p in corners]
        return {
            'bbox': [min(lons), min(lats), max(lons), max(lats)],
            'center_lon': sum(lons) / 4,
            'center_lat': sum(lats) / 4,
        }
    except Exception:
        return {}


def _fetch_frame_footprint(path_num: int, frame_num: int) -> dict | None:
    """Fetch a representative Sentinel-1 frame footprint geometry from ASF."""
    try:
        import asf_search as asf
        results = asf.geo_search(
            platform=asf.PLATFORM.SENTINEL1,
            processingLevel=asf.PRODUCT_TYPE.SLC,
            beamMode=asf.BEAMMODE.IW,
            maxResults=3,
            relativeOrbit=path_num,
            frame=frame_num,
            start='2022-01-01', end='2022-01-31',
        )
        if len(results) == 0:
            return None
        from shapely.geometry import shape
        geom = shape(results[0].geometry)
        if geom.is_empty:
            return None
        from shapely import wkt
        return {'wkt': geom.wkt, 'type': geom.geom_type}
    except Exception:
        return None


_STD_VEL_RE = re.compile(r'^vel_(\d+)_(\d+)_(\d{6})_(\d{6})\.tif$')
_STD_VELE_RE = re.compile(r'^vel_E_(\d+)_(\d+)_(\d{6})_(\d{6})\.tif$')


def _ym_to_date(ym: str) -> str:
    """Convert YYYYMM to YYYY-MM display format."""
    return f'{ym[:4]}-{ym[4:]}'


def _make_entry_id(path_num: int, frame_num: int, date_start: str = '', date_end: str = '') -> str:
    tag = f'{path_num}_{frame_num}'
    if date_start:
        tag += f'_{date_start}'
    if date_end:
        tag += f'_{date_end}'
    return f'preview_{tag}'


def auto_import(base_dir: str, dry_run: bool = False, fast: bool = True) -> list[dict]:
    """Scan base_dir for result files and auto-register in catalog.

    Supports two detection modes:
      A) Legacy: {path}_{frame}/ directories with raw filenames
      B) Standard: task dirs (Path*_Frame*) with mintpy/ containing
         standard-named files like vel_157_127_201809_201812.tif

    Args:
        base_dir: Directory to scan
        dry_run: If True, only scan and return metadata without registering
        fast: If True (default), skip metadata extraction for speed

    Returns:
        List of detected (and optionally registered) entry dicts
    """
    bp = Path(base_dir)
    if not bp.is_dir():
        return [{'error': f'Directory not found: {base_dir}'}]

    imported = []
    seen = set()

    # ── Mode A: legacy {path}_{frame} directories ──
    for sub in sorted(bp.iterdir()):
        if not sub.is_dir():
            continue
        m = re.match(r'^(\d{1,3})_(\d{1,3})$', sub.name)
        if not m:
            continue
        path_num, frame_num = int(m.group(1)), int(m.group(2))
        if (path_num, frame_num) in seen:
            continue

        vel_tif = sub / f'vel_{path_num}_{frame_num}.tif'
        coh_tif = sub / 'temporalCoherence.tif'
        water_tif = sub / 'waterMask.tif'
        ts_h5 = sub / 'timeseries_ramp_demErr.h5'

        if not vel_tif.is_file():
            continue

        seen.add((path_num, frame_num))

        meta = {}
        if not fast:
            if ts_h5.is_file():
                try:
                    meta.update(_extract_meta_hdf5(str(ts_h5)))
                except Exception:
                    pass
            try:
                meta.update(_extract_meta_tiff(str(vel_tif)))
            except Exception:
                pass

        files = {}
        if vel_tif.is_file():
            files['velocity'] = str(vel_tif)
        if coh_tif.is_file():
            files['temporal_coherence'] = str(coh_tif)
        if water_tif.is_file():
            files['water_mask'] = str(water_tif)
        if ts_h5.is_file():
            files['timeseries'] = str(ts_h5)

        for fp in sub.glob('*.png'):
            files[str(fp.name)] = str(fp)
            break

        lon, lat = meta.get('center_lon', 0), meta.get('center_lat', 0)
        bbox = tuple(meta.get('bbox', (lon - 0.5, lat - 0.5, lon + 0.5, lat + 0.5)))

        file_count = len(files)
        preview = files.get(list(files.keys())[0], '') if files else ''

        if dry_run:
            entry = {
                'id': _make_entry_id(path_num, frame_num),
                'name': f'Path{path_num}_Frame{frame_num}',
                'center': [lon, lat],
                'bbox': list(bbox),
                'date_start': meta.get('date_start', '(未检测到)'),
                'date_end': meta.get('date_end', '(未检测到)'),
                'path': path_num,
                'frame': frame_num,
                'files': {k: os.path.basename(v) for k, v in files.items()},
                'file_count': file_count,
                'has_velocity': True,
                'has_coherence': coh_tif.is_file(),
                'has_watermask': water_tif.is_file(),
                'has_timeseries': ts_h5.is_file(),
            }
            imported.append(entry)
            continue

        entry = register_result(
            name=f'Path{path_num}_Frame{frame_num}',
            center_lon=lon, center_lat=lat,
            bbox=bbox,
            date_start=meta.get('date_start', ''),
            date_end=meta.get('date_end', ''),
            result_files=files,
            path=path_num, frame=frame_num,
            preview=preview,
            parameters={'source_dir': str(sub), 'auto_detected': True},
        )
        fp = _fetch_frame_footprint(path_num, frame_num)
        if fp:
            entry['footprint'] = fp
            entries_all = _load_index()
            for e in entries_all:
                if e.get('id') == entry['id']:
                    e['footprint'] = fp
                    break
            _save_index(entries_all)
        imported.append(entry)

    # ── Mode B: task dirs (Path*_Frame*) with mintpy/ standard files ──
    for sub in sorted(bp.iterdir()):
        if not sub.is_dir():
            continue
        mintpy_dir = sub / 'mintpy'
        if not mintpy_dir.is_dir():
            continue

        vel_files = sorted(mintpy_dir.glob('vel_*.tif'))
        for vf in vel_files:
            m = _STD_VEL_RE.match(vf.name) or _STD_VELE_RE.match(vf.name)
            if not m:
                continue
            path_num = int(m.group(1))
            frame_num = int(m.group(2))
            date_start = m.group(3)
            date_end = m.group(4)

            if (path_num, frame_num) in seen:
                continue
            seen.add((path_num, frame_num))

            tag = f'{path_num}_{frame_num}_{date_start}_{date_end}'
            work_dir = vf.parent  # = mintpy_dir

            ts_h5 = work_dir / f'cum_rd_{tag}.h5'
            if not ts_h5.is_file():
                ts_h5 = work_dir / f'cum_rdE_{tag}.h5'
            coh_tif = work_dir / f'tc_{tag}.tif'
            water_tif = work_dir / f'water_{tag}.tif'

            files = {'velocity': str(vf)}
            if coh_tif.is_file():
                files['temporal_coherence'] = str(coh_tif)
            if water_tif.is_file():
                files['water_mask'] = str(water_tif)
            if ts_h5.is_file():
                files['timeseries'] = str(ts_h5)

            for fp in work_dir.glob('*.png'):
                files[str(fp.name)] = str(fp)
                break

            lon, lat = 0.0, 0.0
            bbox = (-0.5, -0.5, 0.5, 0.5)
            if not fast:
                try:
                    meta = _extract_meta_tiff(str(vf))
                except Exception:
                    meta = {}
                lon = meta.get('center_lon', 0.0)
                lat = meta.get('center_lat', 0.0)
                bbox_val = meta.get('bbox')
                if bbox_val:
                    bbox = tuple(bbox_val)

            file_count = len(files)
            preview = files.get(list(files.keys())[0], '') if files else ''

            if dry_run:
                entry = {
                    'id': _make_entry_id(path_num, frame_num, date_start, date_end),
                    'name': f'Path{path_num}_Frame{frame_num}',
                    'center': [lon, lat],
                    'bbox': list(bbox),
                    'date_start': _ym_to_date(date_start),
                    'date_end': _ym_to_date(date_end),
                    'path': path_num,
                    'frame': frame_num,
                    'files': {k: os.path.basename(v) for k, v in files.items()},
                    'file_count': file_count,
                    'has_velocity': True,
                    'has_coherence': coh_tif.is_file(),
                    'has_watermask': water_tif.is_file(),
                    'has_timeseries': ts_h5.is_file(),
                }
                imported.append(entry)
                continue

            entry = register_result(
                name=f'Path{path_num}_Frame{frame_num}',
                center_lon=lon, center_lat=lat,
                bbox=bbox,
                date_start=_ym_to_date(date_start),
                date_end=_ym_to_date(date_end),
                result_files=files,
                path=path_num, frame=frame_num,
                preview=preview,
                parameters={'source_dir': str(work_dir), 'auto_detected': True},
            )
            fp = _fetch_frame_footprint(path_num, frame_num)
            if fp:
                entry['footprint'] = fp
                entries_all = _load_index()
                for e in entries_all:
                    if e.get('id') == entry['id']:
                        e['footprint'] = fp
                        break
                _save_index(entries_all)
            imported.append(entry)

    # ── Mode C: {path}_{frame} directories with standard-named files ──
    for sub in sorted(bp.iterdir()):
        if not sub.is_dir():
            continue
        dm = re.match(r'^(\d{1,3})_(\d{1,3})$', sub.name)
        if not dm:
            continue
        path_num = int(dm.group(1))
        frame_num = int(dm.group(2))
        if (path_num, frame_num) in seen:
            continue

        vel_files = sorted(sub.glob('vel_*.tif'))
        for vf in vel_files:
            m = _STD_VEL_RE.match(vf.name) or _STD_VELE_RE.match(vf.name)
            if not m:
                continue
            date_start = m.group(3)
            date_end = m.group(4)
            if (path_num, frame_num) in seen:
                continue
            seen.add((path_num, frame_num))

            tag = f'{path_num}_{frame_num}_{date_start}_{date_end}'

            ts_h5 = sub / f'cum_rd_{tag}.h5'
            if not ts_h5.is_file():
                ts_h5 = sub / f'cum_rdE_{tag}.h5'
            coh_tif = sub / f'tc_{tag}.tif'
            water_tif = sub / f'water_{tag}.tif'

            files = {'velocity': str(vf)}
            if coh_tif.is_file():
                files['temporal_coherence'] = str(coh_tif)
            if water_tif.is_file():
                files['water_mask'] = str(water_tif)
            if ts_h5.is_file():
                files['timeseries'] = str(ts_h5)

            for fp in sub.glob('*.png'):
                files[str(fp.name)] = str(fp)
                break

            lon, lat = 0.0, 0.0
            bbox = (-0.5, -0.5, 0.5, 0.5)
            if not fast:
                try:
                    meta = _extract_meta_tiff(str(vf))
                except Exception:
                    meta = {}
                lon = meta.get('center_lon', 0.0)
                lat = meta.get('center_lat', 0.0)
                bbox_val = meta.get('bbox')
                if bbox_val:
                    bbox = tuple(bbox_val)

            file_count = len(files)
            preview = files.get(list(files.keys())[0], '') if files else ''

            if dry_run:
                entry = {
                    'id': _make_entry_id(path_num, frame_num, date_start, date_end),
                    'name': f'Path{path_num}_Frame{frame_num}',
                    'center': [lon, lat],
                    'bbox': list(bbox),
                    'date_start': _ym_to_date(date_start),
                    'date_end': _ym_to_date(date_end),
                    'path': path_num,
                    'frame': frame_num,
                    'files': {k: os.path.basename(v) for k, v in files.items()},
                    'file_count': file_count,
                    'has_velocity': True,
                    'has_coherence': coh_tif.is_file(),
                    'has_watermask': water_tif.is_file(),
                    'has_timeseries': ts_h5.is_file(),
                }
                imported.append(entry)
                continue

            entry = register_result(
                name=f'Path{path_num}_Frame{frame_num}',
                center_lon=lon, center_lat=lat,
                bbox=bbox,
                date_start=_ym_to_date(date_start),
                date_end=_ym_to_date(date_end),
                result_files=files,
                path=path_num, frame=frame_num,
                preview=preview,
                parameters={'source_dir': str(sub), 'auto_detected': True},
            )
            fp = _fetch_frame_footprint(path_num, frame_num)
            if fp:
                entry['footprint'] = fp
                entries_all = _load_index()
                for e in entries_all:
                    if e.get('id') == entry['id']:
                        e['footprint'] = fp
                        break
                _save_index(entries_all)
            imported.append(entry)

    return imported
