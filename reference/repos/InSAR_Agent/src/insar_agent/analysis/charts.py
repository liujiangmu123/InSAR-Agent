"""Chart generation for deformation analysis.

Generates PNG charts from analysis results using matplotlib.
Each chart includes region metadata (Path, Frame, date range) in the title.
"""

import io
import os
from datetime import datetime
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np


def _fig_to_bytes(fig, dpi=120):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, bbox_inches='tight', facecolor='white')
    buf.seek(0)
    plt.close(fig)
    return buf.read()


def _make_region_subtitle(meta):
    """Build 'Path {p} Frame {f} | {start} ~ {end}' subtitle."""
    parts = []
    if meta.get('path') is not None:
        parts.append(f'Path {meta["path"]}')
    if meta.get('frame') is not None:
        parts.append(f'Frame {meta["frame"]}')
    if meta.get('date_start'):
        parts.append(f'{meta["date_start"]} ~ {meta.get("date_end", "")}')
    return ' | '.join(parts) if parts else ''


def velocity_histogram(velocity_data_mm, velocity_stats, meta, output_path):
    """Real histogram of velocity values with percentile markers.

    Args:
        velocity_data_mm: 1D numpy array of valid velocity values in mm/yr.
        velocity_stats: Dict with mean, min, max, std, and percentile values.
        meta: Dict with path, frame, date_start, date_end.
    """
    fig, ax = plt.subplots(figsize=(10, 4.5))
    color = '#1a73e8'

    perc = np.percentile(velocity_data_mm, [1, 99])
    bins = np.linspace(perc[0], perc[1], 80)
    ax.hist(velocity_data_mm, bins=bins, color=color, alpha=0.75,
            edgecolor='white', linewidth=0.3)

    ax.axvline(x=0, color='#ef4444', linewidth=1, linestyle='--', alpha=0.7)

    mean_val = velocity_stats.get('mean_mm_yr', 0)
    ax.axvline(x=mean_val, color='#e67e22', linewidth=1.5, linestyle='-',
               alpha=0.8, label=f'Mean: {mean_val:.1f} mm/yr')

    p50 = velocity_stats.get('p50_mm_yr', 0)
    ax.axvline(x=p50, color='#2ecc71', linewidth=1, linestyle=':', alpha=0.7,
               label=f'Median (P50): {p50:.1f} mm/yr')

    ax.set_xlabel('Velocity (mm/yr)', fontsize=10, color='#333')
    ax.set_ylabel('Pixel Count', fontsize=10, color='#333')
    title = 'LOS Velocity Distribution'
    subtitle = _make_region_subtitle(meta)
    if subtitle:
        title += f'\n{subtitle}'
    ax.set_title(title, fontsize=12, fontweight='bold', color='#333')
    ax.legend(fontsize=9, loc='upper right')
    ax.tick_params(colors='#333')
    ax.grid(True, alpha=0.2, color='#ccc')

    # Add text box with key stats
    stats_text = (
        f'Min: {velocity_stats.get("min_mm_yr", "?"):.1f} mm/yr\n'
        f'Max: {velocity_stats.get("max_mm_yr", "?"):.1f} mm/yr\n'
        f'Std:  {velocity_stats.get("std_mm_yr", "?"):.1f} mm/yr\n'
        f'P10:  {velocity_stats.get("p10_mm_yr", "?"):.1f} mm/yr\n'
        f'P90:  {velocity_stats.get("p90_mm_yr", "?"):.1f} mm/yr'
    )
    ax.text(0.02, 0.95, stats_text, transform=ax.transAxes,
            fontsize=8, verticalalignment='top', family='monospace',
            bbox=dict(boxstyle='round', facecolor='white', alpha=0.8, edgecolor='#ddd'))

    fig.tight_layout()
    img = _fig_to_bytes(fig)
    with open(output_path, 'wb') as f:
        f.write(img)
    return output_path


