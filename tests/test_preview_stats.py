"""Preview stats/map: 只来自已加载的降采样平面,不编造、不整载立方体。"""

from __future__ import annotations

import json
import struct
import warnings
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from insar_agent.preview.array_png import plane_stats  # noqa: E402
from insar_agent.preview.dispatch import preview_file  # noqa: E402
from insar_agent.preview.hdf5_view import MAX_PNG_EDGE  # noqa: E402
from insar_agent.preview.raster_view import _probe_backend  # noqa: E402
from insar_agent.preview.timeseries_view import preview_if_timeseries  # noqa: E402

_STATS_KEYS = ("vmin", "vmax", "p2", "p98", "nan_fraction", "n_finite")


def _png_hw(png: bytes) -> tuple[int, int]:
    w, h = struct.unpack(">II", png[16:24])
    return int(w), int(h)


def _assert_stats_shape(stats: dict) -> None:
    assert set(stats) == set(_STATS_KEYS)
    json.dumps(stats)
    assert isinstance(stats["n_finite"], int)
    assert isinstance(stats["nan_fraction"], float)


def test_plane_stats_from_in_memory_array() -> None:
    arr = np.arange(100, dtype=np.float64).reshape(10, 10)
    stats = plane_stats(arr)
    assert stats is not None
    _assert_stats_shape(stats)
    assert stats["vmin"] == 0.0
    assert stats["vmax"] == 99.0
    assert stats["n_finite"] == 100
    assert stats["nan_fraction"] == 0.0
    p2, p98 = np.percentile(arr, (2.0, 98.0))
    assert stats["p2"] == pytest.approx(float(p2))
    assert stats["p98"] == pytest.approx(float(p98))


def test_plane_stats_all_nan_is_null_not_invented() -> None:
    stats = plane_stats(np.full((4, 5), np.nan))
    assert stats is not None
    _assert_stats_shape(stats)
    assert stats["vmin"] is None
    assert stats["vmax"] is None
    assert stats["p2"] is None
    assert stats["p98"] is None
    assert stats["n_finite"] == 0
    assert stats["nan_fraction"] == 1.0


def test_plane_stats_nodata_and_omit_non_2d() -> None:
    arr = np.array([[1.0, -9999.0], [3.0, 5.0]])
    stats = plane_stats(arr, nodata=-9999.0)
    assert stats is not None
    assert stats["vmin"] == 1.0
    assert stats["vmax"] == 5.0
    assert stats["n_finite"] == 3
    assert stats["nan_fraction"] == pytest.approx(0.25)
    assert plane_stats(None) is None
    assert plane_stats(np.arange(8.0)) is None


def test_hdf5_2d_stats_and_original_map(tmp_path: Path) -> None:
    h5py = pytest.importorskip("h5py")
    path = tmp_path / "velocity.h5"
    data = np.arange(48, dtype=np.float32).reshape(6, 8)
    data[0, 0] = np.nan
    with h5py.File(path, "w") as hf:
        hf.create_dataset("velocity", data=data)
    result = preview_file(path)
    assert result.kind == "hdf5"
    json.dumps(result.payload)
    assert result.payload["map"] == {"height": 6, "width": 8}
    stats = result.payload["stats"]
    _assert_stats_shape(stats)
    finite = data[np.isfinite(data)]
    assert stats["vmin"] == float(finite.min())
    assert stats["vmax"] == float(finite.max())
    assert stats["n_finite"] == int(finite.size)
    assert stats["nan_fraction"] == pytest.approx(1.0 / 48.0)
    p2, p98 = np.percentile(finite, (2.0, 98.0))
    assert stats["p2"] == pytest.approx(float(p2))
    assert stats["p98"] == pytest.approx(float(p98))
    png_w, png_h = _png_hw(result.png)
    assert (png_h, png_w) == (6, 8)


def test_hdf5_3d_stats_from_displayed_slice_not_cube(tmp_path: Path) -> None:
    h5py = pytest.importorskip("h5py")
    path = tmp_path / "velocity_stack.h5"
    cube = np.zeros((3, 5, 7), dtype=np.float32)
    cube[0] = 1.0
    cube[1] = 80.0
    cube[2] = -9.0
    with h5py.File(path, "w") as hf:
        hf.create_dataset("velocity", data=cube)
    result = preview_file(path)
    assert result.kind == "hdf5"
    assert result.payload["map"] == {"height": 5, "width": 7}
    assert result.payload["slice"] == 0
    stats = result.payload["stats"]
    _assert_stats_shape(stats)
    assert stats["vmin"] == 1.0
    assert stats["vmax"] == 1.0
    assert stats["p2"] == 1.0
    assert stats["p98"] == 1.0
    assert stats["n_finite"] == 35
    assert stats["nan_fraction"] == 0.0
    assert stats["vmax"] != float(cube.max())
    assert stats["vmin"] != float(cube.min())


