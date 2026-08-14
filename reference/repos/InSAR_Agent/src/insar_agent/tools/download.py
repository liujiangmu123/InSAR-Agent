"""Parallel downloader for ASF HyP3 products with ASF authentication"""

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

import requests


@dataclass
class DownloadResult:
    save_dir: str
    total_files: int
    success_count: int
    failed_count: int
    failed_files: list[str] = field(default_factory=list)
    total_bytes: int = 0
    elapsed_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)


def _download_one(url: str, save_dir: str, session: requests.Session) -> tuple[str, bool, int]:
    from urllib.parse import urlparse
    fname = os.path.basename(urlparse(url).path) or url.rstrip('/').split('/')[-1]
    fpath = os.path.join(save_dir, fname)

    if os.path.exists(fpath):
        try:
            size = os.path.getsize(fpath)
            return fname, True, size
        except Exception:
            pass

    part_file = fpath + '.part'
    try:
        resp = session.get(url, stream=True, timeout=(30, 3600))
        resp.raise_for_status()

        downloaded = 0
        with open(part_file, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                downloaded += len(chunk)

        os.replace(part_file, fpath)
        return fname, True, downloaded
    except Exception:
        try:
            os.remove(part_file)
        except Exception:
            pass
        return fname, False, 0


def download_products(
    url_file: Optional[str] = None,
    urls: Optional[list[str]] = None,
    save_dir: str = '',
    username: str = '',
    password: str = '',
    max_workers: int = 4,
    stop_event=None,
) -> DownloadResult:
    """Download HyP3 products from ASF with parallel workers.

    Args:
        url_file: Path to file with download URLs (one per line)
        urls: Alternatively, a list of URLs directly
        save_dir: Directory to save files
        username: ASF username (or via HYP3_USERNAME env var)
        password: ASF password (or via HYP3_PASSWORD env var)
        max_workers: Number of parallel download workers

    Returns:
        DownloadResult with counts and file sizes
    """
    warnings = []

    if not username:
        username = os.environ.get('HYP3_USERNAME', '')
    if not password:
        password = os.environ.get('HYP3_PASSWORD', '')

    if not username or not password:
        return DownloadResult(
            save_dir=save_dir, total_files=0, success_count=0, failed_count=0,
            warnings=['ASF credentials required. Set username/password or HYP3_USERNAME/PASSWORD env vars.'],
        )

    if urls is None:
        urls = []
    if url_file and os.path.isfile(url_file):
        with open(url_file, 'r', encoding='utf-8') as f:
            urls = [line.strip() for line in f if line.strip()]

    if not urls:
        return DownloadResult(
            save_dir=save_dir, total_files=0, success_count=0, failed_count=0,
            warnings=['No URLs to download. Provide url_file or urls.'],
        )

    os.makedirs(save_dir, exist_ok=True)

    from asf_search import ASFSession
    base = ASFSession()
    base.auth_with_creds(username, password)

    def _make_session():
        s = requests.Session()
        s.cookies.update(base.cookies)
        return s

    ok = fail = 0
    total_bytes = 0
    failed_files = []
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_download_one, url, save_dir, _make_session()): url
            for url in urls
        }
        for future in as_completed(futures):
            if stop_event and stop_event.is_set():
                pool.shutdown(wait=False, cancel_futures=True)
                warnings.append('Download cancelled by user')
                break
            try:
                fname, success, size = future.result()
                if success:
                    ok += 1
                    total_bytes += size
                else:
                    fail += 1
                    failed_files.append(fname)
            except Exception:
                fail += 1

    elapsed = time.time() - t0
    return DownloadResult(
        save_dir=save_dir,
        total_files=len(urls),
        success_count=ok,
        failed_count=fail,
        failed_files=failed_files,
        total_bytes=total_bytes,
        elapsed_seconds=elapsed,
        warnings=warnings,
    )
