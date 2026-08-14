"""Deformation zone classification."""

import numpy as np
from scipy.ndimage import label
import rasterio


DEFAULT_ZONE_THRESHOLDS_MM_YR = {
    'severe_subsidence': -30.0,
    'moderate_subsidence': -10.0,
    'slight_subsidence': -3.0,
    'slight_uplift': 3.0,
    'moderate_uplift': 10.0,
}


def _pixel_area_km2(gt, crs):
    dx = abs(gt.a)
    dy = abs(gt.e)
    if crs and crs.is_projected:
        return dx * dy / 1e6
    return dx * 111320.0 * dy * 111320.0 / 1e6


def classify_zones(vel_path, thresholds=None):
    t = thresholds or DEFAULT_ZONE_THRESHOLDS_MM_YR

    with rasterio.open(str(vel_path)) as src:
        data = src.read(1).astype(np.float32)
        nodata = src.nodata
        gt = src.transform
        crs = src.crs
        if nodata is not None:
            data = np.where(data == nodata, np.nan, data)

    data_mm = data * 1000.0
    total_pixels = int(np.sum(~np.isnan(data_mm)))
    pixel_area = _pixel_area_km2(gt, crs)

    zones = [
        ('severe_subsidence', float('-inf'), t['severe_subsidence']),
        ('moderate_subsidence', t['severe_subsidence'], t['moderate_subsidence']),
        ('slight_subsidence', t['moderate_subsidence'], t['slight_subsidence']),
        ('stable', t['slight_subsidence'], t['slight_uplift']),
        ('slight_uplift', t['slight_uplift'], t['moderate_uplift']),
        ('significant_uplift', t['moderate_uplift'], float('inf')),
    ]

    results = []
    for name, lo, hi in zones:
        mask = (~np.isnan(data_mm)) & (data_mm >= lo) & (data_mm < hi)
        count = int(np.sum(mask))
        pct = round(count / total_pixels * 100, 2) if total_pixels > 0 else 0.0
        area = round(count * pixel_area, 2)
        mean_val = round(float(np.nanmean(data_mm[mask])), 1) if count > 0 else 0.0

        if lo == float('-inf'):
            rng = f'< {hi}'
        elif hi == float('inf'):
            rng = f'> {lo}'
        else:
            rng = f'{lo} ~ {hi}'

        results.append({
            'zone': name,
            'rate_range_mm_yr': rng,
            'area_km2': area,
            'pct': pct,
            'mean_rate_mm_yr': mean_val,
            'pixel_count': count,
        })

    return results
