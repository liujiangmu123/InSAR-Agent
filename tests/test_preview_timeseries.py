"""时序 HDF5 文件级预览:按文件名接手,只列日期与 shape,不读立方体。"""

from __future__ import annotations

from pathlib import Path

import pytest

from insar_agent.preview.timeseries_view import preview_if_timeseries


def test_velocity_h5_name_returns_none(tmp_path: Path):
    path = tmp_path / "velocity.h5"
    path.write_bytes(b"not a timeseries")
    assert preview_if_timeseries(path) is None


def test_timeseries_h5_dates_and_shape_without_cube(tmp_path: Path):
    h5py = pytest.importorskip("h5py")
    path = tmp_path / "timeseries.h5"
    stamps = [b"20190101", b"20190113", b"20190125"]
    with h5py.File(path, "w") as f:
        f.create_dataset("date", data=stamps)
        f.create_dataset("timeseries", data=[
            [[0.0] * 5 for _ in range(4)] for _ in range(3)
        ], dtype="float32")
    prev = preview_if_timeseries(path)
    assert prev is not None
    assert prev.kind == "timeseries"
    assert prev.payload["kind"] == "timeseries"
    assert prev.payload["n_dates"] == 3
    assert prev.payload["dates"] == ["20190101", "20190113", "20190125"]
    assert prev.payload["shape"] == [3, 4, 5]
    assert prev.payload["values"] == [0.0, 0.0, 0.0]
    assert prev.payload["sample_row"] == 2
    assert prev.payload["sample_col"] == 2
    assert prev.png is not None
    assert prev.png[:8] == b"\x89PNG\r\n\x1a\n"
    assert prev.note is not None and "/api/timeseries-point" in prev.note


def test_timeseries_named_non_hdf5_is_unsupported_not_none(tmp_path: Path):
    pytest.importorskip("h5py")
    path = tmp_path / "timeseries.h5"
    path.write_bytes(b"SIMULATED placeholder, not an HDF5 file")
    prev = preview_if_timeseries(path)
    assert prev is not None
    assert prev.kind == "unsupported"
    assert prev.payload.get("reason") == "invalid_hdf5"
