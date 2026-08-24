"""时序 HDF5 文件级预览:日期、形状、中心像元曲线、一帧切片图。

不注册 .h5:由 hdf5_view 转调。单像元列 ds[:, r, c] 与一帧 ds[k, ::step, ::step],
绝不整载立方体,不编造形变值。
"""

from __future__ import annotations

import math
from pathlib import Path

from insar_agent.preview.dispatch import Preview, current_opts

_DATE_CAP = 2000
_NOTE = "中心(或指定)像元曲线 + 一帧切片;完整立方体未加载。对话 /api/timeseries-point 可另取点"
_NOTE_RADAR = (
    "该时序文件没有地理参考属性(radar 坐标):lat/lon 已忽略,请用 row/col 定位。"
)


def preview_if_timeseries(path: Path) -> Preview | None:
    """文件名含 timeseries/offset 才接手;否则 None 让 hdf5_view 走通用 HDF5。"""
    name = path.name.lower()
    if "timeseries" not in name and "offset" not in name:
        return None
    try:
        import h5py
    except ImportError:
        return Preview(
            kind="unsupported",
            note="未安装 h5py,无法读取时序 HDF5(pip install 'insar-agent[raster]')",
            payload={"reason": "missing_h5py"},
        )
    if not h5py.is_hdf5(str(path)):
        return Preview(
            kind="unsupported",
            note="文件不是有效的 HDF5,无法按时序列出日期与形状",
            payload={"reason": "invalid_hdf5"},
        )
    opts = current_opts()
    try:
        with h5py.File(path, "r") as f:
            n_dates, dates, truncated = _read_dates(h5py, f)
            shape = _timeseries_shape(h5py, f)
            values, sample_row, sample_col, ignored_latlon = _sample_column(
                h5py, f, opts)
            png, slice_used, stats, map_hw = _slice_png(h5py, f, opts.slice)
    except OSError:
        return Preview(
            kind="unsupported",
            note="文件不是有效的 HDF5,无法按时序列出日期与形状",
            payload={"reason": "invalid_hdf5"},
        )
    if n_dates == 0 and shape:
        n_dates = int(shape[0])
    payload: dict[str, object] = {
        "kind": "timeseries",
        "n_dates": n_dates,
        "dates": dates,
        "values": values,
        "sample_row": sample_row,
        "sample_col": sample_col,
        "slice": slice_used,
    }
    if shape is not None:
        payload["shape"] = shape
        if map_hw is None and len(shape) >= 3:
            map_hw = {"height": int(shape[1]), "width": int(shape[2])}
    if map_hw is not None:
        payload["map"] = map_hw
    if stats is not None:
        payload["stats"] = stats
    note = _NOTE
    if ignored_latlon:
        payload["note"] = _NOTE_RADAR
        note = _NOTE_RADAR
    return Preview(
        kind="timeseries",
        payload=payload,
        png=png,
        truncated=truncated,
        note=note,
    )


def _timeseries_shape(h5py, f) -> list[int] | None:
    obj = f.get("timeseries")
    if not isinstance(obj, h5py.Dataset):
        return None
    return [int(n) for n in obj.shape]


def _attr_float(attrs, key: str) -> float | None:
    """MintPy 属性一律按字符串落盘;bytes/str/数值都归一成有限 float。"""
    v = attrs.get(key)
    if v is None:
        return None
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _geo_transform(attrs) -> tuple[float, float, float, float] | None:
    """X_FIRST/Y_FIRST 为第 0 像元中心;缺任一键或步长为 0 → 无地理参考。"""
    x_first = _attr_float(attrs, "X_FIRST")
    x_step = _attr_float(attrs, "X_STEP")
    y_first = _attr_float(attrs, "Y_FIRST")
    y_step = _attr_float(attrs, "Y_STEP")
    if None in (x_first, x_step, y_first, y_step) or x_step == 0 or y_step == 0:
        return None
    return x_first, x_step, y_first, y_step


def _rowcol_from_latlon(
    lat: float, lon: float, geo: tuple[float, float, float, float],
    n_rows: int, n_cols: int,
) -> tuple[int, int]:
    """与 /api/timeseries-point 同口径:半像素取整后夹到网格内。"""
    x_first, x_step, y_first, y_step = geo
    rf = (lat - y_first) / y_step
    cf = (lon - x_first) / x_step
    r = min(n_rows - 1, max(0, round(rf)))
    c = min(n_cols - 1, max(0, round(cf)))
    return int(r), int(c)


def _sample_column(h5py, f, opts):
    obj = f.get("timeseries")
    if not isinstance(obj, h5py.Dataset) or obj.ndim != 3:
        return None, None, None, False
    n, h, w = (int(s) for s in obj.shape)
    if n <= 0 or h <= 0 or w <= 0:
        return None, None, None, False
    ignored_latlon = False
    r = c = None
    if opts.lat is not None and opts.lon is not None:
        geo = _geo_transform(f.attrs)
        if geo is not None:
            r, c = _rowcol_from_latlon(opts.lat, opts.lon, geo, h, w)
        else:
            ignored_latlon = True
    if r is None or c is None:
        r = h // 2 if opts.row is None else max(0, min(h - 1, int(opts.row)))
        c = w // 2 if opts.col is None else max(0, min(w - 1, int(opts.col)))
    try:
        column = obj[:, r, c]
    except Exception:  # noqa: BLE001
        return None, r, c, ignored_latlon
    values: list[float | None] = []
    for x in column:
        try:
            v = float(x)
        except (TypeError, ValueError):
            values.append(None)
            continue
        values.append(v if v == v else None)  # NaN → None
    return values, r, c, ignored_latlon


def _slice_png(h5py, f, slice_index: int):
    obj = f.get("timeseries")
    if not isinstance(obj, h5py.Dataset) or obj.ndim != 3:
        return None, None, None, None
    try:
        from insar_agent.preview.hdf5_view import _render_plane
    except Exception:  # noqa: BLE001
        return None, None, None, None
    try:
        return _render_plane(obj, slice_index)
    except Exception:  # noqa: BLE001
        return None, None, None, None


def _read_dates(h5py, f) -> tuple[int, list[str], bool]:
    dset = None
    for key in ("date", "dates"):
        obj = f.get(key)
        if isinstance(obj, h5py.Dataset):
            dset = obj
            break
    if dset is None:
        return 0, [], False
    if not dset.shape:
        return 1, [_decode_date(dset[()])], False
    n = int(dset.shape[0])
    raw = dset[: min(n, _DATE_CAP)]
    dates = [_decode_date(v) for v in raw]
    return n, dates, n > _DATE_CAP


def _decode_date(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace").strip("\x00").strip()
    return str(value).strip()
