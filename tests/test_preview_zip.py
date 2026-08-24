"""Zip / KMZ 成员清单预览:只列中央目录,不解压、不编造成员。"""

from __future__ import annotations

import zipfile
from pathlib import Path

from insar_agent.preview.dispatch import PreviewOpts, preview_file

KML = '<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document/></kml>'


def _write_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def test_zip_three_members(tmp_path: Path):
    zpath = tmp_path / "pack.zip"
    payload = {
        "a.txt": b"hello",
        "b/c.txt": b"world!",
        "d.bin": b"\x00\x01\x02",
    }
    _write_zip(zpath, payload)
    result = preview_file(zpath)
    assert result.kind == "zip"
    assert result.truncated is False
    assert result.payload["n_members_total"] == 3
    members = result.payload["members"]
    assert members == [
        {"name": "a.txt", "size": 5},
        {"name": "b/c.txt", "size": 6},
        {"name": "d.bin", "size": 3},
    ]


def test_kmz_lists_doc_kml(tmp_path: Path):
    kpath = tmp_path / "scene.kmz"
    body = KML.encode("utf-8")
    _write_zip(kpath, {"doc.kml": body})
    result = preview_file(kpath)
    assert result.kind == "zip"
    assert result.truncated is False
    assert result.payload["n_members_total"] == 1
    assert result.payload["members"] == [{"name": "doc.kml", "size": len(body)}]


def test_invalid_zip(tmp_path: Path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip archive")
    result = preview_file(bad)
    assert result.kind == "unsupported"
    assert result.payload["reason"] == "invalid_zip"
    assert result.note == "不是合法 zip/kmz"


def test_truncated_after_200(tmp_path: Path):
    zpath = tmp_path / "many.zip"
    members = {f"f{i:03d}.txt": b"x" for i in range(201)}
    _write_zip(zpath, members)
    result = preview_file(zpath)
    assert result.kind == "zip"
    assert result.truncated is True
    assert result.payload["n_members_total"] == 201
    listed = result.payload["members"]
    assert len(listed) == 200
    assert listed[0] == {"name": "f000.txt", "size": 1}
    assert listed[-1] == {"name": "f199.txt", "size": 1}
    assert all(m["name"] in members for m in listed)


def test_zip_csv_member_honors_offset(tmp_path: Path):
    zpath = tmp_path / "table.zip"
    lines = ["a,b"] + [f"{i},{i}" for i in range(5)]
    _write_zip(zpath, {"t.csv": "\n".join(lines).encode("utf-8")})
    first = preview_file(zpath, PreviewOpts(member="t.csv", offset=0, limit=2))
    assert first.kind == "zip"
    assert first.payload["member_kind"] == "table"
    assert first.payload["n_rows_total"] == 5
    assert first.payload["rows"][0] == ["0", "0"]
    assert first.payload["limit"] == 2
    page = preview_file(zpath, PreviewOpts(member="t.csv", offset=2, limit=2))
    assert page.payload["offset"] == 2
    assert page.payload["rows"][0] == ["2", "2"]
    assert page.payload["rows"][1] == ["3", "3"]


def test_zip_text_member_honors_byte_offset(tmp_path: Path):
    zpath = tmp_path / "log.zip"
    body = b"A" * 100 + b"B" * 100
    _write_zip(zpath, {"big.txt": body})
    page = preview_file(zpath, PreviewOpts(member="big.txt", offset=100))
    assert page.kind == "zip"
    assert page.payload["member_kind"] == "text"
    assert page.payload["offset"] == 100
    assert page.payload["n_bytes_total"] == 200
    assert page.payload["member_text"].startswith("B")
    assert page.payload["n_bytes"] == 100
