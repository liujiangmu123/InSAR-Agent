"""MintPy integration tool - config generation and processing

Generates smallbaselineApp.cfg from a template, replacing the workspace
path with the actual clipped data directory. The config is saved in a
mintpy/ directory alongside the clip/ directory.

Supports per-project parameter overrides via mintpy_overrides.json
placed in the project root directory.
"""

import json
import os
import re
import subprocess
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable


_TEMPLATE_CONFIG = Path(__file__).parent / 'smallbaselineApp.cfg'

_PLACEHOLDER_BASE = '/workspace/ASF/gamma_clipped'

_PATH_KEYS = [
    'mintpy.load.unwFile',
    'mintpy.load.corFile',
    'mintpy.load.demFile',
    'mintpy.load.incAngleFile',
    'mintpy.load.azAngleFile',
    'mintpy.load.waterMaskFile',
]


@dataclass
class MintPyConfigResult:
    config_path: str
    data_dir: str
    output_dir: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class MintPyRunResult:
    config_path: str
    data_dir: str
    output_dir: str
    success: bool = False
    output_files: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)
    error: str = ''


def generate_mintpy_config(
    data_dir: str,
    template_config: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> MintPyConfigResult:
    """Generate smallbaselineApp.cfg for the clipped HyP3 data.

    Args:
        data_dir: Directory containing clipped HyP3 products (e.g. .../tasks/{task_id}/clip/)
        template_config: Path to custom config template. Defaults to built-in.
        output_dir: Where to write the config file. Defaults to data_dir's parent / 'mintpy'.

    Returns:
        MintPyConfigResult with config_path and output_dir
    """
    warnings = []
    data_path = Path(data_dir).resolve()

    if output_dir:
        out_dir = Path(output_dir).resolve()
    else:
        out_dir = data_path.parent / 'mintpy'
    out_dir.mkdir(parents=True, exist_ok=True)

    template_path = Path(template_config) if template_config else _TEMPLATE_CONFIG
    if not template_path.is_file():
        return MintPyConfigResult(
            config_path='', data_dir=str(data_path), output_dir=str(out_dir),
            warnings=[f'Template config not found: {template_path}'],
        )

    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()

    new_base = str(data_path)

    for key in _PATH_KEYS:
        pattern = re.compile(
            r'(' + re.escape(key) + r'\s*=\s*)' + re.escape(_PLACEHOLDER_BASE),
        )
        if pattern.search(content):
            content = pattern.sub(r'\1' + new_base, content)
        else:
            warnings.append(f'Template missing placeholder for {key}, verify manually.')

    overrides_file = _get_task_dir(str(data_path)) / 'mintpy_overrides.json'
    if overrides_file.is_file():
        try:
            with open(overrides_file, 'r', encoding='utf-8') as f:
                overrides = json.load(f)
            content = _apply_overrides(content, overrides)
        except Exception:
            pass

    config_path = str(out_dir / 'smallbaselineApp.cfg')
    with open(config_path, 'w', encoding='utf-8') as f:
        f.write(content)

    return MintPyConfigResult(
        config_path=config_path,
        data_dir=str(data_path),
        output_dir=str(out_dir),
        warnings=warnings,
    )


def _get_task_dir(data_dir: str) -> Path:
    data_path = Path(data_dir).resolve()
    return data_path.parent  # task directory (parent of clip/)


def save_mintpy_overrides(task_id: str, overrides: dict):
    """Save MintPy parameter overrides for a task.

    The file mintpy_overrides.json is saved in the task directory
    (projects/{task_id}/mintpy_overrides.json).
    It will be auto-detected by generate_mintpy_config.

    Args:
        task_id: Task identifier from submit_insar_jobs
        overrides: Dict of section.key -> value, e.g.
            {'mintpy.network.minCoherence': 0.5, 'mintpy.deramp': 'linear'}
    """
    from ..config import get_task_dir
    root = get_task_dir(task_id)
    root.mkdir(parents=True, exist_ok=True)
    overrides_file = root / 'mintpy_overrides.json'
    with open(overrides_file, 'w', encoding='utf-8') as f:
        json.dump(overrides, f, ensure_ascii=False, indent=2)
    # auto-regenerate config so changes are visible immediately
    clip_dir = root / 'clip'
    if clip_dir.is_dir():
        generate_mintpy_config(str(clip_dir))


def _apply_overrides(content: str, overrides: dict) -> str:
    for key, val in overrides.items():
        pattern = re.compile(
            r'(' + re.escape(key) + r'\s*=\s*)\S+',
            re.MULTILINE,
        )
        content = pattern.sub(r'\1' + str(val), content)
    return content


def run_mintpy(
    data_dir: str,
    template_config: Optional[str] = None,
    output_dir: Optional[str] = None,
    callback: Optional[Callable[[str], None]] = None,
    stop_event=None,
) -> MintPyRunResult:
    """Generate config and run MintPy smallbaselineApp processing.

    Args:
        data_dir: Directory containing clipped HyP3 products
        template_config: Custom config template (uses built-in if None)
        output_dir: Output directory for config and results
        callback: Optional callback for progress updates

    Returns:
        MintPyRunResult with success status and output file paths
    """
    log = callback or (lambda msg: None)

    cfg_result = generate_mintpy_config(data_dir, template_config, output_dir)
    if cfg_result.warnings and not cfg_result.config_path:
        return MintPyRunResult(
            config_path='', data_dir=data_dir, output_dir='',
            warnings=cfg_result.warnings, error='Config generation failed',
        )

    config_path = cfg_result.config_path
    work_dir = os.path.dirname(config_path)
    log(f'[MintPy] Config written: {config_path}')

    import sys
    mintpy_exe = shutil.which('smallbaselineApp.py')
    if mintpy_exe:
        cmd = [mintpy_exe, config_path]
    else:
        cmd = [sys.executable, '-m', 'mintpy.cli.smallbaselineApp', config_path]
        log('[MintPy] Using python -m mintpy.cli.smallbaselineApp')

    import time
    t0 = time.time()

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=work_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        all_lines = []
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            all_lines.append(line)
            log(f'[MintPy] {line}')
            if stop_event and stop_event.is_set():
                log('[MintPy] 收到取消信号，终止进程...')
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                elapsed = time.time() - t0
                return MintPyRunResult(
                    config_path=config_path, data_dir=data_dir,
                    output_dir=cfg_result.output_dir,
                    elapsed_seconds=elapsed,
                    error='Cancelled by user',
                )

        proc.wait()
        elapsed = time.time() - t0

        if proc.returncode != 0:
            _tail = '\n'.join(all_lines[-50:]).strip() or '(no output)'
            return MintPyRunResult(
                config_path=config_path, data_dir=data_dir,
                output_dir=cfg_result.output_dir,
                elapsed_seconds=elapsed,
                error=f'MintPy exit {proc.returncode}:\n{_tail}',
            )

        output_files = []
        for pat in ['velocity.h5', 'timeseries*.h5', 'temporalCoherence.h5',
                     'maskTempCoh.h5', 'avgSpatialCoh.h5', 'geo']:
            for f in Path(work_dir).glob(f'**/{pat}'):
                output_files.append(str(f))

        log(f'[MintPy] Done in {elapsed/60:.0f} min. {len(output_files)} output files.')

        return MintPyRunResult(
            config_path=config_path, data_dir=data_dir,
            output_dir=cfg_result.output_dir,
            success=True, output_files=output_files,
            elapsed_seconds=elapsed,
        )

    except FileNotFoundError:
        return MintPyRunResult(
            config_path=config_path, data_dir=data_dir,
            output_dir=cfg_result.output_dir,
            error='smallbaselineApp.py not found. Install MintPy: pip install mintpy',
        )
    except Exception as e:
        return MintPyRunResult(
            config_path=config_path, data_dir=data_dir,
            output_dir=cfg_result.output_dir,
            error=str(e),
        )


def _find_in_dir(base: Path, *names: str) -> Optional[Path]:
    for name in names:
        p = base / name
        if p.is_file():
            return p
        p = base / 'geo' / name
        if p.is_file():
            return p
    return None


def post_mintpy_h5_to_tif(mintpy_dir: Path, callback=None):
    log = callback or (lambda msg: None)
    conversions = [
        ('velocity.h5',       ['-d', 'velocity',          '-o', 'velocity.tif',       '--of', 'GTiff']),
        ('velocityERA5.h5',   ['-d', 'velocity',          '-o', 'velocityERA5.tif',   '--of', 'GTiff']),
        ('incidenceAngle.h5', ['-d', 'incidenceAngle',    '-o', 'incidenceAngle.tif', '--of', 'GTiff']),
        ('maskTempCoh.h5',     ['--of', 'GTiff']),
        ('temporalCoherence.h5', ['--of', 'GTiff']),
        ('waterMask.h5',       ['--of', 'GTiff']),
    ]
    for src_name, extra_args in conversions:
        src = _find_in_dir(mintpy_dir, src_name)
        if not src:
            log(f'[h5→tif] SKIP: {src_name} not found')
            continue
        dst = src.with_suffix('.tif')
        if dst.is_file():
            log(f'[h5→tif] SKIP: {dst.name} already exists')
            continue
        log(f'[h5→tif] {src.relative_to(mintpy_dir)} → {dst.name}')
        try:
            subprocess.run(
                ['save_gdal.py', str(src)] + extra_args,
                cwd=str(mintpy_dir), capture_output=True, text=True, check=True,
            )
        except subprocess.CalledProcessError as e:
            log(f'[h5→tif] FAIL: {src_name}: {e.stderr.strip()}')
        except FileNotFoundError:
            log('[h5→tif] FAIL: save_gdal.py not found, install mintpy')

    geo_geom = mintpy_dir / 'inputs' / 'geometryGeo.h5'
    geo_dst = mintpy_dir / 'incidenceAngle.tif'
    if geo_geom.is_file() and not geo_dst.is_file():
        log(f'[h5→tif] inputs/geometryGeo.h5 → incidenceAngle.tif')
        try:
            subprocess.run(
                ['save_gdal.py', str(geo_geom), '-d', 'incidenceAngle', '-o', str(geo_dst), '--of', 'GTiff'],
                cwd=str(mintpy_dir), capture_output=True, text=True, check=True,
            )
        except subprocess.CalledProcessError as e:
            log(f'[h5→tif] FAIL geometryGeo.h5: {e.stderr.strip()}')


def _extract_standard_tag(mintpy_dir: Path) -> tuple:
    """Extract (path, frame, date_tag) from MintPy output directory context.

    Returns (path_int, frame_int, 'YYYYMM_YYYYMM') or (None, None, '').
    """
    task_dir = mintpy_dir.parent
    m = re.search(r'Path(\d+).*Frame(\d+)', task_dir.name, re.IGNORECASE)
    if not m:
        return None, None, ''
    path_num = int(m.group(1))
    frame_num = int(m.group(2))

    start_ym = ''
    end_ym = ''
    for ts_pat in ['timeseries_ramp_demErr.h5', 'timeseries_ERA5_ramp_demErr.h5',
                   'timeseries.h5', 'timeseries_ERA5.h5']:
        ts_file = _find_in_dir(mintpy_dir, ts_pat)
        if not ts_file:
            continue
        try:
            import h5py
            with h5py.File(str(ts_file), 'r') as f:
                ds = f.attrs.get('START_DATE')
                de = f.attrs.get('END_DATE')
                if ds is not None:
                    s = ds.decode('utf-8') if isinstance(ds, bytes) else str(ds)
                    start_ym = s.strip("b'").split('T')[0].strip()[:6]
                if de is not None:
                    e = de.decode('utf-8') if isinstance(de, bytes) else str(de)
                    end_ym = e.strip("b'").split('T')[0].strip()[:6]
            break
        except Exception:
            continue

    date_tag = f'{start_ym}_{end_ym}' if start_ym and end_ym else 'nodate'
    return path_num, frame_num, date_tag


def post_mintpy_mask_velocity(mintpy_dir: Path, callback=None):
    """Apply maskTempCoh + waterMask to velocity tifs, output standard-named files.

    Outputs vel_{path}_{frame}_{YYYYMM}_{YYYYMM}.tif (or vel_E_... for ERA5)
    directly — no intermediate vel_mintpy.tif.
    """
    log = callback or (lambda msg: None)
    mask_tif = _find_in_dir(mintpy_dir, 'maskTempCoh.tif', 'geo_maskTempCoh.tif')
    water_tif = _find_in_dir(mintpy_dir, 'waterMask.tif')

    path_num, frame_num, date_tag = _extract_standard_tag(mintpy_dir)
    if path_num is not None:
        tag = f'{path_num}_{frame_num}_{date_tag}'
    else:
        tag = mintpy_dir.name

    for vel_name, era5 in [('velocity.tif', False), ('velocityERA5.tif', True)]:
        velocity_tif = _find_in_dir(mintpy_dir, vel_name)
        if not velocity_tif:
            continue
        prefix = 'vel_E' if era5 else 'vel'
        output = mintpy_dir / f'{prefix}_{tag}.tif'

        old = mintpy_dir / f'vel{"_ERA5" if era5 else ""}_mintpy.tif'
        if old.is_file():
            old.unlink()

        if output.is_file():
            log(f'[mask_velocity] SKIP: {output.name} already exists')
            continue

        log(f'[mask_velocity] masking {velocity_tif.name}...')
        try:
            import numpy as np
            import rasterio

            with rasterio.open(str(velocity_tif)) as src:
                velocity = np.array(src.read(1), dtype=np.float32)
                profile = src.profile.copy()
                nodata = src.nodata if src.nodata is not None else np.nan

            mask = np.zeros(velocity.shape, dtype=bool)
            if mask_tif:
                with rasterio.open(str(mask_tif)) as src_m:
                    m = src_m.read(1)
                    mask |= (m == 0)
            if water_tif:
                with rasterio.open(str(water_tif)) as src_w:
                    w = src_w.read(1)
                    mask |= (w == 0)

            velocity[mask] = nodata
            profile.update(dtype='float32', nodata=nodata)

            with rasterio.open(str(output), 'w', **profile) as dst:
                dst.write(velocity, 1)

            log(f'[mask_velocity] → {output.name}')
        except Exception as e:
            log(f'[mask_velocity] FAIL: {e}')


def post_mintpy_standardize_names(mintpy_dir: Path, callback=None):
    """Generate standard-named copies of non-velocity output files.

    Velocity files are already standard-named by post_mintpy_mask_velocity.
    Handles: cum_rd h5, water tif, temporal coherence tif.
    """
    log = callback or (lambda msg: None)

    path_num, frame_num, date_tag = _extract_standard_tag(mintpy_dir)
    if path_num is None:
        log('[standardize] SKIP: cannot parse path/frame from task dir name')
        return
    tag = f'{path_num}_{frame_num}_{date_tag}'

    tif_mappings = [
        ('waterMask.tif',       f'water_{tag}.tif'),
        ('temporalCoherence.tif', f'tc_{tag}.tif'),
    ]
    for src_name, std_name in tif_mappings:
        src = _find_in_dir(mintpy_dir, src_name)
        if not src:
            log(f'[standardize] SKIP: {src_name} not found')
            continue
        dst = mintpy_dir / std_name
        if dst.is_file():
            log(f'[standardize] SKIP: {std_name} already exists')
            continue
        src.rename(dst)
        log(f'[standardize] {src_name} → {std_name}')

    h5_mappings = [
        (['timeseries_ramp_demErr.h5', 'timeseriesResidual_ramp_demErr.h5'],
         f'cum_rd_{tag}.h5'),
        (['timeseries_ERA5_ramp_demErr.h5'],
         f'cum_rdE_{tag}.h5'),
    ]
    for src_names, std_name in h5_mappings:
        src = None
        for name in src_names:
            src = _find_in_dir(mintpy_dir, name)
            if src:
                break
        if not src:
            log(f'[standardize] SKIP: {"|".join(src_names)} not found')
            continue
        dst = mintpy_dir / std_name
        if dst.is_file():
            log(f'[standardize] SKIP: {std_name} already exists')
            continue
        shutil.copy2(str(src), str(dst))
        log(f'[standardize] {src.name} → {std_name}')
