"""Analysis report orchestration and packaging.

Runs the full deformation analysis pipeline: funnel detection, cumulative
displacement, strain rate, acceleration, zone classification, chart generation,
and zips all outputs for download.

Extracts region metadata (Path, Frame, date range) from the input files
and embeds it into chart titles and the report.
"""

import json
import os
import re
import zipfile
from pathlib import Path


def _extract_meta(vel_path, ts_path):
    """Extract Path, Frame, and date range from filenames and HDF5 metadata.

    Returns dict with path, frame, date_start, date_end, center_lon, center_lat.
    """
    meta = {'path': None, 'frame': None, 'date_start': '', 'date_end': '',
            'center_lon': None, 'center_lat': None}

    # Try to parse Path/Frame from parent directory name
    # velocity.tif is in <task_dir>/mintpy/velocity.tif, so parent.parent is task_dir
    vel_file = Path(vel_path)
    task_dir_name = vel_file.parent.parent.name
    match = re.search(r'Path(\d+).*Frame(\d+)', task_dir_name, re.IGNORECASE)
    if match:
        meta['path'] = int(match.group(1))
        meta['frame'] = int(match.group(2))

    # Try HDF5 attributes for dates
    try:
        import h5py
        with h5py.File(ts_path, 'r') as f:
            attrs = dict(f.attrs)
            start = attrs.get('START_DATE', '')
            end = attrs.get('END_DATE', '')
            if isinstance(start, bytes):
                start = start.decode('utf-8')
            if isinstance(end, bytes):
                end = end.decode('utf-8')
            meta['date_start'] = str(start).strip("b'")[:8] if start else ''
            meta['date_end'] = str(end).strip("b'")[:8] if end else ''

            # Fallback: get from date array
            if not meta['date_start']:
                dates = attrs.get('date')
                if dates is not None and len(dates) > 0:
                    d0 = dates[0]
                    d1 = dates[-1]
                    meta['date_start'] = (d0.decode('utf-8') if isinstance(d0, bytes) else str(d0)).strip("b'")[:8]
                    meta['date_end'] = (d1.decode('utf-8') if isinstance(d1, bytes) else str(d1)).strip("b'")[:8]

            # LON/LAT corner attributes for center (may be projected coords)
            lons = [attrs.get(f'LON_REF{i}') for i in range(1, 5)]
            lats = [attrs.get(f'LAT_REF{i}') for i in range(1, 5)]
            if all(v is not None for v in lons + lats):
                try:
                    clon = sum(float(v) for v in lons) / 4
                    clat = sum(float(v) for v in lats) / 4
                    epsg_val = attrs.get('EPSG')
                    if epsg_val is not None:
                        from rasterio.warp import transform
                        epsg_str = f'EPSG:{int(epsg_val)}'
                        wlon, wlat = transform(epsg_str, 'EPSG:4326', [clon], [clat])
                        meta['center_lon'] = round(float(wlon[0]), 5)
                        meta['center_lat'] = round(float(wlat[0]), 5)
                    else:
                        meta['center_lon'] = round(clon, 5)
                        meta['center_lat'] = round(clat, 5)
                except Exception:
                    pass
    except Exception:
        pass

    # Fallback: try velocity.tif with proper CRS conversion
    if meta['center_lon'] is None:
        try:
            import rasterio
            from rasterio.warp import transform
            with rasterio.open(vel_path) as src:
                gt = src.transform
                rows, cols = src.height, src.width
                crx = gt[0] + (cols // 2) * gt[1] + (rows // 2) * gt[2]
                cry = gt[3] + (cols // 2) * gt[4] + (rows // 2) * gt[5]
                if src.crs and src.crs.is_projected:
                    lon, lat = transform(src.crs, 'EPSG:4326', [crx], [cry])
                    meta['center_lon'] = round(float(lon[0]), 5)
                    meta['center_lat'] = round(float(lat[0]), 5)
                else:
                    meta['center_lon'] = round(float(crx), 5)
                    meta['center_lat'] = round(float(cry), 5)
        except Exception:
            pass

    return meta


def run_analysis(vel_path, ts_path, output_dir, task_id=''):
    """Execute the complete deformation analysis pipeline.

    Args:
        vel_path: Path to velocity GeoTIFF (masked, e.g. vel_{pf}.tif).
        ts_path: Path to timeseries HDF5 (timeseries_ramp_demErr.h5 or similar).
        output_dir: Directory to write analysis outputs (creates analysis/ subdir).
        task_id: Task ID for naming.

    Returns:
        dict with report (full analysis JSON), charts (list), zip_path, zip_url, errors.
    """
    analysis_dir = Path(output_dir) / 'analysis'
    analysis_dir.mkdir(parents=True, exist_ok=True)

    vel_file = Path(vel_path)
    ts_file = Path(ts_path)
    errors = []

    if not vel_file.is_file():
        return {'error': f'Velocity file not found: {vel_path}', 'success': False}
    if not ts_file.is_file():
        return {'error': f'Timeseries file not found: {ts_path}', 'success': False}

    meta = _extract_meta(str(vel_file), str(ts_file))

    report = {
        'task_id': task_id,
        'velocity_file': str(vel_file),
        'timeseries_file': str(ts_file),
        'meta': meta,
    }

    import numpy as np
    import rasterio

    # ── 1. Velocity statistics ──
    vel_data_mm = np.array([])
    try:
        with rasterio.open(str(vel_file)) as src:
            data = src.read(1).astype(np.float32)
            nodata = src.nodata
            if nodata is not None:
                data = np.where(data == nodata, np.nan, data)
            vel_gt = src.transform

        valid_mask = np.isfinite(data)
        total_valid = int(np.sum(valid_mask))
        valid = data[valid_mask]
        valid_mm = valid * 1000.0
        data_mm = data * 1000.0
        subsiding_2d = valid_mask & (data_mm < -3.0)
        subsiding_count = int(np.sum(subsiding_2d))

        report['velocity'] = {
            'mean_mm_yr': round(float(np.mean(valid_mm)), 2),
            'min_mm_yr': round(float(np.amin(valid_mm)), 2),
            'max_mm_yr': round(float(np.amax(valid_mm)), 2),
            'std_mm_yr': round(float(np.std(valid_mm)), 2),
            'p10_mm_yr': round(float(np.percentile(valid_mm, 10)), 2),
            'p25_mm_yr': round(float(np.percentile(valid_mm, 25)), 2),
            'p50_mm_yr': round(float(np.percentile(valid_mm, 50)), 2),
            'p75_mm_yr': round(float(np.percentile(valid_mm, 75)), 2),
            'p90_mm_yr': round(float(np.percentile(valid_mm, 90)), 2),
            'valid_pixels': total_valid,
            'subsiding_pct': round(subsiding_count / total_valid * 100, 2) if total_valid > 0 else 0.0,
        }
        vel_data_mm = valid_mm
    except Exception as e:
        errors.append(f'velocity_stats: {e}')
        report['velocity'] = {'error': str(e)}
        vel_data_mm = np.array([])

    # ── 2. Funnel detection ──
    try:
        from .bowl import detect_bowls
        bowls, bowl_stats = detect_bowls(str(vel_file))
        report['funnels'] = bowls
        report['funnel_count'] = len(bowls)
        report['bowl_stats'] = bowl_stats
    except Exception as e:
        errors.append(f'bowl_detection: {e}')
        report['funnels'] = []
        report['funnel_count'] = 0
        report['bowl_stats'] = {}
        bowls = []

    # ── 3. Cumulative displacement ──
    try:
        from .cumulative import compute_cumulative
        cum = compute_cumulative(str(vel_file), str(ts_file))
        report['cumulative'] = cum
    except Exception as e:
        errors.append(f'cumulative: {e}')
        report['cumulative'] = {'error': str(e)}

    # ── 4. Gradient / strain rate ──
    try:
        from .gradient import compute_gradient
        grad = compute_gradient(str(vel_file))
        report['strain'] = grad
    except Exception as e:
        errors.append(f'gradient: {e}')
        report['strain'] = {'error': str(e)}

    # ── 5. Zoning ──
    try:
        from .zoning import classify_zones
        zones = classify_zones(str(vel_file))
        report['zoning'] = zones
    except Exception as e:
        errors.append(f'zoning: {e}')
        report['zoning'] = []
        zones = []

    # ── 6. Acceleration detection ──
    accel_results = []
    try:
        from .timeseries import detect_acceleration
        if bowls and ts_file.is_file():
            accel_results = detect_acceleration(str(ts_file), bowls)
        report['acceleration'] = {
            'results': accel_results,
            'accelerated_count': sum(1 for a in accel_results if a.get('accelerated')),
        }
    except Exception as e:
        errors.append(f'acceleration: {e}')
        report['acceleration'] = {'error': str(e), 'results': [], 'accelerated_count': 0}

    # ── 7. Time series for cumulative chart ──
    ts_info = {'dates': [], 'displacement_mm': []}
    try:
        from .timeseries import compute_mean_timeseries
        ts_info = compute_mean_timeseries(str(ts_file))
    except Exception as e:
        errors.append(f'mean_timeseries: {e}')

    # ── 8. Charts (statistical) ──
    charts = {}
    try:
        from .charts import generate_charts
        charts = generate_charts(
            vel_data_mm, ts_info,
            report.get('velocity', {}), zones, bowls, accel_results,
            meta, str(analysis_dir),
        )
    except Exception as e:
        errors.append(f'charts: {e}')

    # ── 9. Charts (spatial / raster) ──
    gradient_tif = report.get('strain', {}).get('gradient_tif')
    try:
        from .charts import generate_spatial_charts
        sp_charts = generate_spatial_charts(
            str(vel_file), str(ts_file), gradient_tif, zones,
            meta, str(analysis_dir),
        )
        charts.update(sp_charts)
    except Exception as e:
        errors.append(f'spatial_charts: {e}')

    report['charts'] = list(charts.values())

    # ── 10. Markdown report ──
    md_path = analysis_dir / 'analysis_report.md'
    try:
        from .markdown_report import generate_markdown
        generate_markdown(report, str(md_path))
        report['markdown_report'] = 'analysis/analysis_report.md'
    except Exception as e:
        errors.append(f'markdown: {e}')

    # ── 11. Report JSON ──
    report_path = analysis_dir / 'report.json'
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)

    # ── 10. Zip ──
    try:
        zip_path = analysis_dir / '_analysis_results.zip'
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zf:
            for fpath in sorted(analysis_dir.rglob('*')):
                if fpath.is_file() and fpath.name != '_analysis_results.zip':
                    zf.write(fpath, str(fpath.relative_to(analysis_dir)))
        report['zip_path'] = str(zip_path)
        report['zip_ready'] = True
    except Exception as e:
        errors.append(f'zip: {e}')
        report['zip_ready'] = False

    report['success'] = True
    report['errors'] = errors if errors else []

    return report
