"""安全响应头与按页 CSP(sec-r2 遗留接力,2026-08-13)。

覆盖三层语义:
  1. 全响应基础头(nosniff / referrer / frame DENY);
  2. /api/* 的 no-store 与「HTML 才有 CSP」的互斥;
  3. 按页 CSP 的形态判定:index.html(无事件属性)走 sha256 哈希白名单,
     哈希必须与落盘文件按 CRLF→LF 归一后的内联脚本正文一致(浏览器口径);
     含 onclick= 属性的历史快照页降级 'unsafe-inline'。
"""
from __future__ import annotations

import base64
import hashlib
import re

import pytest
from fastapi.testclient import TestClient

from insar_agent.api.app import (PROTOTYPE_DIR, _page_csp, create_app,
                                 scan_ui_csp)


@pytest.fixture()
def client(tmp_path):
    app = create_app(home=tmp_path / "home")
    with TestClient(app) as c:
        yield c


def test_api_responses_have_base_headers_and_no_store(client):
    r = client.get("/api/status")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Cache-Control"] == "no-store"
    # CSP 只属于 HTML 文档;JSON 响应不背这个头
    assert "Content-Security-Policy" not in r.headers


def test_index_csp_uses_sha256_hash_matching_disk(client):
    if not (PROTOTYPE_DIR / "index.html").exists():
        pytest.skip("无 UI 部署形态")
    r = client.get("/")
    csp = r.headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    # index.html 有 1 个内联 <script> 且无事件属性:必须走哈希白名单,不得放行 unsafe-inline
    m = re.search(r"script-src ([^;]+)", csp)
    assert m, csp
    assert "'unsafe-inline'" not in m.group(1)
    body = (PROTOTYPE_DIR / "index.html").read_bytes()
    body = body.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    script = re.search(rb"<script\b(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>", body,
                       re.IGNORECASE | re.DOTALL)
    if script:
        want = "'sha256-" + base64.b64encode(
            hashlib.sha256(script.group(1)).digest()).decode() + "'"
        assert want in m.group(1), f"CSP 哈希与落盘 index.html 不一致:{csp}"


def test_event_handler_page_degrades_to_unsafe_inline():
    raw = b"<html><body><button onclick=\"go()\">x</button></body></html>"
    csp = _page_csp(raw)
    assert "script-src 'self' 'unsafe-inline'" in csp


def test_crlf_and_lf_yield_same_hash():
    lf = b"<html><script>alert(1)\n</script></html>"
    crlf = lf.replace(b"\n", b"\r\n")
    assert _page_csp(lf) == _page_csp(crlf)


def test_scan_registers_directory_index_alias(tmp_path):
    (tmp_path / "index.html").write_bytes(b"<html><script>1</script></html>")
    table = scan_ui_csp(tmp_path)
    assert "/index.html" in table and "/" in table
    assert table["/"] == table["/index.html"]


def test_missing_ui_dir_returns_empty_table(tmp_path):
    assert scan_ui_csp(tmp_path / "nope") == {}
