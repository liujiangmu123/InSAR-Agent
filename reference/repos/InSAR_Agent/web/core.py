import json
import os
import secrets
from pathlib import Path

from fastapi import Request, Response

DATA_DIR = Path(os.environ.get('DATA_DIR', Path(__file__).resolve().parent.parent / 'data'))
SESSIONS_DIR = DATA_DIR / 'sessions'
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
PROJECTS_DIR = DATA_DIR / 'projects'
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


def _load_path_map() -> dict[str, str]:
    mapping = {}
    raw = os.environ.get('WIN_PATH_MAP', '')
    if not raw:
        return mapping
    for pair in raw.split(';'):
        pair = pair.strip()
        if '=' not in pair:
            continue
        key, val = pair.split('=', 1)
        mapping[key.strip().replace('\\', '/').rstrip('/')] = val.strip().rstrip('/')
    return mapping


_PATH_MAP = _load_path_map()


def translate_path(path: str) -> str:
    if not path or not _PATH_MAP:
        return path
    normalized = path.replace('\\', '/').rstrip('/')
    for win_prefix, docker_prefix in _PATH_MAP.items():
        if normalized.lower().startswith(win_prefix.lower()):
            return docker_prefix + normalized[len(win_prefix):]
    return path


def translate_files(files: dict[str, str]) -> dict[str, str]:
    return {k: translate_path(v) for k, v in files.items()}


def get_or_create_sid(request: Request) -> str:
    sid = request.cookies.get('insar_sid')
    if not sid:
        sid = secrets.token_hex(16)
    return sid


def load_history(sid: str) -> list[dict]:
    f = SESSIONS_DIR / f'{sid}.json'
    if f.is_file():
        data = json.loads(f.read_text(encoding='utf-8'))
        if isinstance(data, dict) and 'messages' in data:
            return data['messages']
        if isinstance(data, list):
            return data
    return []


def load_session_title(sid: str) -> str | None:
    f = SESSIONS_DIR / f'{sid}.json'
    if f.is_file():
        data = json.loads(f.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            return data.get('_title')
    return None


def save_history(sid: str, history: list[dict]):
    f = SESSIONS_DIR / f'{sid}.json'
    existing_title = load_session_title(sid)
    payload = {'_title': existing_title, 'messages': history}
    f.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def load_credentials(sid: str) -> dict:
    f = SESSIONS_DIR / f'{sid}_creds.json'
    if f.is_file():
        return json.loads(f.read_text(encoding='utf-8'))
    return {}


def save_credentials(sid: str, creds: dict):
    f = SESSIONS_DIR / f'{sid}_creds.json'
    f.write_text(json.dumps(creds, ensure_ascii=False, indent=2), encoding='utf-8')


def sse_event(event: str, data: dict) -> str:
    return f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'