def cumulative_timeseries(dates, displacement_mm, meta, output_path):
    """Cumulative displacement time series with proper date axis.

    Args:
        dates: List of date strings (YYYYMMDD format).
        displacement_mm: List of cumulative displacement values in mm.
        meta: Dict with path, frame, date_start, date_end.
    """
    fig, ax = plt.subplots(figsize=(10, 4.5))
    color = '#1a73e8'

    if dates and len(dates) == len(displacement_mm) and len(dates) > 0:
        dts = [datetime.strptime(d, '%Y%m%d') for d in dates]
        ax.plot(dts, displacement_mm, 'o-', markersize=2, linewidth=1, color=color)
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=4))
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        fig.autofmt_xdate()
    elif len(displacement_mm) > 0:
        x = np.arange(len(displacement_mm))
        ax.plot(x, displacement_mm, 'o-', markersize=2, linewidth=1, color=color)
        ax.set_xlabel('Time Step', fontsize=10, color='#333')
    else:
        ax.text(0.5, 0.5, 'No time series data', ha='center', va='center',
                transform=ax.transAxes, fontsize=14, color='#999')
        fig.tight_layout()
        img = _fig_to_bytes(fig)
        with open(output_path, 'wb') as f:
            f.write(img)
        return output_path

    ax.axhline(y=0, color='#ef4444', linewidth=0.8, linestyle='--')
    ax.set_ylabel('Cumulative Displacement (mm)', fontsize=10, color='#333')
    title = 'Spatial Mean Cumulative Displacement'
    subtitle = _make_region_subtitle(meta)
    if subtitle:
        title += f'\n{subtitle}'
    ax.set_title(title, fontsize=12, fontweight='bold', color='#333')
    ax.tick_params(colors='#333')
    ax.grid(True, alpha=0.2, color='#ccc')

    if displacement_mm:
        final = displacement_mm[-1]
        ax.annotate(
            f'{final:.1f} mm', xy=(len(displacement_mm) - 1, final),
            fontsize=9, fontweight='bold', color='#c0392b',
            bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.8, edgecolor='#c0392b'),
        )

    fig.tight_layout()
    img = _fig_to_bytes(fig)
    with open(output_path, 'wb') as f:
        f.write(img)
    return output_path


def funnel_timeseries(accel_results, meta, output_path):
    """Overlaid time series for each detected funnel.

    Args:
        accel_results: List from detect_acceleration().
        meta: Dict with path, frame, date_start, date_end.
    """
    fig, ax = plt.subplots(figsize=(10, 4.5))
    colors = ['#e74c3c', '#3498db', '#2ecc71', '#e67e22', '#9b59b6', '#1abc9c']

    has_data = False
    for i, result in enumerate(accel_results):
        ts = result.get('mean_ts_mm', [])
        if not ts or len(ts) < 2:
            continue
        has_data = True
        bowl_id = result.get('bowl_id', i + 1)
        color = colors[i % len(colors)]
        vel = result.get('velocity_mm_yr', 0)
        accel = result.get('acceleration_mm_yr2', 0)
        acc_tag = f' accel={accel:+.1f}' if result.get('accelerated') else ''
        label = f'Funnel {bowl_id} (v={vel:.1f}{acc_tag})'

        dates = result.get('dates', [])
        if dates and len(dates) == len(ts):
            dts = [datetime.strptime(d, '%Y%m%d') for d in dates]
            ax.plot(dts, ts, linewidth=1.3, color=color, alpha=0.85, label=label)
        else:
            ax.plot(ts, linewidth=1.3, color=color, alpha=0.85, label=label)

    if not has_data:
        ax.text(0.5, 0.5, 'No funnel time series', ha='center', va='center',
                transform=ax.transAxes, fontsize=14, color='#999')
    else:
        ax.axhline(y=0, color='#ef4444', linewidth=0.8, linestyle='--')
        ax.legend(fontsize=8, loc='lower left')
        ax.set_xlabel('Date', fontsize=10, color='#333')
        if dates:
            ax.xaxis.set_major_locator(mdates.MonthLocator(interval=4))
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
            fig.autofmt_xdate()

    ax.set_ylabel('Cumulative Displacement (mm)', fontsize=10, color='#333')
    title = 'Funnel-Averaged Displacement Time Series'
    subtitle = _make_region_subtitle(meta)
    if subtitle:
        title += f'\n{subtitle}'
    ax.set_title(title, fontsize=12, fontweight='bold', color='#333')
    ax.tick_params(colors='#333')
    ax.grid(True, alpha=0.2, color='#ccc')

    fig.tight_layout()
    img = _fig_to_bytes(fig)
    with open(output_path, 'wb') as f:
        f.write(img)
    return output_path


