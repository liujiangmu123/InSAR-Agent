"""纯文本预览:小 utf-8 yaml 原样返回;超 32KiB 截断。不执行 YAML。"""

from __future__ import annotations

from pathlib import Path

from insar_agent.preview.dispatch import preview_file
from insar_agent.preview.text_view import _MAX_BYTES

YAML_BODY = "method: mintpy_sbas\nlooks: 4\n"


def test_small_utf8_yaml(tmp_path: Path):
    p = tmp_path / "params.yaml"
    p.write_bytes(YAML_BODY.encode("utf-8"))
    r = preview_file(p)
    assert r.kind == "text"
    assert r.truncated is False
    assert r.note is None
    assert r.payload["text"] == YAML_BODY
    assert r.payload["encoding"] == "utf-8"
    assert r.payload["n_bytes"] == len(YAML_BODY.encode("utf-8"))


def test_large_file_truncated(tmp_path: Path):
    p = tmp_path / "run.log"
    p.write_bytes(b"A" * (_MAX_BYTES + 200))
    r = preview_file(p)
    assert r.kind == "text"
    assert r.truncated is True
    assert r.note is not None
    assert r.payload["text"] == "A" * _MAX_BYTES
    assert r.payload["n_bytes"] == _MAX_BYTES
    assert r.payload["n_bytes_total"] == _MAX_BYTES + 200
    assert r.payload["encoding"] == "utf-8"
    assert len(r.payload["text"].encode("utf-8")) == _MAX_BYTES
