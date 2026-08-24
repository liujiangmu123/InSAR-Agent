"""把二维数值阵编码成 PNG。只用 numpy+zlib,不依赖 matplotlib,不编造像元。"""

from __future__ import annotations

import struct
import zlib
from typing import Any


def _as_float_plane(data: Any, *, nodata: object = None):
    """已在内存中的 2D 阵 → float64,无效为 nan。无 numpy / 非 2D → None。"""
    try:
        import numpy as np
    except ImportError:
        return None
    if data is None:
        return None
    if np.ma.isMaskedArray(data):
        arr = np.ma.filled(np.ma.asarray(data, dtype=np.float64), np.nan)
    else:
        arr = np.asarray(data, dtype=np.float64)
        if nodata is not None:
            try:
                nd = float(nodata)
            except (TypeError, ValueError):
                nd = None
            if nd is not None and np.isfinite(nd):
                arr = np.where(arr == nd, np.nan, arr)
    if arr.ndim != 2 or arr.size == 0:
        return None
    return arr


def _json_float(value: object) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def plane_stats(data: Any, *, nodata: object = None) -> dict[str, float | int | None] | None:
    """从已加载的预览平面算统计,不二次读盘。无 numpy → None(调用方省略 stats)。"""
    try:
        import numpy as np
    except ImportError:
        return None
    arr = _as_float_plane(data, nodata=nodata)
    if arr is None:
        return None
    finite = np.isfinite(arr)
    n_finite = int(finite.sum())
    n = int(arr.size)
    nan_fraction = float(1.0 - n_finite / n) if n else 1.0
    if n_finite == 0:
        return {
            "vmin": None,
            "vmax": None,
            "p2": None,
            "p98": None,
            "nan_fraction": nan_fraction,
            "n_finite": n_finite,
        }
    finite_vals = arr[finite]
    p2, p98 = np.percentile(finite_vals, (2.0, 98.0))
    return {
        "vmin": _json_float(np.min(finite_vals)),
        "vmax": _json_float(np.max(finite_vals)),
        "p2": _json_float(p2),
        "p98": _json_float(p98),
        "nan_fraction": nan_fraction,
        "n_finite": n_finite,
    }


def array_to_png(data: Any, *, nodata: object = None) -> bytes | None:
    """2D 有限值 → 8bit RGB PNG;全无效/非 2D → None。"""
    try:
        import numpy as np
    except ImportError:
        return None
    arr = _as_float_plane(data, nodata=nodata)
    if arr is None:
        return None
    rgb = _colorize(np.where(np.isfinite(arr), arr, np.nan))
    if rgb is None:
        return None
    return _png_rgb(rgb)


def _colorize(arr):
    import numpy as np

    finite = np.isfinite(arr)
    h, w = arr.shape
    rgb = np.empty((h, w, 3), dtype=np.uint8)
    rgb[:] = 160  # 无效像元中性灰
    if not finite.any():
        return rgb
    lo, hi = np.nanpercentile(arr, (2.0, 98.0))
    if not np.isfinite(lo) or not np.isfinite(hi):
        return None
    if lo == hi:
        rgb[finite] = (200, 200, 200)
        return rgb
    t = np.clip((arr - lo) / (hi - lo), 0.0, 1.0)
    # 蓝-白-红(发散),与速度场惯例同向,不是科学色标声明
    pos = t >= 0.5
    neg = ~pos
    u = np.empty_like(t)
    u[neg] = t[neg] * 2.0
    u[pos] = (t[pos] - 0.5) * 2.0
    r = np.zeros_like(t)
    g = np.zeros_like(t)
    b = np.zeros_like(t)
    r[neg] = 40 + 215 * u[neg]
    g[neg] = 70 + 185 * u[neg]
    b[neg] = 180 + 75 * u[neg]
    r[pos] = 255
    g[pos] = 255 - 200 * u[pos]
    b[pos] = 255 - 220 * u[pos]
    rgb[finite, 0] = r[finite].astype(np.uint8)
    rgb[finite, 1] = g[finite].astype(np.uint8)
    rgb[finite, 2] = b[finite].astype(np.uint8)
    return rgb


def _png_rgb(rgb) -> bytes:
    h, w, _c = rgb.shape
    raw = b"".join(b"\x00" + rgb[i].tobytes() for i in range(int(h)))
    compressed = zlib.compress(raw, 9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", int(w), int(h), 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", compressed) + chunk(b"IEND", b""))
