"""HDF5 结构预览:列出 datasets/attrs,不依赖 timeseries 模块。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

h5py = pytest.importorskip("h5py")
np = pytest.importorskip("numpy")

from insar_agent.preview.dispatch import preview_file  # noqa: E402


def _write_velocity(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as hf:
        hf.create_dataset("velocity", data=np.zeros((8, 8), dtype="float32"))
        hf.attrs["FILE_TYPE"] = "velocity"


def _velocity_entry(datasets: list[dict]) -> dict:
    for item in datasets:
        if str(item["path"]).rstrip("/").endswith("velocity"):
            return item
    raise AssertionError(f"velocity dataset missing: {datasets}")


def test_tiny_2d_lists_datasets_and_attrs(tmp_path: Path) -> None:
    path = tmp_path / "velocity.h5"
    _write_velocity(path)
    result = preview_file(path)
    assert result.kind == "hdf5"
    vel = _velocity_entry(result.payload["datasets"])
    assert list(vel["shape"]) == [8, 8]
    assert "dtype" in vel and vel["dtype"]
    assert result.payload["attrs"]["FILE_TYPE"] == "velocity"
    assert result.note and "立方体" in result.note
    assert result.png is not None
    assert result.png[:8] == b"\x89PNG\r\n\x1a\n"


def test_tiny_2d_png_optional(tmp_path: Path) -> None:
    path = tmp_path / "velocity.h5"
    _write_velocity(path)
    result = preview_file(path)
    assert result.png is not None
    assert result.png[:8] == b"\x89PNG\r\n\x1a\n"


def test_preview_file_works_if_timeseries_import_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "velocity.h5"
    _write_velocity(path)
    monkeypatch.setitem(sys.modules, "insar_agent.preview.timeseries_view", None)
    result = preview_file(path)
    assert result.kind == "hdf5"
    _velocity_entry(result.payload["datasets"])


def test_timeseries_none_uses_hdf5_path(tmp_path: Path) -> None:
    path = tmp_path / "velocity.h5"
    _write_velocity(path)
    try:
        from insar_agent.preview.timeseries_view import preview_if_timeseries
    except Exception:  # noqa: BLE001
        preview_if_timeseries = lambda p: None  # noqa: E731
    assert preview_if_timeseries(path) is None
    result = preview_file(path)
    assert result.kind == "hdf5"
    _velocity_entry(result.payload["datasets"])
