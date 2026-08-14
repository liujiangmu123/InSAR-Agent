"""Time series acceleration detection.

For detected subsidence funnels, extracts spatially-averaged
displacement time series and fits a quadratic model to detect
accelerating or decelerating deformation.
"""

import numpy as np
import h5py


def _fit_quadratic(t, d):
    """Fit d(t) = a*t + 0.5*b*t² + c and return acceleration parameter b."""
    A = np.column_stack([t, 0.5 * t ** 2, np.ones_like(t)])
    coeffs, residuals, rank, s = np.linalg.lstsq(A, d, rcond=None)
    a = coeffs[0]
    b = coeffs[1]
    c = coeffs[2]
    pred = a * t + 0.5 * b * t ** 2 + c
    ss_res = np.sum((d - pred) ** 2)
    ss_tot = np.sum((d - np.mean(d)) ** 2)
    r2 = 1 - ss_res / (ss_tot + 1e-10) if ss_tot > 1e-10 else 0.0
    return a, b, c, r2


def detect_acceleration(ts_path, bowl_centers, window_radius=20, min_r2=0.5):
    """Detect acceleration in time series at each funnel center.

    For each funnel, extracts the mean displacement over a window
    around the center and fits a quadratic to detect acceleration.

    Args:
        ts_path: Path to timeseries HDF5 (timeseries_ramp_demErr.h5).
        bowl_centers: List of dicts from detect_bowls() with center_row/center_col.
        window_radius: Pixel radius for spatial averaging around funnel center.
        min_r2: Minimum R² for accelerated conclusion.

    Returns:
        list[dict]: Each dict: bowl_id, velocity_mm_yr, acceleration_mm_yr2,
                    r_squared, accelerated (bool), num_dates, dates (list of str).
    """
    with h5py.File(ts_path, 'r') as f:
        ts = f['timeseries']
        if ts is None:
            return []
        n_steps = ts.shape[0]
        dates_raw = f.attrs.get('date', None)
        dates = []
        if dates_raw is not None:
            for d in dates_raw:
                if isinstance(d, bytes):
                    dates.append(d.decode('utf-8'))
                else:
                    dates.append(str(d))

        results = []
        half = window_radius

        for bowl in bowl_centers:
            cr = bowl.get('center_row')
            cc = bowl.get('center_col')
            if cr is None or cc is None:
                continue

            r1 = max(0, cr - half)
            r2 = min(ts.shape[1], cr + half + 1)
            c1 = max(0, cc - half)
            c2 = min(ts.shape[2], cc + half + 1)

            ts_window = ts[:, r1:r2, c1:c2]
            mean_ts = np.nanmean(ts_window, axis=(1, 2))

            t = np.arange(n_steps, dtype=float)
            valid = np.isfinite(mean_ts)

            if not np.any(valid):
                results.append({
                    'bowl_id': bowl['id'],
                    'velocity_mm_yr': 0, 'acceleration_mm_yr2': 0,
                    'r_squared': 0, 'accelerated': False,
                    'dates': [],
                })
                continue

            t_v = t[valid]
            d_v = mean_ts[valid]
            d_v_mm = d_v * 1000.0

            a, b, c, r2 = _fit_quadratic(t_v, d_v_mm)
            vel_mm_yr = abs(a) * 12.0
            accel_mm_yr2 = b * 12.0

            results.append({
                'bowl_id': bowl['id'],
                'velocity_mm_yr': round(vel_mm_yr, 2),
                'acceleration_mm_yr2': round(accel_mm_yr2, 4),
                'r_squared': round(r2, 3),
                'accelerated': bool(r2 > min_r2 and abs(accel_mm_yr2) > 0.05),
                'mean_ts_mm': [round(v, 2) for v in d_v_mm.tolist()],
                'dates': dates,
            })

    return results


def compute_mean_timeseries(ts_path):
    """Extract spatially-averaged cumulative displacement time series.

    Args:
        ts_path: Path to timeseries HDF5.

    Returns:
        dict with dates (list[str]) and displacement_mm (list[float]).
    """
    try:
        with h5py.File(ts_path, 'r') as f:
            ts = f['timeseries']
            if ts is None:
                return {'dates': [], 'displacement_mm': [], 'error': 'No timeseries dataset'}
            n_steps = ts.shape[0]
            dates_raw = f.attrs.get('date', None)
            dates = []
            if dates_raw is not None:
                for d in dates_raw:
                    if isinstance(d, bytes):
                        dates.append(d.decode('utf-8'))
                    else:
                        dates.append(str(d))
            mean_ts = np.zeros(n_steps, dtype=np.float64)
            for i in range(n_steps):
                frame = np.array(ts[i], dtype=np.float64)
                frame[~np.isfinite(frame)] = np.nan
                mean_ts[i] = np.nanmean(frame)
            mean_ts_mm = (mean_ts * 1000.0).tolist()
        return {
            'dates': dates,
            'displacement_mm': [round(v, 2) if v is not None and np.isfinite(v) else 0.0 for v in mean_ts_mm],
            'time_steps': n_steps,
        }
    except Exception as e:
        return {'dates': [], 'displacement_mm': [], 'error': str(e)}
