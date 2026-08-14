import json
import os
from pathlib import Path
from .utils.crypto import encrypt, decrypt

_APP_ROOT = Path(__file__).parent.parent
_CONFIG_DIR = _APP_ROOT / 'config'
_DATA_DIR = _APP_ROOT / 'data'
_PROJECTS_DIR = _APP_ROOT / 'projects'

SETTINGS_FILE = _CONFIG_DIR / 'settings.json'
ACCOUNTS_FILE = _CONFIG_DIR / 'accounts.enc'

DEFAULT_SETTINGS = {
    'python_path': '',
    'last_project': '',
    'deepseek_api_key': '',
    'deepseek_base_url': 'https://api.deepseek.com',
    'deepseek_model': 'deepseek-chat',
    'max_download_jobs': 4,
    'max_unzip_jobs': 4,
    'delete_source_zip': True,
}

# InSAR defaults
DEFAULT_INSAR_OPTS = {
    'looks': '20x4',
    'include_inc_map': True,
    'include_look_vectors': True,
    'include_dem': True,
    'include_displacement_maps': True,
    'include_wrapped_phase': True,
    'apply_water_mask': True,
}


def ensure_dirs():
    for d in [_CONFIG_DIR, _DATA_DIR, _PROJECTS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


ensure_dirs()


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                return {**DEFAULT_SETTINGS, **json.load(f)}
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict):
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def load_accounts() -> list[dict]:
    if ACCOUNTS_FILE.exists():
        try:
            raw = ACCOUNTS_FILE.read_bytes().decode('utf-8')
            return decrypt(raw)['accounts']
        except Exception:
            pass
    return []


def save_accounts(accounts: list[dict]):
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = {'accounts': accounts}
    ACCOUNTS_FILE.write_bytes(encrypt(data).encode('utf-8'))


def get_accounts_as_env_pairs(accounts: list[dict]) -> dict[str, str]:
    env = {}
    for i, acc in enumerate(accounts):
        if i == 0:
            env['HYP3_USERNAME'] = acc['username']
            env['HYP3_PASSWORD'] = acc['password']
        else:
            env[f'HYP3_USERNAME_{i}'] = acc['username']
            env[f'HYP3_PASSWORD_{i}'] = acc['password']
    return env


def get_project_dir(project_name: str) -> Path:
    return _PROJECTS_DIR / project_name


def get_output_dir(project_name: str) -> Path:
    return _PROJECTS_DIR / project_name / 'output'


def get_task_dir(task_id: str) -> Path:
    return _PROJECTS_DIR / task_id
