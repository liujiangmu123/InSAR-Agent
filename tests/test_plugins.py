"""声明式插件清单(plugins/loader + /api/plugins + figures files.viewers)。

  - 内置 catalog N 份可加载;offset-timeseries 为 ready(预览已有 offset/GOFF,非自研追踪)
  - 文件名匹配:suffixes 与 name_contains 同时声明则 AND;reserved 也返回
  - 坏 YAML 警告跳过,不拖垮清单
  - <home>/plugins/<id>/plugin.yaml 以 source=home 出现
  - GET /api/plugins 200,不泄露 home 绝对路径
绝不 import/exec 插件目录里的 Python。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from insar_agent.api.app import create_app
from insar_agent.plugins.loader import (
    PluginWarning,
    load_plugins,
    match_viewers,
)

BUILTIN_IDS = {
    "journal-png",
    "point-timeseries",
    "offset-timeseries",
    "kmz-earth",
    "interferogram-binary",
    "step-skills",
    "scenario-packs",
    "engine-method",
    "table-csv",
    "excel-xlsx",
    "json-tree",
    "hdf5-structure",
    "raster-geotiff",
    "text-sidecar",
    "zip-members",
    "pdf-pages",
    "shp-table",
    "mat-structure",
}

FOO_YAML = """\
id: foo
kind: viewer
status: reserved
title: 测试插件
version: 0.1.0
render: none
sidebar: files
note: 单测夹具
"""


def _write_plugin(home: Path, name: str, text: str) -> Path:
    d = home / "plugins" / name
    d.mkdir(parents=True)
    path = d / "plugin.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_n_builtin():
    plugins = load_plugins()
    assert {p.id for p in plugins} == BUILTIN_IDS
    assert all(p.source == "builtin" for p in plugins)
    by_id = {p.id: p for p in plugins}
    assert by_id["offset-timeseries"].status == "ready"
    assert by_id["offset-timeseries"].render == "timeseries_point"
    assert by_id["point-timeseries"].status == "ready"
    assert by_id["shp-table"].render == "table"
    assert by_id["mat-structure"].render == "hdf5_meta"
    assert by_id["interferogram-binary"].status == "ready"
    assert by_id["interferogram-binary"].render == "binary_meta"
    assert by_id["table-csv"].render == "table"
    assert by_id["excel-xlsx"].status == "ready"
    assert by_id["json-tree"].render == "json_tree"
    assert by_id["hdf5-structure"].render == "hdf5_meta"
    assert by_id["raster-geotiff"].render == "raster_png"
    assert by_id["text-sidecar"].render == "text_plain"
    assert by_id["zip-members"].render == "zip_list"
    assert by_id["pdf-pages"].render == "pdf_meta"
    assert by_id["step-skills"].skill_type == "step"
    assert by_id["scenario-packs"].skill_type == "scenario"
    assert by_id["engine-method"].kind == "engine"


def test_match_velocity_h5_not_offset():
    ids = [h["id"] for h in match_viewers("velocity.h5")]
    assert "offset-timeseries" not in ids
    assert "point-timeseries" not in ids
    assert "hdf5-structure" in ids


def test_match_azimuth_offset_h5():
    hits = match_viewers("azimuthOffset.h5")
    assert any(h["id"] == "offset-timeseries" for h in hits)
    hit = next(h for h in hits if h["id"] == "offset-timeseries")
    assert hit["status"] == "ready"
    assert set(hit) == {"id", "status", "title", "render"}


def test_match_timeseries_h5():
    hits = match_viewers("timeseries.h5")
    assert any(h["id"] == "point-timeseries" for h in hits)
    assert "offset-timeseries" not in {h["id"] for h in hits}


def test_match_shp_and_mat():
    shp = match_viewers("points.shp")
    assert any(h["id"] == "shp-table" for h in shp)
    assert next(h for h in shp if h["id"] == "shp-table")["status"] == "ready"
    mat = match_viewers("gbis.mat")
    assert any(h["id"] == "mat-structure" for h in mat)
    assert next(h for h in mat if h["id"] == "mat-structure")["render"] == "hdf5_meta"


def test_bad_yaml_skipped(tmp_path):
    home = tmp_path / "home"
    _write_plugin(home, "foo", FOO_YAML)
    _write_plugin(home, "bad", "{[}")
    with pytest.warns(PluginWarning, match="YAML 解析失败"):
        plugins = load_plugins(home)
    ids = {p.id for p in plugins}
    assert "foo" in ids
    assert "bad" not in ids
    assert BUILTIN_IDS <= ids


def test_missing_required_skipped(tmp_path):
    home = tmp_path / "home"
    _write_plugin(home, "noid", "kind: viewer\nstatus: ready\ntitle: 缺 id\n")
    with pytest.warns(PluginWarning, match="缺 id"):
        plugins = load_plugins(home)
    assert "noid" not in {p.id for p in plugins}


def test_unknown_field_still_loads(tmp_path):
    home = tmp_path / "home"
    _write_plugin(home, "foo", FOO_YAML + "extra_key: 1\n")
    with pytest.warns(PluginWarning, match="未知字段"):
        plugins = load_plugins(home)
    foo = next(p for p in plugins if p.id == "foo")
    assert foo.source == "home"


def test_home_plugin_listed(tmp_path):
    home = tmp_path / "home"
    _write_plugin(home, "foo", FOO_YAML)
    plugins = load_plugins(home)
    foo = next(p for p in plugins if p.id == "foo")
    assert foo.source == "home"
    assert foo.origin == "plugins/foo"


def test_outside_plugins_dir_not_loaded(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "plugin.yaml").write_text(
        FOO_YAML.replace("id: foo", "id: evil"), encoding="utf-8")
    plugins = load_plugins(home)
    assert "evil" not in {p.id for p in plugins}


def test_api_plugins_200(tmp_path):
    home = tmp_path / "home"
    _write_plugin(home, "foo", FOO_YAML)
    app = create_app(home=home)
    with TestClient(app) as client:
        r = client.get("/api/plugins")
        assert r.status_code == 200
        body = r.json()
        assert set(body) >= {"plugins", "roots"}
        ids = {p["id"] for p in body["plugins"]}
        assert BUILTIN_IDS <= ids
        foo = next(p for p in body["plugins"] if p["id"] == "foo")
        assert foo["source"] == "home"
        assert "plugins/foo" in body["roots"]
        assert "catalog" in body["roots"]
        home_abs = str(home.resolve())
        assert home_abs not in r.text
        assert all(isinstance(x, str) and not Path(x).is_absolute()
                   for x in body["roots"])
        offset = next(p for p in body["plugins"]
                      if p["id"] == "offset-timeseries")
        assert offset["status"] == "ready"
