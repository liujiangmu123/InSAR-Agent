"""MATLAB .mat 预览:变量表 + 可选小 2D PNG。不 exec MATLAB,不编造数组。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from insar_agent.preview.dispatch import Preview, current_opts, register

MAX_VARS = 80
MAX_PNG_EDGE = 1024
MAX_PNG_ELEMS = MAX_PNG_EDGE * MAX_PNG_EDGE

_SKIP_NAMES = frozenset({
    "__header__", "__version__", "__globals__", "#refs#", "#subsystem#",
})
_MATLAB_NUMERIC = frozenset({
    "double", "single", "logical",
    "int8", "int16", "int32", "int64",
    "uint8", "uint16", "uint32", "uint64",
})
_NOTE = "变量表 + 可选小图;未整载大数组"
_NOTE_MISSING = "未安装 scipy,无法预览 .mat"
_HDF5_SIG = b"\x89HDF\r\n\x1a\n"


def _import_scipy_io():
    try:
        from scipy import io as sio
        return sio
    except Exception:  # noqa: BLE001
        return None


def _import_h5py():
    try:
        import h5py
        return h5py
    except Exception:  # noqa: BLE001
        return None


def _missing_lib() -> Preview:
    return Preview(
        kind="unsupported",
        payload={"reason": "missing_mat_lib"},
        note=_NOTE_MISSING,
    )


def _cannot_open() -> Preview:
    return Preview(
        kind="unsupported",
        payload={"reason": "cannot_open"},
        note="无法打开 .mat",
    )


def _looks_raw_hdf5(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(8) == _HDF5_SIG
    except OSError:
        return False


def _shape_list(shape: object) -> list[int]:
    try:
        return [int(s) for s in shape]  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        return []


def _dtype_numeric(dtype: str) -> bool:
    key = dtype.lower().split("[", 1)[0].strip().lstrip("<>|=")
    if key in _MATLAB_NUMERIC:
        return True
    return any(token in key for token in ("float", "int", "uint", "bool"))


def _skip_name(name: str) -> bool:
    return name in _SKIP_NAMES or name.startswith("#")


def _pick_2d_numeric(variables: list[dict[str, Any]], wanted: str | None) -> str | None:
    names = [str(item["name"]) for item in variables]
    if wanted:
        for name in names:
            if name == wanted:
                return name
    for item in variables:
        shape = item["shape"]
        if (
            len(shape) == 2
            and int(shape[0]) > 0
            and int(shape[1]) > 0
            and _dtype_numeric(str(item["dtype"]))
        ):
            return str(item["name"])
    return None


def _array_png(data: Any) -> bytes | None:
    try:
        from insar_agent.preview.array_png import array_to_png
    except Exception:  # noqa: BLE001
        return None
    kind = getattr(getattr(data, "dtype", None), "kind", "")
    if kind not in "biuf":
        return None
    return array_to_png(data)


def _ok(variables: list[dict[str, Any]], truncated: bool, png: bytes | None) -> Preview:
    return Preview(
        kind="mat",
        payload={"variables": variables},
        png=png,
        truncated=truncated,
        note=_NOTE,
    )


def _preview_scipy(path: Path) -> Preview | None:
    sio = _import_scipy_io()
    if sio is None:
        return None
    try:
        listing = sio.whosmat(str(path))
    except NotImplementedError:
        return None
    except Exception:  # noqa: BLE001 — 交 h5py / cannot_open
        return None
    if listing is None:
        return None

    variables: list[dict[str, Any]] = []
    truncated = False
    for name, shape, dtype in listing:
        key = str(name)
        if _skip_name(key):
            continue
        if len(variables) >= MAX_VARS:
            truncated = True
            break
        variables.append({
            "name": key,
            "shape": _shape_list(shape),
            "dtype": str(dtype),
        })
    if not truncated:
        n_keep = sum(1 for name, _shape, _dt in listing if not _skip_name(str(name)))
        truncated = n_keep > len(variables)

    wanted = current_opts().dataset
    chosen = _pick_2d_numeric(variables, wanted)
    png: bytes | None = None
    if chosen:
        item = next(v for v in variables if v["name"] == chosen)
        shape = item["shape"]
        if len(shape) == 2:
            n_elem = int(shape[0]) * int(shape[1])
            if 0 < n_elem <= MAX_PNG_ELEMS:
                try:
                    loaded = sio.loadmat(
                        str(path),
                        variable_names=[chosen],
                        squeeze_me=False,
                    )
                    png = _array_png(loaded.get(chosen))
                except Exception:  # noqa: BLE001 — 列表仍有效
                    png = None
    return _ok(variables, truncated, png)


def _h5_class(obj: Any) -> str | None:
    try:
        raw = obj.attrs.get("MATLAB_class")
    except Exception:  # noqa: BLE001
        return None
    if raw is None:
        return None
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", "replace")
    else:
        text = str(raw)
    text = text.strip()
    return text or None


def _h5_variables(hf: Any, h5py: Any) -> tuple[list[dict[str, Any]], bool]:
    try:
        keys = [str(k) for k in hf.keys()]
    except Exception:  # noqa: BLE001
        return [], False
    kept = [k for k in keys if not _skip_name(k)]
    truncated = len(kept) > MAX_VARS
    variables: list[dict[str, Any]] = []
    for key in kept[:MAX_VARS]:
        try:
            obj = hf[key]
        except Exception:  # noqa: BLE001
            continue
        cls = _h5_class(obj)
        if isinstance(obj, h5py.Dataset):
            dtype = cls or str(obj.dtype)
            shape = _shape_list(obj.shape)
        else:
            dtype = cls or "group"
            shape = []
        variables.append({"name": key, "shape": shape, "dtype": dtype})
    return variables, truncated


def _strided_2d(ds: Any):
    if int(getattr(ds, "ndim", 0)) != 2:
        return None
    h, w = (int(s) for s in ds.shape)
    if h <= 0 or w <= 0:
        return None
    step = max(1, (max(h, w) + MAX_PNG_EDGE - 1) // MAX_PNG_EDGE)
    return ds[::step, ::step]


def _preview_h5py(path: Path) -> Preview | None:
    h5py = _import_h5py()
    if h5py is None:
        return None
    try:
        hf = h5py.File(path, "r")
    except Exception:  # noqa: BLE001
        return None
    try:
        variables, truncated = _h5_variables(hf, h5py)
        wanted = current_opts().dataset
        chosen = _pick_2d_numeric(variables, wanted)
        png: bytes | None = None
        if chosen is not None:
            try:
                ds = hf[chosen]
                kind = getattr(getattr(ds, "dtype", None), "kind", "")
                if kind in "biuf":
                    plane = _strided_2d(ds)
                    if plane is not None:
                        png = _array_png(plane)
            except Exception:  # noqa: BLE001 — 列表仍有效
                png = None
        return _ok(variables, truncated, png)
    finally:
        hf.close()


@register(".mat")
def preview_mat(path: Path) -> Preview:
    sio = _import_scipy_io()
    h5py = _import_h5py()
    if sio is None and h5py is None:
        return _missing_lib()

    if _looks_raw_hdf5(path):
        got = _preview_h5py(path)
        if got is not None:
            return got

    got = _preview_scipy(path)
    if got is not None:
        return got

    got = _preview_h5py(path)
    if got is not None:
        return got

    if sio is None:
        return _missing_lib()
    return _cannot_open()
