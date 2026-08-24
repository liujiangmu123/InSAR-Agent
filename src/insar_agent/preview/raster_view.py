"""GeoTIFF / GMT grid / NetCDF 栅格预览:overview 或降采样 PNG,不编造像元。

依赖按序尝试 rasterio → osgeo.gdal → tifffile+numpy+matplotlib。
缺库或打不开时诚实 unsupported;CRS 未知则为 null,绝不填 EPSG:4326。
"""

from __future__ import annotations

from pathlib import Path

from insar_agent.preview.dispatch import Preview, register

_MAX_DIM = 1024
_MISSING_NOTE = "未安装 rasterio/GDAL，无法把 GeoTIFF 转 PNG"
_PREVIEW_NOTE = "降采样预览非原分辨率"


def _probe_backend() -> str | None:
    try:
        import rasterio  # noqa: F401
        return "rasterio"
    except ImportError:
        pass
    try:
        from osgeo import gdal  # noqa: F401
        return "gdal"
    except ImportError:
        pass
    try:
        import matplotlib  # noqa: F401
        import numpy  # noqa: F401
        import tifffile  # noqa: F401
        return "tifffile"
    except ImportError:
        pass
    return None


def _crs_str(crs: object | None) -> str | None:
    """文件自带 CRS 的字符串;未知则 None。绝不默认 EPSG:4326。"""
    if crs is None:
        return None
    text = str(crs).strip()
    return text or None


def _target_wh(width: int, height: int, max_dim: int = _MAX_DIM) -> tuple[int, int]:
    width = int(width)
    height = int(height)
    m = max(width, height)
    if m <= max_dim:
        return width, height
    scale = max_dim / m
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


def _missing_lib() -> Preview:
    return Preview(
        kind="unsupported",
        note=_MISSING_NOTE,
        payload={"reason": "missing_raster_lib"},
    )


def _cannot_open(path: Path, exc: BaseException | None = None) -> Preview:
    suffix = path.suffix.lower()
    note = f"无法打开 {suffix or '栅格'}"
    if exc is not None:
        note = f"{note}:{type(exc).__name__}"
    return Preview(
        kind="unsupported",
        note=note,
        payload={"reason": "cannot_open", "suffix": suffix},
    )


def _success(
    *,
    width: int,
    height: int,
    dtype: object,
    crs: str | None,
    band_count: int,
    png: bytes | None,
    plane: object = None,
    nodata: object = None,
) -> Preview:
    payload: dict = {
        "width": int(width),
        "height": int(height),
        "dtype": str(dtype),
        "crs": crs,
        "band_count": int(band_count),
        "note": _PREVIEW_NOTE,
        "map": {"height": int(height), "width": int(width)},
    }
    try:
        from insar_agent.preview.array_png import plane_stats
        stats = plane_stats(plane, nodata=nodata)
    except Exception:  # noqa: BLE001 — 无 numpy 则省略,不编造
        stats = None
    if stats is not None:
        payload["stats"] = stats
    return Preview(kind="raster", payload=payload, png=png, note=_PREVIEW_NOTE)


def _array_to_png(data: object, nodata: object = None) -> bytes | None:
    """用 nanpercentile 定色标;无 numpy 或全无效则跳过 PNG。"""
    try:
        from insar_agent.preview.array_png import array_to_png
    except Exception:  # noqa: BLE001
        return None
    return array_to_png(data, nodata=nodata)


def _sample_plane(plane, out_w: int, out_h: int):
    """从已有像元取预览窗,不插值编造。"""
    import numpy as np

    a = np.asarray(plane)
    h, w = int(a.shape[0]), int(a.shape[1])
    if (w, h) == (out_w, out_h):
        return a
    y = np.linspace(0, h - 1, out_h).round().astype(int)
    x = np.linspace(0, w - 1, out_w).round().astype(int)
    return a[np.ix_(y, x)]


@register(".tif", ".tiff", ".grd", ".nc")
def preview_raster(path: Path) -> Preview:
    backend = _probe_backend()
    if backend is None:
        return _missing_lib()
    if backend == "rasterio":
        return _with_rasterio(path)
    if backend == "gdal":
        return _with_gdal(path)
    return _with_tifffile(path)


