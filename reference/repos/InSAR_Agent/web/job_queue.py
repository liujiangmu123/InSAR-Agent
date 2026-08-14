"""Job queue manager for InSAR Agent - prevents concurrent resource-heavy operations."""
import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path

_DATA_DIR = Path(os.environ.get('DATA_DIR', Path(__file__).resolve().parent.parent / 'data'))
_QUEUE_FILE = _DATA_DIR / 'system' / 'job_queue.json'
_QUEUE_LOCK = threading.Lock()
_MAX_WORKERS = int(os.environ.get('MAX_MINT_PY_WORKERS', 2))


def _ensure_dirs():
    _QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)


def _load_queue() -> dict:
    _ensure_dirs()
    if _QUEUE_FILE.is_file():
        return json.loads(_QUEUE_FILE.read_text(encoding='utf-8'))
    return {'queue': [], 'active_count': 0}


def _save_queue(data: dict):
    _ensure_dirs()
    _QUEUE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def submit_job(username: str, job_type: str, params: dict, description: str = '') -> str:
    """Submit a job to the queue. Returns job_id."""
    job_id = uuid.uuid4().hex[:12]

    with _QUEUE_LOCK:
        data = _load_queue()
        job = {
            'id': job_id,
            'username': username,
            'type': job_type,
            'description': description,
            'params': params,
            'status': 'pending',
            'progress': '',
            'result': None,
            'error': None,
            'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'started_at': None,
            'completed_at': None,
        }
        data['queue'].append(job)
        _save_queue(data)

    _try_process_queue()
    return job_id


def get_job(job_id: str) -> dict | None:
    with _QUEUE_LOCK:
        data = _load_queue()
        for job in data['queue']:
            if job['id'] == job_id:
                return dict(job)
    return None


def get_user_jobs(username: str) -> list[dict]:
    with _QUEUE_LOCK:
        data = _load_queue()
        return [dict(j) for j in data['queue'] if j['username'] == username]


def get_all_jobs() -> list[dict]:
    with _QUEUE_LOCK:
        data = _load_queue()
        return [dict(j) for j in data['queue']]


def cancel_job(job_id: str, username: str | None = None) -> bool:
    with _QUEUE_LOCK:
        data = _load_queue()
        for job in data['queue']:
            if job['id'] == job_id:
                if username and job['username'] != username:
                    return False
                if job['status'] == 'pending':
                    job['status'] = 'cancelled'
                    job['completed_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
                    _save_queue(data)
                    return True
                elif job['status'] == 'running':
                    job['status'] = 'cancelling'
                    _save_queue(data)
                    return True
    return False


def _try_process_queue():
    """Start processing pending jobs if capacity available."""
    with _QUEUE_LOCK:
        data = _load_queue()
        running_count = sum(1 for j in data['queue'] if j['status'] == 'running')
        if running_count >= _MAX_WORKERS:
            return
        for job in data['queue']:
            if job['status'] == 'pending':
                job['status'] = 'running'
                job['started_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
                _save_queue(data)
                thread = threading.Thread(target=_run_job, args=(job['id'],), daemon=True)
                thread.start()
                break


def _run_job(job_id: str):
    """Execute a job in a background thread."""
    job = get_job(job_id)
    if not job:
        return

    try:
        job_type = job['type']
        params = job['params']

        if job_type == 'mintpy':
            _run_mintpy_job(job_id, params)
        elif job_type == 'auto_import':
            _run_auto_import_job(job_id, params)
        else:
            _update_job(job_id, status='failed', error=f'Unknown job type: {job_type}')

    except Exception as e:
        _update_job(job_id, status='failed', error=str(e))
    finally:
        _try_process_queue()


def _run_mintpy_job(job_id: str, params: dict):
    work_dir = params.get('work_dir', '')
    config_path = params.get('config_path', '')

    _update_job(job_id, progress='Starting smallbaselineApp.py ...')

    cmd = ['smallbaselineApp.py', config_path] if config_path else ['smallbaselineApp.py']

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=work_dir or None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        output_lines = []
        for line in proc.stdout:
            line = line.rstrip()
            output_lines.append(line)
            if len(output_lines) > 50:
                output_lines = output_lines[-30:]
            if 'RUNNING' in line or 'time used' in line or 'number of' in line.lower():
                _update_job(job_id, progress=line[:200])

        proc.wait()
        if proc.returncode == 0:
            _update_job(job_id, status='completed', result={'output': output_lines[-20:]})
        else:
            _update_job(job_id, status='failed', error='\n'.join(output_lines[-10:]))

    except FileNotFoundError:
        _update_job(job_id, status='failed', error='smallbaselineApp.py not found. Is MintPy installed?')


def _run_auto_import_job(job_id: str, params: dict):
    from insar_agent.tools.catalog import auto_import as _catalog_auto_import

    base_dir = params.get('dir', '')
    _update_job(job_id, progress=f'Scanning {base_dir} ...')

    try:
        results = _catalog_auto_import(base_dir, dry_run=False, fast=True)
        if isinstance(results, list) and results and 'error' in results[0]:
            _update_job(job_id, status='failed', error=results[0]['error'])
        else:
            _update_job(job_id, status='completed', result={'count': len(results), 'entries': results})
    except Exception as e:
        _update_job(job_id, status='failed', error=str(e))


def _update_job(job_id: str, status: str = None, progress: str = None, result=None, error: str = None):
    with _QUEUE_LOCK:
        data = _load_queue()
        for job in data['queue']:
            if job['id'] == job_id:
                if status:
                    job['status'] = status
                    if status in ('completed', 'failed', 'cancelled'):
                        job['completed_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
                if progress is not None:
                    job['progress'] = progress
                if result is not None:
                    job['result'] = result
                if error is not None:
                    job['error'] = error
                _save_queue(data)
                return