def zoning_bar_chart(zones, meta, output_path):
    """Horizontal bar chart of area per deformation zone.

    Args:
        zones: List from classify_zones().
        meta: Dict with path, frame, date_start, date_end.
    """
    fig, ax = plt.subplots(figsize=(10, 4))

    names = [z['zone'].replace('_', ' ').title() for z in zones]
    rnames = [z['rate_range_mm_yr'] for z in zones]
    areas = [z['area_km2'] for z in zones]
    mean_rates = [z['mean_rate_mm_yr'] for z in zones]
    zone_colors = ['#c0392b', '#e74c3c', '#f39c12', '#2ecc71', '#3498db', '#9b59b6']

    bars = ax.barh(names, areas, color=zone_colors[:len(names)], alpha=0.85,
                   edgecolor='#333', linewidth=0.5)

    max_area = max(areas) if areas else 1
    for bar, area, rate, rname in zip(bars, areas, mean_rates, rnames):
        if area > 0:
            ax.text(bar.get_width() + max_area * 0.02, bar.get_y() + bar.get_height() / 2,
                    f'{area:.1f} km^2  ({rate:+.1f} mm/yr)\n  [{rname}]',
                    va='center', fontsize=8, color='#333')

    ax.set_xlabel('Area (km^2)', fontsize=10, color='#333')
    title = 'Zonal Deformation Area Distribution'
    subtitle = _make_region_subtitle(meta)
    if subtitle:
        title += f'\n{subtitle}'
    ax.set_title(title, fontsize=12, fontweight='bold', color='#333')
    ax.tick_params(colors='#333')
    ax.grid(axis='x', alpha=0.2, color='#ccc')
    ax.invert_yaxis()

    fig.tight_layout()
    img = _fig_to_bytes(fig)
    with open(output_path, 'wb') as f:
        f.write(img)
    return output_path


def generate_charts(vel_data_mm, ts_info, velocity_stats, zones, bowls, accel_results, meta, output_dir):
    """Generate all analysis charts into output_dir.

    Args:
        vel_data_mm: 1D numpy array of valid velocity values (mm/yr).
        ts_info: Dict with 'dates' and 'displacement_mm' from compute_mean_timeseries.
        velocity_stats: Dict of velocity percentiles.
        zones: List from classify_zones.
        bowls: List from detect_bowls.
        accel_results: List from detect_acceleration.
        meta: Dict with path, frame, date_start, date_end.
        output_dir: Absolute path to save PNGs.

    Returns:
        dict: chart name -> relative path.
    """
    out = str(output_dir)
    os.makedirs(out, exist_ok=True)
    charts = {}

    p = os.path.join(out, 'velocity_histogram.png')
    velocity_histogram(vel_data_mm, velocity_stats, meta, p)
    charts['velocity_histogram'] = 'analysis/velocity_histogram.png'

    p = os.path.join(out, 'cumulative_timeseries.png')
    cumulative_timeseries(
        ts_info.get('dates', []),
        ts_info.get('displacement_mm', []),
        meta, p,
    )
    charts['cumulative_timeseries'] = 'analysis/cumulative_timeseries.png'

    p = os.path.join(out, 'funnel_timeseries.png')
    funnel_timeseries(accel_results, meta, p)
    charts['funnel_timeseries'] = 'analysis/funnel_timeseries.png'

    p = os.path.join(out, 'zoning_bar_chart.png')
    zoning_bar_chart(zones, meta, p)
    charts['zoning_bar_chart'] = 'analysis/zoning_bar_chart.png'

    return charts


