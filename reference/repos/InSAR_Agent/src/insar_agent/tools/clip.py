"""Clip HyP3 GAMMA InSAR products to common overlap for MintPy"""

import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ClipResult:
    data_dir: str
    output_dir: str
    total_files: int = 0
    clipped_count: int = 0
    skipped_count: int = 0
    txt_copied: int = 0
    excluded_pairs: int = 0
    overlap: Optional[list[float]] = None
    elapsed_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)
    mintpy_config: str = ''


def _clip_one_file(f, overlap, out_dir, data_dir):
    from osgeo import gdal
    rel = f.relative_to(data_dir)
    out_file = out_dir / rel.parent / f'{f.stem}_clipped{f.suffix}'
    out_file.parent.mkdir(parents=True, exist_ok=True)
    if out_file.exists():
        return f'skipped', str(rel)
    gdal.Translate(destName=str(out_file), srcDS=str(f), projWin=overlap)
    return f'clipped', str(rel)


def clip_to_common_overlap(
    data_dir: str,
    output_dir: Optional[str] = None,
    exclude_dates: Optional[list[str]] = None,
    max_workers: Optional[int] = None,
    stop_event=None,
) -> ClipResult:
    """Clip all HyP3 GAMMA InSAR products to common overlap area.

    Processes: _water_mask.tif, _corr.tif, _unw_phase.tif, _dem.tif,
    _lv_theta.tif, _lv_phi.tif, _conncomp.tif

    Also copies .txt metadata files required by MintPy.

    Args:
        data_dir: Directory containing unzipped HyP3 products
        output_dir: Output directory (defaults to data_dir + '_clipped')
        exclude_dates: List of dates to exclude (pairs containing these dates skipped)
        max_workers: Number of parallel workers (defaults to CPU count)

    Returns:
        ClipResult with statistics and mintpy_config snippet
    """
    from osgeo import gdal

    warnings = []
    if exclude_dates is None:
        exclude_dates = []

    data_path = Path(data_dir)
    out_path = Path(output_dir) if output_dir else Path(str(data_dir) + '_clipped')
    out_path.mkdir(parents=True, exist_ok=True)

    if max_workers is None:
        max_workers = os.cpu_count() or 4

    def _is_excluded(pair_dir_name: str) -> bool:
        return any(d in pair_dir_name for d in exclude_dates)

    dem_files = sorted(data_path.rglob('*_dem.tif'))
    included_dem = [f for f in dem_files if not _is_excluded(f.parent.name)]

    if not included_dem:
        return ClipResult(
            data_dir=str(data_path), output_dir=str(out_path),
            warnings=['No DEM files found for overlap computation'],
        )

    corners = [gdal.Info(str(f), format='json')['cornerCoordinates'] for f in included_dem]
    ulx = max(c['upperLeft'][0] for c in corners)
    uly = min(c['upperLeft'][1] for c in corners)
    lrx = min(c['lowerRight'][0] for c in corners)
    lry = max(c['lowerRight'][1] for c in corners)
    overlap = [ulx, uly, lrx, lry]

    extensions = [
        '_water_mask.tif', '_corr.tif', '_unw_phase.tif',
        '_dem.tif', '_lv_theta.tif', '_lv_phi.tif', '_conncomp.tif',
    ]

    tasks = []
    for ext in extensions:
        for f in data_path.rglob(f'*{ext}'):
            if not _is_excluded(f.parent.name):
                tasks.append(f)

    clipped = skipped = 0
    t0 = time.time()

    if tasks:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_clip_one_file, f, overlap, out_path, data_path): f for f in tasks}
            for future in as_completed(futures):
                if stop_event and stop_event.is_set():
                    pool.shutdown(wait=False, cancel_futures=True)
                    warnings.append('Clip cancelled by user')
                    break
                status, _ = future.result()
                if status == 'clipped':
                    clipped += 1
                else:
                    skipped += 1

    txt_copied = 0
    for dirpath in data_path.iterdir():
        if not dirpath.is_dir():
            continue
        if _is_excluded(dirpath.name):
            continue
        for txt_file in dirpath.glob('*.txt'):
            rel = txt_file.relative_to(data_path)
            dst = out_path / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.copy2(txt_file, dst)
                txt_copied += 1

    elapsed = time.time() - t0
    mintpy_config = (
        f'mintpy.load.processor    = hyp3\n'
        f'mintpy.load.unwFile      = {out_path}/*/*_unw_phase_clipped.tif\n'
        f'mintpy.load.corFile      = {out_path}/*/*_corr_clipped.tif\n'
        f'mintpy.load.connCompFile = {out_path}/*/*_conncomp_clipped.tif\n'
        f'mintpy.load.demFile      = {out_path}/*/*_dem_clipped.tif\n'
        f'mintpy.load.incAngleFile = {out_path}/*/*_lv_theta_clipped.tif\n'
        f'mintpy.load.azAngleFile  = {out_path}/*/*_lv_phi_clipped.tif\n'
        f'mintpy.load.waterMaskFile= {out_path}/*/*_water_mask_clipped.tif\n'
    )

    return ClipResult(
        data_dir=str(data_path),
        output_dir=str(out_path),
        total_files=len(tasks),
        clipped_count=clipped,
        skipped_count=skipped,
        txt_copied=txt_copied,
        excluded_pairs=len(dem_files) - len(included_dem),
        overlap=overlap,
        elapsed_seconds=elapsed,
        warnings=warnings,
        mintpy_config=mintpy_config,
    )
