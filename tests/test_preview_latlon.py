"""preview lat/lon → sample_row/col,与 /api/timeseries-point 同一半像素换算。"""

from __future__ import annotations

from pathlib import Path

import pytest

from insar_agent.preview.dispatch import PreviewOpts, preview_file


def _write_ts(path: Path, *, with_geo: bool) -> None:
    h5py = pytest.importorskip("h5py")
    cube = [[[0.0] * 5 for _ in range(4)] for _ in range(3)]
    for k in range(3):
        cube[k][1][2] = float(k + 1)
    with h5py.File(path, "w") as f:
        f.create_dataset("date", data=[b"20190101", b"20190113", b"20190125"])
        f.create_dataset("timeseries", data=cube, dtype="float32")
        if with_geo:
            f.attrs["X_FIRST"] = "-118.0"
            f.attrs["X_STEP"] = "0.1"
            f.attrs["Y_FIRST"] = "36.0"
            f.attrs["Y_STEP"] = "-0.1"


def test_latlon_hits_known_pixel(tmp_path: Path) -> None:
    """像元中心 (35.9, -117.8) → (row=1, col=2);坐标由文件自身 geo attrs 算出。"""
    path = tmp_path / "timeseries.h5"
    _write_ts(path, with_geo=True)
    prev = preview_file(path, PreviewOpts(lat=35.9, lon=-117.8))
    assert prev.kind == "timeseries"
    assert prev.payload["sample_row"] == 1
    assert prev.payload["sample_col"] == 2
    assert prev.payload["values"] == pytest.approx([1.0, 2.0, 3.0])


def test_latlon_ignored_without_geo_keeps_center(tmp_path: Path) -> None:
    path = tmp_path / "timeseries.h5"
    _write_ts(path, with_geo=False)
    prev = preview_file(path, PreviewOpts(lat=35.9, lon=-117.8))
    assert prev.kind == "timeseries"
    assert prev.payload["sample_row"] == 2
    assert prev.payload["sample_col"] == 2
    note = str(prev.payload.get("note") or prev.note or "")
    assert "row/col" in note
    assert "radar" in note.lower()


def test_preview_opts_latlon_clamp_and_query_pairs() -> None:
    bad = PreviewOpts(lat=float("inf"), lon=float("nan")).clamp()
    assert bad.lat is None and bad.lon is None
    ok = PreviewOpts(lat=35.9, lon=-117.8).clamp()
    pairs = dict(ok.query_pairs())
    assert pairs["lat"] == str(ok.lat)
    assert pairs["lon"] == str(ok.lon)