# ── Raster / spatial charts ──

_MAX_RASTER_DIM = 1400


def _render_raster(data_2d, cmap_name, vmin, vmax, label, meta, title, output_path):
    """Render a 2D raster as a colormap PNG with colorbar."""
    import matplotlib.colors as mcolors

    data = np.asarray(data_2d, dtype=np.float64)
    valid = data[np.isfinite(data)]
    if len(valid) == 0:
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.text(0.5, 0.5, 'No valid data', ha='center', va='center',
                transform=ax.transAxes, fontsize=14, color='#999')
        fig.tight_layout()
        img = _fig_to_bytes(fig)
        with open(output_path, 'wb') as f:
            f.write(img)
        return output_path

    if vmin is None:
        vmin = float(np.percentile(valid, 2))
    if vmax is None:
        vmax = float(np.percentile(valid, 98))

    if vmax <= vmin:
        vmax = vmin + 1e-6

    rows, cols = data.shape
    if max(rows, cols) > _MAX_RASTER_DIM:
        scale = _MAX_RASTER_DIM / max(rows, cols)
        new_rows = int(rows * scale)
        new_cols = int(cols * scale)

        from PIL import Image
        rgba = np.zeros((rows, cols, 4), dtype=np.uint8)
        norm = (data - vmin) / (vmax - vmin)
        norm = np.clip(norm, 0, 1)
        cmap_obj = plt.cm.get_cmap(cmap_name)
        colored = cmap_obj(norm, bytes=True)
        colored[~np.isfinite(data)] = [255, 255, 255, 0]
        img_pil = Image.fromarray(colored)
        img_pil = img_pil.resize((new_cols, new_rows), Image.LANCZOS)
        colored_resized = np.array(img_pil)
    else:
        norm = (data - vmin) / (vmax - vmin)
        norm = np.clip(norm, 0, 1)
        cmap_obj = plt.cm.get_cmap(cmap_name)
        colored_resized = cmap_obj(norm, bytes=True)
        colored_resized[~np.isfinite(data)] = [255, 255, 255, 0]
        new_rows, new_cols = rows, cols

    dpi = 100
    fig_w = max(new_cols / dpi, 6)
    fig_h = new_rows / dpi + 0.5

    fig = plt.figure(figsize=(fig_w, fig_h), dpi=dpi)
    gs = fig.add_gridspec(2, 1, height_ratios=[new_rows / dpi, 0.4], hspace=0.05)

    ax_img = fig.add_subplot(gs[0])
    ax_img.imshow(colored_resized, aspect='equal')
    ax_img.set_xticks([])
    ax_img.set_yticks([])

    full_title = title
    subtitle = _make_region_subtitle(meta)
    if subtitle:
        full_title += f'\n{subtitle}'
    ax_img.set_title(full_title, fontsize=11, fontweight='bold', color='#333')

    ax_cbar = fig.add_subplot(gs[1])
    norm_bar = mcolors.Normalize(vmin=vmin, vmax=vmax)
    cb = fig.colorbar(
        plt.cm.ScalarMappable(norm=norm_bar, cmap=cmap_name),
        cax=ax_cbar, orientation='horizontal',
    )
    cb.set_label(label, color='#333', fontsize=10)
    cb.ax.tick_params(labelsize=8, colors='#333')

    fig.tight_layout()
    img = _fig_to_bytes(fig, dpi=dpi)
    with open(output_path, 'wb') as f:
        f.write(img)
    return output_path


def velocity_map(vel_path, meta, output_path):
    """Velocity field raster map with colorbar."""
    import rasterio
    with rasterio.open(str(vel_path)) as src:
        data = src.read(1).astype(np.float32)
        nodata = src.nodata
        if nodata is not None:
            data = np.where(data == nodata, np.nan, data)
    data_mm = data * 1000.0
    return _render_raster(
        data_mm, 'jet', None, None, 'Velocity (mm/yr)',
        meta, 'LOS Velocity Map', output_path,
    )


