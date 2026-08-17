"""安全响应头与按页 CSP。

覆盖:
  1. 全响应基础头(nosniff / referrer / frame DENY);
  2. /api/* 的 no-store;产品根路径 / 是 JSON,不带 CSP;
  3. _page_csp / scan_ui_csp 形态判定(事件属性页降级 unsafe-inline;哈希口径 CRLF→LF)。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from insar_agent.api.app import _page_csp, create_app, scan_ui_csp


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


def test_api_root_is_json_not_html(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["health"] == "/api/health"
    assert "Content-Security-Policy" not in r.headers


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
