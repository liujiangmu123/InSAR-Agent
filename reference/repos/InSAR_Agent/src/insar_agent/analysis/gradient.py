"""Deformation gradient / strain rate computation."""

import numpy as np
from scipy.ndimage import gaussian_filter
import rasterio


def _pixel_sizes(gt, crs):
    dx = abs(gt.a)
    dy = abs(gt.e)
    if crs and crs.is_projected:
        return dx, dy, dx * dy / 1e6
    else:
        dx_m = dx * 111320.0
        dy_m = dy * 111320.0
        return dx_m, dy_m, dx_m * dy_m / 1e6


def compute_gradient(vel_path, smooth_sigma=3.0):
    try:
        with rasterio.open(str(vel_path)) as src:
            data = src.read(1).astype(np.float64)
            nodata = src.nodata
            gt = src.transform
            crs = src.crs
            profile = src.profile.copy()

        if nodata is not None:
            data = np.where(data == nodata, np.nan, data)

        dx_m, dy_m, pixel_area_km2 = _pixel_sizes(gt, crs)

        mask = ~np.isfinite(data)
        data[mask] = 0.0
        smoothed = gaussian_filter(data, sigma=smooth_sigma, mode='nearest')
        smoothed[mask] = np.nan

        dv_dy, dv_dx = np.gradient(smoothed)
        with np.errstate(invalid='ignore', divide='ignore'):
            dv_dx_mask = dv_dx / max(dx_m, 1.0)
            dv_dy_mask = dv_dy / max(dy_m, 1.0)
            gradient_mag = np.sqrt(dv_dx_mask ** 2 + dv_dy_mask ** 2)

        gradient_mag[~np.isfinite(gradient_mag)] = np.nan
        valid = gradient_mag[np.isfinite(gradient_mag)]

        if len(valid) == 0:
            return {'mean_gradient': 0, 'max_gradient': 0, 'high_gradient_area_km2': 0,
                    'total_area_km2': 0, 'gradient_tif': None, 'error': 'No valid data'}

        mean_grad = float(np.mean(valid))
        max_grad = float(np.max(valid))
        high_thresh = 2.0 * mean_grad if mean_grad > 1e-12 else max_grad * 0.5
        high_count = int(np.sum(valid > high_thresh))
        high_area_km2 = round(high_count * pixel_area_km2, 3)
        total_area = round(len(valid) * pixel_area_km2, 3)

        output_tif = None
        try:
            out_path = str(vel_path).replace('.tif', '_gradient.tif')
            profile.update(dtype='float32', nodata=np.nan)
            out_data = gradient_mag.astype(np.float32)
            out_data[~np.isfinite(out_data)] = profile['nodata']
            with rasterio.open(out_path, 'w', **profile) as dst:
                dst.write(out_data, 1)
            output_tif = out_path
        except Exception:
            pass

        return {
            'mean_gradient': round(mean_grad, 8),
            'max_gradient': round(max_grad, 8),
            'high_gradient_area_km2': high_area_km2,
            'high_gradient_threshold': round(high_thresh, 8),
            'total_area_km2': total_area,
            'gradient_tif': output_tif,
        }
    except Exception as e:
        return {
            'mean_gradient': 0, 'max_gradient': 0, 'high_gradient_area_km2': 0,
            'total_area_km2': 0, 'gradient_tif': None, 'error': str(e),
        }