def cumulative_map(ts_path, meta, output_path):
    """Cumulative displacement map at last time step."""
    import h5py
    with h5py.File(ts_path, 'r') as f:
        ts = f['timeseries']
        if ts is None:
            fig, ax = plt.subplots(figsize=(8, 5))
            ax.text(0.5, 0.5, 'No timeseries data', ha='center', va='center',
                    transform=ax.transAxes, fontsize=14, color='#999')
            fig.tight_layout()
            img = _fig_to_bytes(fig)
            with open(output_path, 'wb') as f:
                f.write(img)
            return output_path
        cum = np.array(ts[-1], dtype=float) - np.array(ts[0], dtype=float)
    cum_mm = cum * 1000.0
    cum_mm[~np.isfinite(cum_mm)] = np.nan
    return _render_raster(
        cum_mm, 'jet', None, None, 'Cumulative Displacement (mm)',
        meta, 'Cumulative Displacement Map', output_path,
    )


def strain_map(gradient_tif, meta, output_path):
    """Strain rate / gradient magnitude raster map."""
    import rasterio
    if gradient_tif and os.path.isfile(gradient_tif):
        with rasterio.open(gradient_tif) as src:
            data = src.read(1).astype(np.float32)
            nodata = src.nodata
            if nodata is not None:
                data = np.where(data == nodata, np.nan, data)
    else:
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.text(0.5, 0.5, 'Strain rate not available', ha='center', va='center',
                transform=ax.transAxes, fontsize=14, color='#999')
        fig.tight_layout()
        img = _fig_to_bytes(fig)
        with open(output_path, 'wb') as f:
            f.write(img)
        return output_path

    valid = data[np.isfinite(data)]
    if len(valid) == 0:
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.text(0.5, 0.5, 'No valid strain data', ha='center', va='center',
                transform=ax.transAxes, fontsize=14, color='#999')
        fig.tight_layout()
        img = _fig_to_bytes(fig)
        with open(output_path, 'wb') as f:
            f.write(img)
        return output_path

    vmax = float(np.percentile(valid, 98))
    vmin = 0.0
    if vmax <= vmin:
        vmax = vmin + 1e-6
    return _render_raster(
        data, 'hot', vmin, vmax, 'Gradient (m/yr/m)',
        meta, 'Deformation Gradient Magnitude', output_path,
    )


