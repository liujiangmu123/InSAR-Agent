"""预览路径钉在工作区内,禁止穿越。"""

from __future__ import annotations

from insar_agent.preview.workspace import resolve_workspace_file


def test_resolve_inside(tmp_path):
    ws = tmp_path / "ws"
    (ws / "export").mkdir(parents=True)
    f = ws / "export" / "a.csv"
    f.write_text("x\n1\n", encoding="utf-8")
    assert resolve_workspace_file(ws, "export/a.csv") == f.resolve()


def test_resolve_traversal_rejected(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    evil = tmp_path / "evil.csv"
    evil.write_text("nope\n", encoding="utf-8")
    assert resolve_workspace_file(ws, "../evil.csv") is None
    assert resolve_workspace_file(ws, str(evil)) is None
    assert resolve_workspace_file(ws, "") is None
