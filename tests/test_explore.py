"""JIT 学习 / 文档检索 / 受控探针:闭集、零任意命令。"""

from __future__ import annotations

from insar_agent.runtime.explore import learn_tool, probe_scratch, search_docs
from insar_agent.runtime.install_guide import install_hint_for


def test_learn_tool_engine_returns_install_hint():
    text, ok = learn_tool("gdal")
    assert ok
    assert "gdal" in text.lower()
    assert install_hint_for("gdal").split(":")[0] in text


def test_learn_tool_unknown_is_honest():
    text, ok = learn_tool("not-a-real-engine")
    assert not ok
    assert "未收录" in text


def test_learn_tool_rejects_empty():
    text, ok = learn_tool("")
    assert not ok


def test_probe_import_allowlisted_numpy():
    text, ok = probe_scratch("import", "numpy")
    assert ok
    assert "numpy" in text


def test_probe_import_rejects_unknown_module():
    text, ok = probe_scratch("import", "os")
    assert not ok
    assert "不可探测" in text


def test_probe_file_meta_needs_project(tmp_path):
    text, ok = probe_scratch("file_meta", "a.tif", project_root=None)
    assert not ok
    assert "未绑定项目" in text


def test_probe_file_meta_reads_project_file(tmp_path):
    (tmp_path / "scene.tif").write_bytes(b"II*\x00" + b"\x00" * 12)
    text, ok = probe_scratch("file_meta", "scene.tif", project_root=tmp_path)
    assert ok
    assert "scene.tif" in text
    assert "bytes" in text


def test_search_docs_empty_query():
    text, ok = search_docs("")
    assert not ok


def test_search_docs_hits_local_skill_without_network(monkeypatch):
    monkeypatch.setattr("insar_agent.net.websearch.web_search",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    text, ok = search_docs("unwrap")
    assert "网页检索不可用" in text or "技能" in text or "没有检索" in text
