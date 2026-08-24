"""产物预览 HTTP 契约与安全边界(GET /api/preview + /api/preview/image)。

覆盖:
  - 工作区相对路径 export/a.csv(不要求出现在产物列表)→ 200,ok=true;
    有 table.py 时 kind=table,处理器未加载则 kind=unsupported,绝不 500;
  - path=../evil.csv → 404,响应体不泄露 Windows 盘符绝对路径;
  - 空 path → 400;
  - 错会话 / 他人 run → 404;
  - /api/preview/image 对无 PNG 的 csv → 404;
  - qa.json → 200(有 json_view.py 时 kind=json,否则 unsupported)。
不依赖 openpyxl。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from insar_agent.api.app import create_app
from insar_agent.core.db import Database
from insar_agent.core.store import Store

RUN_ID = "20260817T000000-prevtest"
OTHER_RUN = "20260817T000001-other"

_PREVIEW_DIR = Path(__file__).resolve().parents[1] / "src" / "insar_agent" / "preview"
_WIN_DRIVE = re.compile(r"[A-Za-z]:[\\/]")


def _has_handler(name: str) -> bool:
    return (_PREVIEW_DIR / name).is_file()


@pytest.fixture()
def env(tmp_path):
    """两个会话 + sess-a 名下一个带 csv/json 的模拟 run(直接写 store + 工作区文件)。"""
    home = tmp_path / "home"
    app = create_app(home=home)
    with TestClient(app) as client:
        client.post("/api/sessions", json={"id": "sess-a"})
        client.post("/api/sessions", json={"id": "sess-b"})
        store = Store(Database(home / "insar.db"))
        ws = home / "sessions" / "sess-a"
        store.create_run(RUN_ID, "sess-a", workspace=str(ws))
        ws_b = home / "sessions" / "sess-b"
        store.create_run(OTHER_RUN, "sess-b", workspace=str(ws_b))

        export = ws / "export"
        export.mkdir(parents=True, exist_ok=True)
        (export / "a.csv").write_text("x\n1\n", encoding="utf-8")
        (ws / "qa.json").write_text('{"n": 1, "ok": true}', encoding="utf-8")
        (home / "evil.csv").write_text("secret\n", encoding="utf-8")

        yield {"client": client, "store": store, "ws": ws, "home": home}
        store.close()


def _preview(client, path, *, session="sess-a", run_id=RUN_ID, image=False):
    url = "/api/preview/image" if image else "/api/preview"
    return client.get(url, params={"session": session, "run_id": run_id, "path": path})


def _assert_no_drive_leak(resp, home: Path) -> None:
    """404/400 体不得携带盘符绝对路径(如 E:\\...)。"""
    text = resp.text
    assert "E:\\" not in text
    assert _WIN_DRIVE.search(text) is None, text
    leak = str(home).replace("\\", "/")
    assert leak not in text.replace("\\\\", "/").replace("\\", "/")


def test_preview_csv_by_workspace_path(env):
    """export/a.csv 在工作区即可预览,不要求列入 artifacts。"""
    r = _preview(env["client"], "export/a.csv")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["run"] == RUN_ID
    assert body["name"] == "a.csv"
    assert body["size"] > 0
    if _has_handler("table.py"):
        assert body["kind"] in {"table", "unsupported"}
    else:
        assert body["kind"] == "unsupported"


def test_preview_traversal_404_no_drive_path(env):
    r = _preview(env["client"], "../evil.csv")
    assert r.status_code == 404
    _assert_no_drive_leak(r, env["home"])


def test_preview_empty_path_400(env):
    # 显式 path= :TestClient 对空字符串有时会省略 params
    r = env["client"].get(
        f"/api/preview?session=sess-a&run_id={RUN_ID}&path=")
    assert r.status_code == 400
    _assert_no_drive_leak(r, env["home"])


def test_preview_wrong_session_or_other_run_404(env):
    c = env["client"]
    cross = _preview(c, "export/a.csv", session="sess-b", run_id=RUN_ID)
    assert cross.status_code == 404
    _assert_no_drive_leak(cross, env["home"])

    other = _preview(c, "export/a.csv", session="sess-a", run_id=OTHER_RUN)
    assert other.status_code == 404
    _assert_no_drive_leak(other, env["home"])

    missing = _preview(c, "export/a.csv", session="sess-a", run_id="nope-run")
    assert missing.status_code == 404
    _assert_no_drive_leak(missing, env["home"])


def test_preview_image_csv_without_png_404(env):
    r = _preview(env["client"], "export/a.csv", image=True)
    assert r.status_code == 404
    _assert_no_drive_leak(r, env["home"])


def test_preview_json_file_200(env):
    r = _preview(env["client"], "qa.json")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["run"] == RUN_ID
    assert body["name"] == "qa.json"
    if _has_handler("json_view.py"):
        assert body["kind"] in {"json", "unsupported"}
    else:
        assert body["kind"] == "unsupported"


def test_preview_csv_second_page(env):
    ws = env["ws"]
    lines = ["x"] + [str(i) for i in range(250)]
    (ws / "export" / "wide.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    r = env["client"].get("/api/preview", params={
        "session": "sess-a", "run_id": RUN_ID, "path": "export/wide.csv",
        "offset": 200, "limit": 200,
    })
    assert r.status_code == 200
    body = r.json()
    if body.get("kind") != "table":
        return
    assert body["n_rows_total"] == 250
    assert body["offset"] == 200
    assert body["rows"][0] == ["200"]
    assert body["rows"][-1] == ["249"]
