"""Cumulative displacement and volume loss computation."""

import numpy as np
import rasterio
import h5py


def _pixel_area_m2(gt, crs):
    """Pixel area in square meters. Handles both geographic and projected CRS."""
    dx = abs(gt.a)
    dy = abs(gt.e)
    if crs and crs.is_projected:
        return dx * dy
    return dx * 111320.0 * dy * 111320.0


def compute_cumulative(vel_path, ts_path):
    with rasterio.open(str(vel_path)) as src:
        gt = src.transform
        crs = src.crs
    pixel_area_m2 = _pixel_area_m2(gt, crs)

    with h5py.File(ts_path, 'r') as f:
        ts = f.get('timeseries')
        if ts is None:
            return {
                'error': 'No timeseries dataset found',
                'max_displacement_mm': 0, 'mean_displacement_mm': 0,
                'total_volume_loss_m3': 0, 'total_volume_loss_km3': 0,
                'subsiding_area_km2': 0, 'total_area_km2': 0,
                'time_steps': 0,
            }
        cum = np.array(ts[-1], dtype=float) - np.array(ts[0], dtype=float)
        n_steps = ts.shape[0]

    cum[~np.isfinite(cum)] = np.nan
    cum_mm = cum * 1000.0

    valid = ~np.isnan(cum_mm)
    max_disp = float(np.nanmin(cum_mm))
    mean_disp = float(np.nanmean(cum_mm[valid]))

    subs_mask = valid & (cum_mm < 0)
    subsiding_count = int(np.sum(subs_mask))
    total_valid_count = int(np.sum(valid))

    pixel_area_km2 = pixel_area_m2 / 1e6
    subsiding_area_km2 = round(subsiding_count * pixel_area_km2, 3)
    total_area_km2 = round(total_valid_count * pixel_area_km2, 3)

    volume_loss_m3 = 0.0
    if subsiding_count > 0:
        volume_loss_m3 = float(np.sum(np.abs(cum[subs_mask]))) * pixel_area_m2

    return {
        'max_displacement_mm': round(max_disp, 1),
        'mean_displacement_mm': round(mean_disp, 1),
        'total_volume_loss_m3': round(volume_loss_m3, 3),
        'total_volume_loss_km3': round(volume_loss_m3 / 1e9, 6),
        'subsiding_area_km2': subsiding_area_km2,
        'total_area_km2': total_area_km2,
        'time_steps': n_steps,
    }
