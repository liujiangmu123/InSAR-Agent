"""Shapefile 属性表预览:无 dbf 诚实说明;有 dbf 则列表;bbox 只来自文件头。"""

from __future__ import annotations

import struct
from pathlib import Path

from insar_agent.preview.dispatch import preview_file

_BBOX_FIELDS = ("xmin", "ymin", "xmax", "ymax")


def test_shp_without_dbf(tmp_path: Path) -> None:
    path = tmp_path / "bare.shp"
    path.write_bytes(b"not-a-real-shp")
    result = preview_file(path)
    assert result.kind == "shp"
    assert result.payload["sidecars"][".shp"] is True
    assert result.payload["sidecars"][".dbf"] is False
    assert "dbf" in (result.note or "").lower()
    assert "bbox" not in result.payload


def _write_dbf(path: Path) -> None:
    name = b"ID" + b"\x00" * 9
    field = name + b"C" + b"\x00" * 4 + bytes([4]) + b"\x00" + b"\x00" * 14
    header_len = 32 + 32 + 1
    rec_len = 1 + 4
    hdr = (
        b"\x03" + b"\x00" * 3
        + struct.pack("<I", 1)
        + struct.pack("<HH", header_len, rec_len)
        + b"\x00" * 20
    )
    path.write_bytes(hdr + field + b"\x0d" + b" " + b"0001")


def _write_shp_header(path: Path, xmin: float, ymin: float, xmax: float, ymax: float) -> None:
    buf = bytearray(100)
    struct.pack_into(">i", buf, 0, 9994)
    struct.pack_into(">i", buf, 24, 50)
    struct.pack_into("<i", buf, 28, 1000)
    struct.pack_into("<i", buf, 32, 1)
    struct.pack_into("<4d", buf, 36, xmin, ymin, xmax, ymax)
    path.write_bytes(bytes(buf))


def test_shp_dbf_attribute_table(tmp_path: Path) -> None:
    shp = tmp_path / "pts.shp"
    shp.write_bytes(b"shp")
    _write_dbf(tmp_path / "pts.dbf")
    result = preview_file(shp)
    assert result.kind == "table"
    assert result.payload["columns"] == ["ID"]
    assert result.payload["rows"] == [["0001"]]
    assert result.payload["n_rows_total"] == 1
    assert "bbox" not in result.payload


def test_dbf_alone_attribute_table(tmp_path: Path) -> None:
    dbf = tmp_path / "only.dbf"
    _write_dbf(dbf)
    result = preview_file(dbf)
    assert result.kind == "table"
    assert result.payload["columns"] == ["ID"]
    assert result.payload["rows"] == [["0001"]]
    assert result.payload["n_rows_total"] == 1
    assert "bbox" not in result.payload


def test_shp_header_bbox(tmp_path: Path) -> None:
    shp = tmp_path / "box.shp"
    _write_shp_header(shp, 100.5, 20.25, 110.0, 30.75)
    _write_dbf(tmp_path / "box.dbf")
    result = preview_file(shp)
    assert result.kind == "table"
    assert result.payload["columns"] == ["ID"]
    assert result.payload["rows"] == [["0001"]]
    bbox = result.payload["bbox"]
    assert tuple(bbox) == _BBOX_FIELDS
    assert bbox == {"xmin": 100.5, "ymin": 20.25, "xmax": 110.0, "ymax": 30.75}


def test_shp_header_too_short_omits_bbox(tmp_path: Path) -> None:
    shp = tmp_path / "short.shp"
    buf = bytearray(99)
    struct.pack_into("<4d", buf, 36, 1.0, 2.0, 3.0, 4.0)
    shp.write_bytes(bytes(buf))
    _write_dbf(tmp_path / "short.dbf")
    result = preview_file(shp)
    assert result.kind == "table"
    assert "bbox" not in result.payload
