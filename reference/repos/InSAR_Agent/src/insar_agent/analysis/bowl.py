"""Subsidence bowl detection and 2D Gaussian characterization."""

import numpy as np
from scipy.ndimage import gaussian_filter, minimum_filter, label
from scipy.optimize import curve_fit
import rasterio


def _gaussian2d(coords, *params):
    x, y = coords
    x0, y0, sigma_x, sigma_y, A, offset = params
    dx = ((x - x0) ** 2) / (2 * max(sigma_x, 0.1) ** 2)
    dy = ((y - y0) ** 2) / (2 * max(sigma_y, 0.1) ** 2)
    return offset + A * np.exp(-(dx + dy))


def _pixel_sizes(gt, crs):
    """Return (pixel_area_km2, pixel_w_km, pixel_h_km)."""
    dx = abs(gt.a)
    dy = abs(gt.e)
    if crs and crs.is_projected:
        return dx * dy / 1e6, dx / 1000.0, dy / 1000.0
    else:
        return dx * 111320.0 * dy * 111320.0 / 1e6, dx * 111.32, dy * 111.32


def _to_lonlat(col, row, gt, src_crs):
    """Pixel -> (lon, lat), converting from projected CRS if needed."""
    x = gt.c + col * gt.a + row * gt.b
    y = gt.f + col * gt.d + row * gt.e
    if src_crs and src_crs.is_projected:
        from rasterio.warp import transform
        lon, lat = transform(src_crs, 'EPSG:4326', [x], [y])
        return float(lon[0]), float(lat[0])
    return float(x), float(y)


def detect_bowls(
    vel_path,
    smooth_sigma=3,
    min_depth_mm_yr=20.0,
    min_separation=20,
    window_radius=40,
    r2_threshold=0.6,
):
    with rasterio.open(str(vel_path)) as src:
        data = src.read(1).astype(np.float32)
        nodata = src.nodata
        gt = src.transform
        crs = src.crs
        if nodata is not None:
            data = np.where(data == nodata, np.nan, data)

    valid_mask = np.isfinite(data)
    total_valid = int(np.sum(valid_mask))
    data_mm = data * 1000.0

    subs_mask = valid_mask & (data_mm < -3.0)
    subs_count = int(np.sum(subs_mask))
    subs_pct = round(subs_count / total_valid * 100, 2) if total_valid > 0 else 0.0

    stats = {
        'total_valid_pixels': total_valid,
        'subsiding_pixels': subs_count,
        'subsiding_pct': subs_pct,
        'min_depth_mm_yr': min_depth_mm_yr,
        'smooth_sigma': smooth_sigma,
    }

    if subs_count < 100:
        return [], stats

    subs = np.zeros_like(data_mm, dtype=np.float64)
    subs[subs_mask] = -data_mm[subs_mask]

    smoothed = gaussian_filter(subs, sigma=smooth_sigma)

    sep = min_separation
    footprint = np.ones((2 * sep + 1, 2 * sep + 1), dtype=bool)
    mf = minimum_filter(smoothed, footprint=footprint, mode='constant')
    is_local_max = (smoothed == mf) & (smoothed > min_depth_mm_yr)

    center_labels, num_centers = label(is_local_max)
    if num_centers == 0:
        return [], stats

    bowls = []
    rows, cols = data_mm.shape
    hw = window_radius
    px_area, px_w, px_h = _pixel_sizes(gt, crs)

    for lid in range(1, num_centers + 1):
        region_mask = center_labels == lid
        if np.sum(region_mask) < 1:
            continue

        cy, cx = np.unravel_index(np.argmax(smoothed * region_mask), smoothed.shape)
        if not subs_mask[cy, cx]:
            continue

        peak_mm = float(smoothed[cy, cx])
        if peak_mm < min_depth_mm_yr:
            continue

        r1, r2 = max(0, cy - hw), min(rows, cy + hw + 1)
        c1, c2 = max(0, cx - hw), min(cols, cx + hw + 1)

        sub_win = subs[r1:r2, c1:c2].astype(np.float64)
        if sub_win.size < 100:
            continue

        win_subs_pct = np.sum(sub_win > 3.0) / sub_win.size
        if win_subs_pct < 0.1:
            continue

        yy, xx = np.mgrid[r1:r2, c1:c2]
        yy_f, xx_f = yy.ravel().astype(np.float64), xx.ravel().astype(np.float64)
        zz_f = sub_win.ravel()

        valid = np.isfinite(zz_f) & (zz_f > 0)
        if valid.sum() < 50:
            continue
        yy_v, xx_v, zz_v = yy_f[valid], xx_f[valid], zz_f[valid]

        peak = float(np.max(zz_v))
        p0 = [float(cx), float(cy), hw / 3.0, hw / 3.0, peak * 0.9, peak * 0.1]
        lb = [c1, r1, 1.0, 1.0, 0, 0]
        ub = [c2, r2, hw, hw, peak * 2, peak * 0.5]

        try:
            popt, _ = curve_fit(_gaussian2d, (xx_v, yy_v), zz_v,
                                p0=p0, maxfev=3000, bounds=(lb, ub))
        except Exception:
            continue

        fx0, fy0, fsx, fsy, fA, foff = popt

        pred = _gaussian2d((xx_v, yy_v), *popt)
        ss_res = np.sum((zz_v - pred) ** 2)
        ss_tot = np.sum((zz_v - np.mean(zz_v)) ** 2)
        r2 = 1 - ss_res / (ss_tot + 1e-12)
        if r2 < r2_threshold:
            continue

        cr = int(round(fy0))
        cc = int(round(fx0))
        if cr < 0 or cr >= rows or cc < 0 or cc >= cols:
            continue
        if not subs_mask[cr, cc]:
            continue

        # Reject if fit center drifted too far from detected peak
        if np.sqrt((fy0 - cy) ** 2 + (fx0 - cx) ** 2) > hw * 0.8:
            continue

        lon, lat = _to_lonlat(fx0, fy0, gt, crs)
        depth_mm = abs(float(data_mm[cr, cc]))
        area_km2 = round(np.pi * fsx * fsy * px_area, 3)
        rx_km = round(fsx * px_w, 3)
        ry_km = round(fsy * px_h, 3)

        bowls.append({
            'id': len(bowls) + 1,
            'center_lon': round(lon, 5),
            'center_lat': round(lat, 5),
            'depth_mm_yr': round(depth_mm, 1),
            'radius_x_km': rx_km,
            'radius_y_km': ry_km,
            'area_km2': area_km2,
            'r_squared': round(r2, 3),
            'center_row': cr,
            'center_col': cc,
            'window_subs_pct': round(win_subs_pct, 3),
        })

    return bowls, stats