def test_hdf5_stats_from_strided_plane_not_full_array(tmp_path: Path) -> None:
    h5py = pytest.importorskip("h5py")
    h, w = 2048, 8
    assert max(h, w) > MAX_PNG_EDGE
    path = tmp_path / "velocity_stride.h5"
    data = np.ones((h, w), dtype=np.float32)
    data[1::2, :] = 999.0
    with h5py.File(path, "w") as hf:
        hf.create_dataset("velocity", data=data)
    result = preview_file(path)
    assert result.kind == "hdf5"
    assert result.payload["map"] == {"height": h, "width": w}
    png_w, png_h = _png_hw(result.png)
    assert (png_h, png_w) != (h, w)
    stats = result.payload["stats"]
    _assert_stats_shape(stats)
    step = max(1, (max(h, w) + MAX_PNG_EDGE - 1) // MAX_PNG_EDGE)
    preview = data[::step, ::step]
    assert stats["vmin"] == float(preview.min())
    assert stats["vmax"] == float(preview.max())
    assert stats["vmax"] != 999.0
    assert stats["n_finite"] == int(preview.size)


def test_hdf5_all_nan_stats_null(tmp_path: Path) -> None:
    h5py = pytest.importorskip("h5py")
    path = tmp_path / "velocity.h5"
    with h5py.File(path, "w") as hf:
        hf.create_dataset("velocity", data=np.full((4, 4), np.nan, dtype=np.float32))
    result = preview_file(path)
    stats = result.payload["stats"]
    assert stats["vmin"] is None
    assert stats["vmax"] is None
    assert stats["p2"] is None
    assert stats["p98"] is None
    assert stats["n_finite"] == 0
    assert stats["nan_fraction"] == 1.0


def test_timeseries_stats_from_slice_keeps_column_values(tmp_path: Path) -> None:
    h5py = pytest.importorskip("h5py")
    path = tmp_path / "timeseries.h5"
    cube = np.zeros((3, 4, 5), dtype=np.float32)
    cube[0] = 1.0
    cube[1] = 2.0
    cube[2] = 3.0
    with h5py.File(path, "w") as f:
        f.create_dataset("date", data=[b"20190101", b"20190113", b"20190125"])
        f.create_dataset("timeseries", data=cube)
    prev = preview_if_timeseries(path)
    assert prev is not None
    assert prev.kind == "timeseries"
    json.dumps(prev.payload)
    assert prev.payload["values"] == [1.0, 2.0, 3.0]
    assert prev.payload["sample_row"] == 2
    assert prev.payload["sample_col"] == 2
    assert prev.payload["map"] == {"height": 4, "width": 5}
    stats = prev.payload["stats"]
    _assert_stats_shape(stats)
    assert stats["vmin"] == 1.0
    assert stats["vmax"] == 1.0
    assert stats["n_finite"] == 20
    assert stats["nan_fraction"] == 0.0
    assert stats["n_finite"] != len(prev.payload["values"])
    assert stats["vmax"] != max(prev.payload["values"])


def test_raster_success_payload_stats_and_map_without_full_read() -> None:
    from insar_agent.preview.raster_view import _success

    plane = np.array([[1.0, 2.0], [3.0, np.nan]], dtype=np.float64)
    result = _success(
        width=100,
        height=200,
        dtype="float32",
        crs=None,
        band_count=1,
        png=None,
        plane=plane,
    )
    assert result.kind == "raster"
    json.dumps(result.payload)
    assert result.payload["map"] == {"height": 200, "width": 100}
    stats = result.payload["stats"]
    _assert_stats_shape(stats)
    assert stats["vmin"] == 1.0
    assert stats["vmax"] == 3.0
    assert stats["n_finite"] == 3
    assert stats["nan_fraction"] == pytest.approx(0.25)


def test_raster_omits_stats_when_helper_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    import insar_agent.preview.array_png as array_png
    from insar_agent.preview.raster_view import _success

    monkeypatch.setattr(array_png, "plane_stats", lambda *a, **k: None)
    result = _success(
        width=8,
        height=8,
        dtype="float32",
        crs=None,
        band_count=1,
        png=None,
        plane=np.ones((4, 4)),
    )
    assert "stats" not in result.payload
    assert result.payload["map"] == {"height": 8, "width": 8}


@pytest.mark.skipif(_probe_backend() != "rasterio", reason="需要 rasterio 写 8x8")
def test_raster_stats_from_preview_plane_respects_nodata(tmp_path: Path) -> None:
    rasterio = pytest.importorskip("rasterio")
    from rasterio.errors import NotGeoreferencedWarning
    from rasterio.transform import Affine

    path = tmp_path / "tiny.tif"
    arr = np.arange(64, dtype=np.float32).reshape(8, 8)
    arr[0, 0] = -9999.0
    profile = {
        "driver": "GTiff",
        "height": 8,
        "width": 8,
        "count": 1,
        "dtype": "float32",
        "nodata": -9999.0,
        "transform": Affine.identity(),
        "crs": None,
    }
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(arr, 1)
    result = preview_file(path)
    assert result.kind == "raster"
    json.dumps(result.payload)
    assert result.payload["map"] == {"height": 8, "width": 8}
    stats = result.payload["stats"]
    _assert_stats_shape(stats)
    finite = np.arange(1, 64, dtype=np.float64)
    assert stats["vmin"] == 1.0
    assert stats["vmax"] == 63.0
    assert stats["n_finite"] == 63
    assert stats["nan_fraction"] == pytest.approx(1.0 / 64.0)
    p2, p98 = np.percentile(finite, (2.0, 98.0))
    assert stats["p2"] == pytest.approx(float(p2))
    assert stats["p98"] == pytest.approx(float(p98))
    assert stats["vmin"] != -9999.0
