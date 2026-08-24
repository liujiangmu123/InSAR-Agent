"""二进制 SAR 预览:无 sidecar 不猜行列;有 WIDTH 才给 width/可选 PNG。"""

from __future__ import annotations

from array import array
from pathlib import Path

from insar_agent.preview.dispatch import preview_file

MISSING_NOTE = "无宽度元数据，拒绝猜测行列，不渲染假图"


def test_bare_unw_missing_geometry(tmp_path: Path):
    p = tmp_path / "scene.unw"
    p.write_bytes(b"\x00" * 16)
    r = preview_file(p)
    assert r.kind == "binary_sar"
    assert r.png is None
    assert r.note == MISSING_NOTE
    assert r.payload["reason"] == "missing_geometry"
    assert r.payload["size_bytes"] == 16
    assert r.payload["suffix"] == ".unw"
    assert "width" not in r.payload


def test_unw_rsc_width_or_png(tmp_path: Path):
    p = tmp_path / "scene.unw"
    p.write_bytes(array("f", range(32)).tobytes())
    (tmp_path / "scene.rsc").write_text("WIDTH=4\nLENGTH=2\n", encoding="utf-8")
    r = preview_file(p)
    assert r.kind == "binary_sar"
    assert r.payload["size_bytes"] == 128
    assert r.payload["suffix"] == ".unw"
    try:
        import numpy  # noqa: F401
    except ImportError:
        assert r.payload.get("width") == 4
        return
    assert r.png is not None or r.payload.get("width") == 4
    assert r.payload.get("width") == 4


def test_isce_xml_width(tmp_path: Path):
    p = tmp_path / "filt.unw"
    p.write_bytes(b"\x00" * 16)
    (tmp_path / "filt.unw.xml").write_text(
        '<imageFile><property name="width"><value>8</value></property>'
        '<property name="length"><value>1</value></property></imageFile>',
        encoding="utf-8",
    )
    r = preview_file(p)
    assert r.kind == "binary_sar"
    assert r.payload.get("width") == 8
    assert r.payload.get("geometry_source") == "isce_xml"


def test_gamma_par_width(tmp_path: Path):
    p = tmp_path / "20200101.slc"
    p.write_bytes(b"\x00" * 16)
    (tmp_path / "20200101.par").write_text(
        "range_samples:                 5\n"
        "azimuth_lines:                 3\n",
        encoding="utf-8",
    )
    r = preview_file(p)
    assert r.kind == "binary_sar"
    assert r.payload.get("width") == 5
    assert r.payload.get("geometry_source") == "gamma_par"
    assert r.png is None  # .slc dtype 无把握,只给元数据


def test_isce_stem_xml_width(tmp_path: Path):
    p = tmp_path / "filt.unw"
    p.write_bytes(b"\x00" * 16)
    (tmp_path / "filt.xml").write_text(
        '<imageFile><property name="width"><value>12</value></property>'
        '<property name="length"><value>2</value></property></imageFile>',
        encoding="utf-8",
    )
    r = preview_file(p)
    assert r.kind == "binary_sar"
    assert r.payload.get("width") == 12
    assert r.payload.get("length") == 2
    assert r.payload.get("geometry_source") == "isce_xml"


def test_name_xml_beats_stem_xml(tmp_path: Path):
    p = tmp_path / "filt.unw"
    p.write_bytes(b"\x00" * 16)
    (tmp_path / "filt.unw.xml").write_text(
        '<property name="width"><value>8</value></property>',
        encoding="utf-8",
    )
    (tmp_path / "filt.xml").write_text(
        '<property name="width"><value>99</value></property>',
        encoding="utf-8",
    )
    r = preview_file(p)
    assert r.payload.get("width") == 8
    assert r.payload.get("geometry_source") == "isce_xml"


def test_par_beats_stem_xml(tmp_path: Path):
    p = tmp_path / "20200101.slc"
    p.write_bytes(b"\x00" * 16)
    (tmp_path / "20200101.par").write_text(
        "range_samples:                 5\n",
        encoding="utf-8",
    )
    (tmp_path / "20200101.xml").write_text(
        '<property name="width"><value>99</value></property>',
        encoding="utf-8",
    )
    r = preview_file(p)
    assert r.payload.get("width") == 5
    assert r.payload.get("geometry_source") == "gamma_par"


def test_envi_name_hdr_width(tmp_path: Path):
    p = tmp_path / "scene.int"
    p.write_bytes(b"\x00" * 16)
    (tmp_path / "scene.int.hdr").write_text(
        "ENVI\nsamples = 16\nlines = 4\n",
        encoding="utf-8",
    )
    r = preview_file(p)
    assert r.kind == "binary_sar"
    assert r.payload.get("width") == 16
    assert r.payload.get("length") == 4
    assert r.payload.get("geometry_source") == "envi_hdr"
    assert r.png is None


def test_envi_stem_hdr_width(tmp_path: Path):
    p = tmp_path / "scene.mli"
    p.write_bytes(b"\x00" * 16)
    (tmp_path / "scene.hdr").write_text("samples=7\nlines=3\n", encoding="utf-8")
    r = preview_file(p)
    assert r.payload.get("width") == 7
    assert r.payload.get("length") == 3
    assert r.payload.get("geometry_source") == "envi_hdr"
    assert r.png is None


def test_name_hdr_beats_stem_hdr(tmp_path: Path):
    p = tmp_path / "scene.diff"
    p.write_bytes(b"\x00" * 16)
    (tmp_path / "scene.diff.hdr").write_text("samples = 5\n", encoding="utf-8")
    (tmp_path / "scene.hdr").write_text("samples = 99\n", encoding="utf-8")
    r = preview_file(p)
    assert r.payload.get("width") == 5
    assert r.payload.get("geometry_source") == "envi_hdr"


def test_hdr_without_samples_missing_geometry(tmp_path: Path):
    p = tmp_path / "scene.unw"
    p.write_bytes(b"\x00" * 16)
    (tmp_path / "scene.hdr").write_text("ENVI\nbands = 1\n", encoding="utf-8")
    r = preview_file(p)
    assert r.png is None
    assert r.note == MISSING_NOTE
    assert r.payload["reason"] == "missing_geometry"
    assert "width" not in r.payload
