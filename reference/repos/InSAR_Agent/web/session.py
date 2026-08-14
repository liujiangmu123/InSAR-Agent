"""Session management for multi-user InSAR Agent web server."""
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Optional

_DATA_DIR = Path(os.environ.get('DATA_DIR', Path(__file__).resolve().parent.parent / 'data'))
_SESSIONS_FILE = _DATA_DIR / 'system' / 'sessions.json'
_USERS_DIR = _DATA_DIR / 'users'


def _ensure_dirs():
    _SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _USERS_DIR.mkdir(parents=True, exist_ok=True)


def _load_sessions() -> dict:
    _ensure_dirs()
    if _SESSIONS_FILE.is_file():
        return json.loads(_SESSIONS_FILE.read_text(encoding='utf-8'))
    return {'sessions': {}}


def _save_sessions(data: dict):
    _ensure_dirs()
    _SESSIONS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def create_user(username: str, password: str | None = None) -> dict:
    username = username.strip().lower()
    if not username:
        raise ValueError('Username required')

    user_dir = _USERS_DIR / username
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / 'projects').mkdir(exist_ok=True)

    config_file = user_dir / 'config.json'
    if not config_file.is_file():
        config_file.write_text(json.dumps({
            'username': username,
            'password_hash': _hash_password(password) if password else None,
            'deepseek_api_key': os.environ.get('DEEPSEEK_API_KEY', ''),
            'deepseek_base_url': os.environ.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com'),
            'deepseek_model': os.environ.get('DEEPSEEK_MODEL', 'deepseek-chat'),
            'asf_credentials': [],
            'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        }, ensure_ascii=False, indent=2), encoding='utf-8')

    return {'username': username, 'created': True}


def login(username: str, password: str | None = None) -> str | None:
    username = username.strip().lower()
    user_config = _USERS_DIR / username / 'config.json'
    if not user_config.is_file():
        return None

    config = json.loads(user_config.read_text(encoding='utf-8'))
    stored_hash = config.get('password_hash')
    if stored_hash and not password:
        return None
    if stored_hash and _hash_password(password) != stored_hash:
        return None

    token = secrets.token_hex(32)
    data = _load_sessions()
    data['sessions'][token] = {
        'username': username,
        'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
    }
    _save_sessions(data)
    return token


def get_user(token: str) -> str | None:
    data = _load_sessions()
    session = data['sessions'].get(token)
    if not session:
        return None
    return session['username']


def logout(token: str):
    data = _load_sessions()
    data['sessions'].pop(token, None)
    _save_sessions(data)


def get_user_config(username: str) -> dict:
    config_file = _USERS_DIR / username / 'config.json'
    if config_file.is_file():
        return json.loads(config_file.read_text(encoding='utf-8'))
    return {}


def update_user_config(username: str, updates: dict):
    config_file = _USERS_DIR / username / 'config.json'
    config = {}
    if config_file.is_file():
        config = json.loads(config_file.read_text(encoding='utf-8'))
    config.update(updates)
    config_file.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')


def get_user_chat_history(username: str) -> list[dict]:
    history_file = _USERS_DIR / username / 'chat_history.json'
    if history_file.is_file():
        return json.loads(history_file.read_text(encoding='utf-8'))
    return []


def save_user_chat_history(username: str, history: list[dict]):
    history_file = _USERS_DIR / username / 'chat_history.json'
    history_file.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding='utf-8')


def get_user_projects_dir(username: str) -> Path:
    return _USERS_DIR / username / 'projects'


def list_users() -> list[str]:
    if not _USERS_DIR.is_dir():
        return []
    return sorted([d.name for d in _USERS_DIR.iterdir() if d.is_dir() and (d / 'config.json').is_file()])