def zoning_map(zones, vel_path, meta, output_path):
    """Color-coded deformation zone classification map."""
    import rasterio

    with rasterio.open(str(vel_path)) as src:
        data = src.read(1).astype(np.float32)
        nodata = src.nodata
        if nodata is not None:
            data = np.where(data == nodata, np.nan, data)

    data_mm = data * 1000.0
    zone_map = np.full(data_mm.shape, -1, dtype=np.int8)
    zone_colors_hex = ['#c0392b', '#e74c3c', '#f39c12', '#2ecc71', '#3498db', '#9b59b6']
    zone_names = ['Severe Subs.', 'Moderate Subs.', 'Slight Subs.',
                  'Stable', 'Slight Uplift', 'Sig. Uplift']
    thresholds = [-30, -10, -3, 3, 10]

    valid = np.isfinite(data_mm)
    for i in range(6):
        if i == 0:
            mask = valid & (data_mm < thresholds[0])
        elif i == 5:
            mask = valid & (data_mm >= thresholds[-1])
        else:
            mask = valid & (data_mm >= thresholds[i - 1]) & (data_mm < thresholds[i])
        zone_map[mask] = i

    if not np.any(valid):
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.text(0.5, 0.5, 'No valid data', ha='center', va='center',
                transform=ax.transAxes, fontsize=14, color='#999')
        fig.tight_layout()
        img = _fig_to_bytes(fig)
        with open(output_path, 'wb') as f:
            f.write(img)
        return output_path

    rows, cols = zone_map.shape
    if max(rows, cols) > _MAX_RASTER_DIM:
        scale = _MAX_RASTER_DIM / max(rows, cols)
        new_rows, new_cols = int(rows * scale), int(cols * scale)
        from PIL import Image
        rgb = np.zeros((rows, cols, 3), dtype=np.uint8)
        from matplotlib.colors import to_rgb
        for i, hx in enumerate(zone_colors_hex):
            r, g, b = to_rgb(hx)
            mask_i = zone_map == i
            rgb[mask_i, 0] = int(r * 255)
            rgb[mask_i, 1] = int(g * 255)
            rgb[mask_i, 2] = int(b * 255)
        mask_invalid = zone_map < 0
        rgb[mask_invalid] = [255, 255, 255]
        img_pil = Image.fromarray(rgb)
        img_pil = img_pil.resize((new_cols, new_rows), Image.NEAREST)
        rgb_resized = np.array(img_pil)
    else:
        rgb_resized = np.zeros((rows, cols, 3), dtype=np.uint8)
        from matplotlib.colors import to_rgb
        for i, hx in enumerate(zone_colors_hex):
            r, g, b = to_rgb(hx)
            mask_i = zone_map == i
            rgb_resized[mask_i, 0] = int(r * 255)
            rgb_resized[mask_i, 1] = int(g * 255)
            rgb_resized[mask_i, 2] = int(b * 255)
        mask_invalid = zone_map < 0
        rgb_resized[mask_invalid] = [255, 255, 255]
        new_rows, new_cols = rows, cols

    dpi = 100
    fig = plt.figure(figsize=(max(new_cols / dpi, 8), new_rows / dpi + 0.6), dpi=dpi)
    gs = fig.add_gridspec(2, 1, height_ratios=[new_rows / dpi, 0.5], hspace=0.08)

    ax_img = fig.add_subplot(gs[0])
    ax_img.imshow(rgb_resized, aspect='equal')
    ax_img.set_xticks([])
    ax_img.set_yticks([])

    full_title = 'Deformation Zone Classification'
    subtitle = _make_region_subtitle(meta)
    if subtitle:
        full_title += f'\n{subtitle}'
    ax_img.set_title(full_title, fontsize=11, fontweight='bold', color='#333')

    ax_leg = fig.add_subplot(gs[1])
    ax_leg.set_xlim(0, 1)
    ax_leg.set_ylim(0, 1)
    ax_leg.axis('off')
    n = len(zone_names)
    for i, (name, hx) in enumerate(zip(zone_names, zone_colors_hex)):
        x = i / n + 0.5 / n
        ax_leg.add_patch(plt.Rectangle((x, 0.3), 0.7 / n, 0.4, color=hx,
                                        transform=ax_leg.transAxes, clip_on=False))
        ax_leg.text(x + 0.35 / n, 0.15, name, transform=ax_leg.transAxes,
                    ha='center', va='center', fontsize=7, color='#333',
                    rotation=45)

    fig.tight_layout()
    img = _fig_to_bytes(fig, dpi=dpi)
    with open(output_path, 'wb') as f:
        f.write(img)
    return output_path


def generate_spatial_charts(vel_path, ts_path, gradient_tif, zones, meta, output_dir):
    """Generate spatial raster charts and save to output_dir.

    Returns dict: chart_name -> relative_path.
    """
    out = str(output_dir)
    os.makedirs(out, exist_ok=True)
    charts = {}

    p = os.path.join(out, 'velocity_map.png')
    velocity_map(vel_path, meta, p)
    charts['velocity_map'] = 'analysis/velocity_map.png'

    p = os.path.join(out, 'cumulative_map.png')
    cumulative_map(ts_path, meta, p)
    charts['cumulative_map'] = 'analysis/cumulative_map.png'

    p = os.path.join(out, 'strain_map.png')
    strain_map(gradient_tif, meta, p)
    charts['strain_map'] = 'analysis/strain_map.png'

    p = os.path.join(out, 'zoning_map.png')
    zoning_map(zones, vel_path, meta, p)
    charts['zoning_map'] = 'analysis/zoning_map.png'

    return charts
