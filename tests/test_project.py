"""项目文件夹:标记、路径沙箱、API、会话绑定。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from insar_agent.api.app import create_app
from insar_agent.core.db import Database
from insar_agent.core.store import Store
from insar_agent.project.paths import (
    init_project_dir,
    list_tree,
    read_text,
    safe_join,
    write_output,
)


def test_safe_join_rejects_escape(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    with pytest.raises(ValueError):
        safe_join(root, "../secret")
    with pytest.raises(ValueError):
        safe_join(root, "E:/other")
    assert safe_join(root, "data/a.tif").is_relative_to(root.resolve())


def test_find_nested_file_by_name(tmp_path):
    from insar_agent.project.paths import find_in_project, init_project_dir

    root = tmp_path / "proj"
    init_project_dir(root, project_id="p-f", name="找文件")
    deep = root / "data" / "a" / "b"
    deep.mkdir(parents=True)
    (deep / "scene.slc").write_bytes(b"x" * 10)
    hits = find_in_project(root, "scene.slc")
    assert hits and hits[0]["rel"] == "data/a/b/scene.slc"
    assert find_in_project(root, "../secret") == []


def test_init_and_list_and_write(tmp_path):
    root = tmp_path / "yushu"
    meta = init_project_dir(root, project_id="p-1", name="玉树")
    assert meta["project_id"] == "p-1"
    (root / "data" / "note.txt").write_text("hello", encoding="utf-8")
    items = list_tree(root)
    names = {i["rel"] for i in items}
    assert "data" in names
    assert "data/note.txt" in names
    path = write_output(root, "output/log.txt", "ok")
    assert path.read_text(encoding="utf-8") == "ok"
    with pytest.raises(ValueError):
        write_output(root, "data/hack.txt", "no")
    assert "hello" in read_text(root, "data/note.txt")


def test_pick_folder_endpoint_override(tmp_path):
    import insar_agent.runtime.folder_pick as fp

    home = tmp_path / "home"
    chosen = tmp_path / "picked"
    chosen.mkdir()
    fp._override = lambda title: str(chosen)
    try:
        with TestClient(create_app(home=home)) as client:
            r = client.post("/api/projects/pick-folder")
            assert r.status_code == 200
            body = r.json()
            assert body["ok"] is True
            assert body["path"] == str(chosen)
            fp._override = lambda title: None
            miss = client.post("/api/projects/pick-folder").json()
            assert miss["ok"] is False and miss["cancelled"] is True
    finally:
        fp._override = None


def test_session_rejects_unknown_project(tmp_path):
    home = tmp_path / "home"
    with TestClient(create_app(home=home)) as client:
        bad = client.post("/api/sessions", json={
            "id": "s-bad-proj", "name": "x", "project_id": "p-does-not-exist"})
        assert bad.status_code == 400


def test_project_then_chat_full_chain(tmp_path):
    """新建项目 → 文件夹可列文件 → 对话挂在项目下。"""
    data = tmp_path / "insar-data"
    data.mkdir()
    (data / "readme.txt").write_text("scene A", encoding="utf-8")
    (data / "data").mkdir()
    (data / "data" / "note.md").write_text("ok", encoding="utf-8")
    home = tmp_path / "home"
    with TestClient(create_app(home=home)) as client:
        proj = client.post("/api/projects", json={"name": "链路测", "root": str(data)}).json()
        assert proj["project_id"]
        assert proj["file_count"] >= 1
        files = client.get(f"/api/projects/{proj['project_id']}/files").json()
        rels = {i["rel"] for i in files["items"]}
        assert "readme.txt" in rels
        sess = client.post("/api/sessions", json={
            "id": "s-chain-1", "name": "研究对话",
            "project_id": proj["project_id"]}).json()
        assert sess["project_id"] == proj["project_id"]
        listed = client.get("/api/sessions").json()
        row = next(x for x in listed if x["session_id"] == "s-chain-1")
        assert row["project_name"] == "链路测"
        assert row["project_root"] == str(data.resolve())


def test_project_api_and_session_bind(tmp_path):
    home = tmp_path / "home"
    with TestClient(create_app(home=home)) as client:
        r = client.post("/api/projects", json={"name": "玉树滑坡"})
        assert r.status_code == 200
        proj = r.json()
        assert proj["project_id"].startswith("p-")
        assert Path(proj["root"]).is_dir()
        assert (Path(proj["root"]) / ".insar" / "project.json").is_file()
        s = client.post("/api/sessions", json={
            "id": "s-proj-1", "name": "玉树会话", "project_id": proj["project_id"]})
        assert s.status_code == 200
        body = s.json()
        assert body["project_id"] == proj["project_id"]
        assert body["project_name"] == "玉树滑坡"
        listed = client.get("/api/sessions").json()
        row = next(x for x in listed if x["session_id"] == "s-proj-1")
        assert row["project_root"] == proj["root"]
        files = client.get(f"/api/projects/{proj['project_id']}/files").json()
        assert files["items"]
        wr = client.post(f"/api/projects/{proj['project_id']}/file",
                         json={"rel": "output/hi.txt", "content": "hi"})
        assert wr.status_code == 200
        rd = client.get(f"/api/projects/{proj['project_id']}/file",
                        params={"rel": "output/hi.txt"})
        assert rd.json()["text"] == "hi"


def test_store_loop_op_roundtrip(tmp_path):
    store = Store(Database(tmp_path / "t.db"))
    store.create_session("s1", "s1")
    store.save_loop_op("s1", op_id="op1", phase="running", goal="分析", n=2,
                       max_cycles=12, cycles_summary=["[1] check_env:ok"],
                       last_action="check_env")
    row = store.load_loop_op("s1")
    assert row["phase"] == "running"
    assert row["cycles_summary"] == ["[1] check_env:ok"]
    store.clear_loop_op("s1")
    assert store.load_loop_op("s1")["phase"] == "idle"
