"""栅格预览:缺库诚实 unsupported;有 rasterio 时写 8x8 GeoTIFF 验 PNG。"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from insar_agent.preview.dispatch import preview_file
from insar_agent.preview.raster_view import (
    _PREVIEW_NOTE,
    _probe_backend,
    _target_wh,
    preview_raster,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_target_wh_caps_max_dim():
    assert _target_wh(100, 80) == (100, 80)
    assert _target_wh(2048, 1024) == (1024, 512)
    assert max(_target_wh(5000, 2000)) == 1024


def test_missing_raster_lib(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from insar_agent.preview import raster_view

    monkeypatch.setattr(raster_view, "_probe_backend", lambda: None)
    p = tmp_path / "a.tif"
    p.write_bytes(b"not-a-geotiff")
    result = preview_raster(p)
    assert result.kind == "unsupported"
    assert result.png is None
    assert result.payload.get("reason") == "missing_raster_lib"
    assert result.note == "未安装 rasterio/GDAL，无法把 GeoTIFF 转 PNG"


def test_register_only_tif_tiff_grd_nc():
    from insar_agent.preview.dispatch import _REGISTRY, _ensure_loaded

    _ensure_loaded()
    suffixes: set[str] = set()
    for group, _name, fn in _REGISTRY:
        if fn is preview_raster or getattr(fn, "__module__", "").endswith("raster_view"):
            suffixes |= set(group)
    assert suffixes == {".tif", ".tiff", ".grd", ".nc"}
    assert not suffixes & {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _write_tiny_geotiff(path: Path) -> None:
    import numpy as np
    import rasterio
    from rasterio.errors import NotGeoreferencedWarning
    from rasterio.transform import Affine

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


@pytest.mark.skipif(_probe_backend() is None, reason="无 rasterio/GDAL/tifffile")
def test_unreadable_nc_grd_is_unsupported(tmp_path: Path):
    for name in ("junk.nc", "junk.grd"):
        p = tmp_path / name
        p.write_bytes(b"not-a-raster")
        result = preview_file(p)
        assert result.kind == "unsupported"
        assert result.png is None
        assert result.payload.get("reason") in {"cannot_open", "handler_error"}


@pytest.mark.skipif(_probe_backend() != "rasterio", reason="需要 numpy+rasterio 写 8x8")
def test_rasterio_tiny_geotiff_preview(tmp_path: Path):
    pytest.importorskip("numpy")
    pytest.importorskip("rasterio")
    path = tmp_path / "tiny.tif"
    _write_tiny_geotiff(path)
    result = preview_file(path)
    assert result.kind == "raster"
    assert result.payload["width"] == 8
    assert result.payload["height"] == 8
    assert result.payload["band_count"] == 1
    assert result.payload["dtype"] == "float32"
    assert result.payload["crs"] is None
    assert result.payload["note"] == _PREVIEW_NOTE
    assert result.note == _PREVIEW_NOTE
    assert result.png is not None
    assert result.png.startswith(PNG_MAGIC)
