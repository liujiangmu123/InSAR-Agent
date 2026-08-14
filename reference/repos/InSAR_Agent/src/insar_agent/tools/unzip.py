"""Unzip tool - parallel extraction of downloaded InSAR products"""

import os
import shutil
import zipfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class UnzipResult:
    source_dir: str
    output_dir: str
    total_files: int
    success_count: int
    failed_count: int
    skipped_count: int
    failed_files: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)


def _unzip_one(zip_path: str, out_dir: str, force: bool = True) -> tuple[str, bool, str]:
    fname = os.path.basename(zip_path)
    target = os.path.join(out_dir, os.path.splitext(fname)[0])

    if os.path.isdir(target) and not force:
        return fname, True, 'skipped'

    try:
        os.makedirs(target, exist_ok=True)
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(target)
        contents = os.listdir(target)
        if len(contents) == 1:
            inner = os.path.join(target, contents[0])
            if os.path.isdir(inner):
                for item in os.listdir(inner):
                    shutil.move(os.path.join(inner, item), os.path.join(target, item))
                os.rmdir(inner)
        return fname, True, 'ok'
    except Exception as e:
        return fname, False, str(e)


def unzip_products(
    source_dir: str,
    output_dir: Optional[str] = None,
    max_workers: int = 4,
    force: bool = True,
    delete_source: bool = False,
    stop_event=None,
) -> UnzipResult:
    """Parallel unzip all .zip files in source_dir.

    Args:
        source_dir: Directory containing .zip files
        output_dir: Output directory (defaults to source_dir)
        max_workers: Number of parallel workers
        force: Overwrite existing extracted directories
        delete_source: Delete zip files after successful extraction

    Returns:
        UnzipResult with counts and status
    """
    warnings = []
    source = Path(source_dir)
    if output_dir:
        out = Path(output_dir)
    else:
        out = source

    out.mkdir(parents=True, exist_ok=True)

    zip_files = sorted(source.glob('*.zip'))
    if not zip_files:
        return UnzipResult(
            source_dir=str(source), output_dir=str(out),
            total_files=0, success_count=0, failed_count=0, skipped_count=0,
            warnings=['No .zip files found in source directory'],
        )

    t0 = time.time()
    ok = fail = skip = 0
    failed_files = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_unzip_one, str(z), str(out), force): z
            for z in zip_files
        }
        for future in as_completed(futures):
            if stop_event and stop_event.is_set():
                pool.shutdown(wait=False, cancel_futures=True)
                warnings.append('Unzip cancelled by user')
                break
            fname, success, status = future.result()
            if status == 'skipped':
                skip += 1
            elif success:
                ok += 1
                if delete_source:
                    try:
                        os.remove(futures[future])
                    except Exception:
                        pass
            else:
                fail += 1
                failed_files.append(fname)

    elapsed = time.time() - t0
    return UnzipResult(
        source_dir=str(source),
        output_dir=str(out),
        total_files=len(zip_files),
        success_count=ok,
        failed_count=fail,
        skipped_count=skip,
        failed_files=failed_files,
        elapsed_seconds=elapsed,
        warnings=warnings,
    )
