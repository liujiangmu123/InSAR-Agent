"""HDF5 预览:结构 + 选中数据集的降采样切片图。绝不整读 3D 立方体。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from insar_agent.preview.dispatch import Preview, current_opts, register

MAX_NODES = 80
MAX_ATTR_KEYS = 40
MAX_ATTR_CHARS = 200
MAX_PNG_EDGE = 1024

_NOTE_STRUCT = "结构 + 降采样切片;立方体未整载"


def _attr_to_str(value: object) -> str:
    if isinstance(value, bytes):
        text = value.decode("utf-8", "replace")
    else:
        text = str(value)
    if len(text) > MAX_ATTR_CHARS:
        return text[:MAX_ATTR_CHARS]
    return text


def _file_attrs(hf: Any) -> tuple[dict[str, str], bool]:
    out: dict[str, str] = {}
    try:
        keys = list(hf.attrs.keys())
    except Exception:  # noqa: BLE001
        return out, False
    truncated = len(keys) > MAX_ATTR_KEYS
    for key in keys[:MAX_ATTR_KEYS]:
        try:
            out[str(key)] = _attr_to_str(hf.attrs[key])
        except Exception:  # noqa: BLE001
            continue
    return out, truncated


def _walk_datasets(hf: Any) -> tuple[list[dict[str, Any]], bool]:
    import h5py

    datasets: list[dict[str, Any]] = []
    n = 0
    truncated = False

    def visit(group: Any) -> None:
        nonlocal n, truncated
        try:
            keys = list(group.keys())
        except Exception:  # noqa: BLE001
            return
        for key in keys:
            if n >= MAX_NODES:
                truncated = True
                return
            n += 1
            try:
                obj = group[key]
            except Exception:  # noqa: BLE001
                continue
            if isinstance(obj, h5py.Dataset):
                datasets.append({
                    "path": str(obj.name),
                    "shape": [int(s) for s in obj.shape],
                    "dtype": str(obj.dtype),
                })
            elif isinstance(obj, h5py.Group):
                visit(obj)
                if truncated:
                    return

    visit(hf)
    return datasets, truncated


def _norm_name(path: str) -> str:
    return path.strip("/").lower()


def _pick_dataset(datasets: list[dict[str, Any]], wanted: str | None) -> str | None:
    names = [str(item["path"]) for item in datasets]
    if wanted:
        for name in names:
            if (name == wanted or name.rstrip("/") == wanted.rstrip("/")
                    or name.endswith("/" + wanted.lstrip("/"))):
                return name
        for name in names:
            if _norm_name(name).endswith(_norm_name(wanted)):
                return name
    prefer = ("velocity", "wrappedphase", "unwrapphase", "coherence", "temporalcoherence", "mask")
    for key in prefer:
        for item in datasets:
            shape = item["shape"]
            if 2 <= len(shape) <= 3 and _norm_name(str(item["path"])).endswith(key):
                return str(item["path"])
    for item in datasets:
        if len(item["shape"]) == 2 and item["shape"][0] > 0 and item["shape"][1] > 0:
            return str(item["path"])
    for item in datasets:
        if len(item["shape"]) == 3:
            return str(item["path"])
    return names[0] if names else None


def _plane_map(ds: Any) -> dict[str, int] | None:
    """原数据平面高宽,不是 PNG 预览尺寸。"""
    try:
        shape = tuple(int(s) for s in ds.shape)
    except Exception:  # noqa: BLE001
        return None
    if len(shape) == 2:
        h, w = shape
    elif len(shape) == 3:
        _n, h, w = shape
    else:
        return None
    if h <= 0 or w <= 0:
        return None
    return {"height": h, "width": w}


def _strided_plane(ds: Any, slice_index: int):
    ndim = int(getattr(ds, "ndim", 0))
    if ndim == 2:
        h, w = (int(s) for s in ds.shape)
        if h <= 0 or w <= 0:
            return None, 0
        step = max(1, (max(h, w) + MAX_PNG_EDGE - 1) // MAX_PNG_EDGE)
        return ds[::step, ::step], 0
    if ndim == 3:
        n, h, w = (int(s) for s in ds.shape)
        if n <= 0 or h <= 0 or w <= 0:
            return None, 0
        idx = min(max(0, slice_index), n - 1)
        step = max(1, (max(h, w) + MAX_PNG_EDGE - 1) // MAX_PNG_EDGE)
        return ds[idx, ::step, ::step], idx
    return None, 0


def _render_plane(
    ds: Any, slice_index: int,
) -> tuple[bytes | None, int | None, dict | None, dict[str, int] | None]:
    map_hw = _plane_map(ds)
    kind = getattr(getattr(ds, "dtype", None), "kind", "")
    if kind not in "biufc":
        return None, None, None, map_hw
    try:
        from insar_agent.preview.array_png import array_to_png, plane_stats
    except Exception:  # noqa: BLE001
        return None, None, None, map_hw
    plane, idx = _strided_plane(ds, slice_index)
    if plane is None:
        return None, None, None, map_hw
    return array_to_png(plane), idx, plane_stats(plane), map_hw


@register(".h5", ".hdf5", ".he5")
def preview_hdf5(path: Path) -> Preview:
    try:
        from insar_agent.preview.timeseries_view import preview_if_timeseries
    except Exception:  # noqa: BLE001 — 时序模块缺席时仍走 HDF5 结构预览
        preview_if_timeseries = lambda p: None  # noqa: E731

    ts = preview_if_timeseries(path)
    if ts is not None:
        return ts

    try:
        import h5py
    except Exception:  # noqa: BLE001
        return Preview(
            kind="unsupported",
            payload={"reason": "missing_h5py"},
            note="未安装 h5py,无法列出 HDF5 结构",
        )

    opts = current_opts()
    with h5py.File(path, "r") as hf:
        datasets, nodes_cut = _walk_datasets(hf)
        attrs, attrs_cut = _file_attrs(hf)
        chosen = _pick_dataset(datasets, opts.dataset)
        png: bytes | None = None
        slice_used: int | None = None
        stats: dict | None = None
        map_hw: dict[str, int] | None = None
        if chosen is not None:
            try:
                png, slice_used, stats, map_hw = _render_plane(hf[chosen], opts.slice)
            except Exception:  # noqa: BLE001 — 切片失败仍保留结构
                png, slice_used, stats, map_hw = None, None, None, None
        note = _NOTE_STRUCT
        if chosen and png is None:
            note = f"{_NOTE_STRUCT};所选数据集未渲染为图"
        payload: dict[str, Any] = {
            "datasets": datasets,
            "attrs": attrs,
            "dataset": chosen,
            "slice": slice_used,
        }
        if map_hw is not None:
            payload["map"] = map_hw
        if stats is not None:
            payload["stats"] = stats
        return Preview(
            kind="hdf5",
            payload=payload,
            png=png,
            truncated=nodes_cut or attrs_cut,
            note=note,
        )
