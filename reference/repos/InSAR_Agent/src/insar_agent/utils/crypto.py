import base64
import hashlib
import json
import os


def _derive_key() -> bytes:
    machine_id = os.environ.get('COMPUTERNAME', '') + os.environ.get('USERNAME', '')
    return hashlib.sha256(machine_id.encode()).digest()


def encrypt(data: dict) -> str:
    key = _derive_key()
    raw = json.dumps(data, ensure_ascii=False).encode('utf-8')
    encrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
    return base64.b64encode(encrypted).decode()


def decrypt(encrypted_str: str) -> dict:
    key = _derive_key()
    encrypted = base64.b64decode(encrypted_str)
    raw = bytes(b ^ key[i % len(key)] for i, b in enumerate(encrypted))
    return json.loads(raw.decode('utf-8'))
