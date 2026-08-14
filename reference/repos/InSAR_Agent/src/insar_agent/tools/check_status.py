"""HyP3 job status check tool"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

from hyp3_sdk import HyP3


@dataclass
class JobStatusResult:
    total: int = 0
    succeeded: int = 0
    running: int = 0
    pending: int = 0
    failed: int = 0
    all_done: bool = False
    job_ids: list[str] = field(default_factory=list)
    url_file: str = ''
    project_label: str = ''
    failed_jobs: list[str] = field(default_factory=list)
    download_urls: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def check_job_status(
    job_file: str,
    output_dir: Optional[str] = None,
    max_workers: int = 10,
) -> JobStatusResult:
    """Query job statuses from HyP3 and export download URLs when all done.

    Args:
        job_file: Path to .txt file containing job IDs (one per line)
        output_dir: Directory for exported _urls.txt (defaults to job_file dir)
        max_workers: Number of parallel API calls

    Returns:
        JobStatusResult with status counts and download URLs
    """
    warnings = []

    if not os.path.isfile(job_file):
        return JobStatusResult(warnings=[f'Job file not found: {job_file}'])

    with open(job_file, 'r', encoding='utf-8') as f:
        job_ids = [line.strip() for line in f if line.strip()]

    if not job_ids:
        return JobStatusResult(warnings=[f'Job file is empty: {job_file}'])

    project_label = os.path.basename(job_file).replace('.txt', '')

    meta_file = job_file + '.meta'
    if os.path.isfile(meta_file):
        with open(meta_file, 'r', encoding='utf-8') as f:
            saved_user = f.readline().strip()
            saved_pwd = f.readline().strip()
        try:
            hyp3 = HyP3(username=saved_user, password=saved_pwd)
        except Exception as e:
            return JobStatusResult(warnings=[f'Login failed: {e}'])
    else:
        accounts = _load_accounts_from_env()
        if not accounts:
            return JobStatusResult(warnings=['No accounts configured. Set HYP3_USERNAME/PASSWORD env vars.'])
        hyp3 = None
        for user, pwd in accounts:
            try:
                h = HyP3(username=user, password=pwd)
                h.check_credits()
                hyp3 = h
                break
            except Exception:
                continue
        if hyp3 is None:
            return JobStatusResult(warnings=['Login failed for all accounts'])

    jobs = []
    failed_jobs = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(hyp3.get_job_by_id, jid): jid for jid in job_ids}
        for future in as_completed(future_map):
            try:
                jobs.append(future.result())
            except Exception:
                failed_jobs.append(future_map[future])

    succeeded = [j for j in jobs if j.succeeded()]
    running = [j for j in jobs if j.running()]
    pending = [j for j in jobs if j.pending()]
    failed = [j for j in jobs if j.failed()]

    all_done = len(running) == 0 and len(pending) == 0

    url_file = ''
    download_urls = []
    if all_done and len(succeeded) > 0:
        out_dir = output_dir or os.path.dirname(job_file)
        url_file = job_file.replace('.txt', '_urls.txt')
        for j in succeeded:
            if j.files:
                for file_obj in j.files:
                    download_urls.append(file_obj['url'])
        with open(url_file, 'w', encoding='utf-8') as f:
            for url in download_urls:
                f.write(url + '\n')

    return JobStatusResult(
        total=len(job_ids),
        succeeded=len(succeeded),
        running=len(running),
        pending=len(pending),
        failed=len(failed),
        all_done=all_done,
        job_ids=job_ids,
        url_file=url_file,
        project_label=project_label,
        failed_jobs=failed_jobs,
        download_urls=download_urls,
        warnings=warnings,
    )


def _load_accounts_from_env() -> list[tuple[str, str]]:
    accounts = []
    u0 = os.environ.get('HYP3_USERNAME')
    p0 = os.environ.get('HYP3_PASSWORD')
    if u0 and p0:
        accounts.append((u0, p0))
    for i in range(1, 10):
        u = os.environ.get(f'HYP3_USERNAME_{i}')
        p = os.environ.get(f'HYP3_PASSWORD_{i}')
        if u and p:
            accounts.append((u, p))
    return accounts