def _with_rasterio(path: Path) -> Preview:
    try:
        import rasterio
        from rasterio.enums import Resampling
    except ImportError:
        return _missing_lib()
    try:
        with rasterio.Env(GDAL_PAM_ENABLED="NO"):
            with rasterio.open(path) as src:
                if src.count < 1 or src.width < 1 or src.height < 1:
                    return _cannot_open(path)
                width, height = int(src.width), int(src.height)
                out_w, out_h = _target_wh(width, height)
                arr = src.read(
                    1,
                    out_shape=(out_h, out_w),
                    resampling=Resampling.nearest,
                    masked=True,
                )
                dtype = src.dtypes[0]
                crs = _crs_str(src.crs)
                band_count = int(src.count)
                nodata = src.nodata
    except Exception as exc:  # noqa: BLE001 — 打不开就诚实 unsupported
        return _cannot_open(path, exc)
    return _success(
        width=width,
        height=height,
        dtype=dtype,
        crs=crs,
        band_count=band_count,
        png=_array_to_png(arr, nodata=nodata),
        plane=arr,
        nodata=nodata,
    )


def _with_gdal(path: Path) -> Preview:
    try:
        from osgeo import gdal
    except ImportError:
        return _missing_lib()
    ds = gdal.Open(str(path), gdal.GA_ReadOnly)
    if ds is None:
        return _cannot_open(path)
    try:
        if ds.RasterCount < 1 or ds.RasterXSize < 1 or ds.RasterYSize < 1:
            return _cannot_open(path)
        width, height = int(ds.RasterXSize), int(ds.RasterYSize)
        band_count = int(ds.RasterCount)
        crs = _crs_str(ds.GetProjection() or None)
        band = ds.GetRasterBand(1)
        dtype = gdal.GetDataTypeName(band.DataType)
        nodata = band.GetNoDataValue()
        out_w, out_h = _target_wh(width, height)
        arr = band.ReadAsArray(buf_xsize=out_w, buf_ysize=out_h)
    except Exception as exc:  # noqa: BLE001
        return _cannot_open(path, exc)
    finally:
        ds = None
    return _success(
        width=width,
        height=height,
        dtype=dtype,
        crs=crs,
        band_count=band_count,
        png=_array_to_png(arr, nodata=nodata),
        plane=arr,
        nodata=nodata,
    )


def _with_tifffile(path: Path) -> Preview:
    try:
        import numpy as np
        import tifffile
    except ImportError:
        return _missing_lib()
    try:
        with tifffile.TiffFile(str(path)) as tif:
            if not tif.pages:
                return _cannot_open(path)
            page = tif.pages[0]
            arr = page.asarray()
            samples = int(getattr(page, "samplesperpixel", 0) or 0)
    except Exception as exc:  # noqa: BLE001 — .nc/.grd/坏 TIFF 诚实失败
        return _cannot_open(path, exc)
    if arr is None or np.asarray(arr).size == 0:
        return _cannot_open(path)
    a = np.asarray(arr)
    if a.ndim == 2:
        plane = a
        band_count = samples or 1
    elif a.ndim == 3:
        if samples and a.shape[-1] == samples:
            plane = a[:, :, 0]
            band_count = samples
        elif samples and a.shape[0] == samples:
            plane = a[0]
            band_count = samples
        elif a.shape[-1] <= 4:
            plane = a[:, :, 0]
            band_count = int(a.shape[-1])
        else:
            plane = a[0]
            band_count = int(a.shape[0])
    else:
        return _cannot_open(path)
    height, width = int(plane.shape[0]), int(plane.shape[1])
    out_w, out_h = _target_wh(width, height)
    preview = _sample_plane(plane, out_w, out_h)
    return _success(
        width=width,
        height=height,
        dtype=a.dtype,
        crs=None,
        band_count=band_count,
        png=_array_to_png(preview),
        plane=preview,
    )
