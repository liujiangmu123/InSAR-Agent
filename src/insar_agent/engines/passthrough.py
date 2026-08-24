"""规范路径物化:硬链接优先、拷贝兜底(零决策)。

与 engines/mintpy.py 的区别:那个跑 smallbaselineApp 分段,这个只把已存在的
真实文件落到分析链的规范位置。register_sources(第 20 步)是同一构建器的
双源形态:params.primary(与非空的 params.secondary)→ analysis/source.*
(与 source_2.*)。后续 passthrough 按步骤把规范输入物化为规范输出。

register_sources 允许的扩展名(大小写不敏感):.h5 .hdf5 .tif .tiff .csv。
.h5/.hdf5 物化到 analysis/source.h5(下游 MintPy 惯例)。.tif/.tiff/.csv
诚实登记:GeoTIFF 硬链到 analysis/source.tif 并写 manifest;有 rasterio 时
把真实像元写入 analysis/source.h5 的 velocity,没有 rasterio/gdal 则不假装
地理参考、不编造栅格数值。CSV 按原列写出 analysis/source.csv,列名只做
启发式识别,不发明列。不支持的扩展名显式 ValueError,绝不静默当 h5。

红线:绝不走 simulate —— 物化的是真实文件,不是合成产物。
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from insar_agent.registry.model import Capability
from insar_agent.runtime.jobs import CommandPlan, wrapper_python

_OUT_DIR = "analysis"

_H5_EXT = frozenset({".h5", ".hdf5"})
_TIF_EXT = frozenset({".tif", ".tiff"})
_CSV_EXT = frozenset({".csv"})
SUPPORTED_SOURCE_EXT = _H5_EXT | _TIF_EXT | _CSV_EXT

# cap.id → ((src_rel, dst_rel, required), ...); 次源 required=False,存在才物化
_PASSTHROUGH_PAIRS: dict[int, tuple[tuple[str, str, bool], ...]] = {
    21: (("analysis/source.h5", "analysis/masked.h5", True),
         ("analysis/source_2.h5", "analysis/masked_2.h5", False)),
    22: (("analysis/masked.h5", "analysis/corrected.h5", True),
         ("analysis/masked_2.h5", "analysis/corrected_2.h5", False)),
    23: (("analysis/corrected.h5", "analysis/decomposed.h5", True),),
    25: (),
    26: (("analysis/decomposed.h5", "analysis/change.h5", True),),
    27: (),
    28: (),
}

# cap.id → JSON 声明产物(跳过科学动作时仍留下可审计文件)
_PASSTHROUGH_MARKERS: dict[int, tuple[tuple[str, dict], ...]] = {
    25: (("analysis/figures/passthrough.json",
          {"method": "passthrough", "reason": "本次分析不出图"}),),
    26: (("analysis/change_summary.json",
          {"method": "passthrough", "significant_fraction": None,
           "note": "本次分析不做变化检测"}),),
    27: (("analysis/prediction.json",
          {"status": "passthrough",
           "assumptions": ["no extrapolation requested (passthrough)"],
           "not_a_forecast_of": [
               "earthquake occurrence or timing",
               "landslide failure time",
               "new coseismic or outburst step events",
           ],
           "ci": {"level": None, "lower": None, "upper": None,
                  "note": "passthrough: no prediction computed"},
           "note": "本次分析不做外推,prediction.json 仅为规范链占位"}),),
    28: (("analysis/bridge/passthrough.json",
          {"method": "passthrough", "reason": "本次分析不导出反演桥"}),),
}

_MATERIALIZE_PY = '''\
# insar-agent 规范路径物化(真实文件硬链接/拷贝,零决策)
import hashlib, json, os, shutil, sys
from pathlib import Path

PAIRS = {pairs!r}  # [(src, dst, required), ...]
MARKERS = {markers!r}  # [(relpath, payload), ...]

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def materialize(src: Path, dst: Path) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        os.link(src, dst)
        return "hardlink"
    except OSError:
        shutil.copy2(src, dst)
        return "copy"

failed = False
for src_s, dst_s, required in PAIRS:
    src, dst = Path(src_s), Path(dst_s)
    if not src.is_file():
        msg = f"源文件不存在: {{src}}"
        if required:
            print("ERROR:", msg, flush=True)
            failed = True
        else:
            print("SKIP:", msg, flush=True)
        continue
    mode = materialize(src, dst)
    digest = sha256_of(dst)
    sidecar = dst.with_name(dst.name + ".json")
    sidecar.write_text(json.dumps({{
        "source": str(src), "dest": str(dst), "sha256": digest, "mode": mode,
    }}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK {{mode}} {{src}} -> {{dst}} sha256={{digest[:12]}}", flush=True)

for rel, payload in MARKERS:
    path = Path(rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK marker {{path}}", flush=True)

sys.exit(2 if failed else 0)
'''

_REGISTER_PY = '''\
# insar-agent register_sources(h5/GeoTIFF/CSV;不编造数值)
import sys
from pathlib import Path

_src = Path(__SRC_ROOT_REPR__)
if _src.is_dir() and str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from insar_agent.engines.passthrough import run_register_sources

run_register_sources(Path(".").resolve(), __PRIMARY_REL__, __SECONDARY_REL__)
'''

# CSV 列名启发式(小写比对);不发明列,只识别已有名字
_CSV_LON = ("lon", "longitude", "long", "x")
_CSV_LAT = ("lat", "latitude", "y")
_CSV_VEL = ("velocity", "vel", "los_mm", "los", "vlos", "dlos")


def _safe_rel(workspace: Path, rel: str, label: str) -> Path:
    """相对路径解析 + 逃逸防御:结果必须仍在 workspace 内。"""
    raw = str(rel or "").strip()
    if not raw:
        raise ValueError(f"{label} 为空")
    p = Path(raw)
    if p.is_absolute() or p.drive:
        raise ValueError(f"{label} 必须是工作区相对路径:{rel}")
    base = workspace.resolve()
    target = (workspace / p).resolve()
    if target == base or not target.is_relative_to(base):
        raise ValueError(f"{label} 逃逸出工作区:{rel}")
    return target


def _rel_posix(workspace: Path, target: Path) -> str:
    return target.resolve().relative_to(workspace.resolve()).as_posix()


def _suffix(path: Path) -> str:
    return path.suffix.lower()


def _unsupported_ext_error(ext: str) -> ValueError:
    allowed = ", ".join(sorted(SUPPORTED_SOURCE_EXT))
    return ValueError(f"register_sources 不支持扩展名 {ext or '(无)'}(允许:{allowed})")


def _materialize_file(src: Path, dst: Path) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        os.link(src, dst)
        return "hardlink"
    except OSError:
        shutil.copy2(src, dst)
        return "copy"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def _register_h5(workspace: Path, src: Path, stem: str) -> dict:
    dst = workspace / _OUT_DIR / f"{stem}.h5"
    mode = _materialize_file(src, dst)
    manifest = {
        "format": "hdf5",
        "path": _rel_posix(workspace, dst),
        "source": _rel_posix(workspace, src),
        "mode": mode,
        "note": "HDF5 原样物化到 analysis/source.h5 惯例路径",
    }
    _write_json(workspace / _OUT_DIR / f"{stem}_manifest.json", manifest)
    print(f"OK {mode} {src} -> {dst}", flush=True)
    return manifest


def _try_geotiff_to_h5(src: Path, dst_h5: Path) -> dict:
    """用 rasterio 读真实像元写入 velocity;失败返回原因,绝不填随机数。"""
    try:
        import numpy as np
        import rasterio
    except ImportError:
        return {"ok": False, "reason": "rasterio 不可用,未写 source.h5,不假装地理参考"}
    try:
        import h5py
    except ImportError:
        return {"ok": False, "reason": "h5py 不可用,未写 source.h5"}
    try:
        with rasterio.open(src) as ds:
            arr = ds.read(1)
            crs = ds.crs
            transform = ds.transform
            units = (ds.units[0] if ds.units else None) or ""
            unit_unknown = not bool(units)
    except Exception as exc:  # 非标准/损坏 TIFF:不编数值,只保留硬链
        return {"ok": False,
                "reason": f"GeoTIFF 无法读取,未写 source.h5({type(exc).__name__})"}
    dst_h5.parent.mkdir(parents=True, exist_ok=True)
    if dst_h5.exists() or dst_h5.is_symlink():
        dst_h5.unlink()
    with h5py.File(dst_h5, "w") as f:
        f.create_dataset("velocity", data=np.asarray(arr))
        f["velocity"].attrs["UNIT"] = "m/yr" if not unit_unknown else "unknown"
        if crs is not None:
            f.attrs["EPSG"] = str(crs)
        f.attrs["X_FIRST"] = float(transform.c)
        f.attrs["Y_FIRST"] = float(transform.f)
        f.attrs["X_STEP"] = float(transform.a)
        f.attrs["Y_STEP"] = float(transform.e)
        f.attrs["FILE_TYPE"] = "velocity"
    return {
        "ok": True,
        "reason": "rasterio 读取真实像元写入 velocity",
        "unit_unknown": unit_unknown,
        "crs": None if crs is None else str(crs),
    }


def _register_geotiff(workspace: Path, src: Path, stem: str) -> dict:
    dst_tif = workspace / _OUT_DIR / f"{stem}.tif"
    mode = _materialize_file(src, dst_tif)
    h5_info = _try_geotiff_to_h5(src, workspace / _OUT_DIR / f"{stem}.h5")
    note = h5_info.get("reason") or "GeoTIFF 已登记"
    if h5_info.get("ok") and h5_info.get("crs") is None:
        note = note + ";GeoTIFF 无 CRS,未假装地理参考"
    manifest = {
        "format": "geotiff",
        "path": _rel_posix(workspace, dst_tif),
        "source": _rel_posix(workspace, src),
        "mode": mode,
        "h5_written": bool(h5_info.get("ok")),
        "unit_unknown": bool(h5_info.get("unit_unknown", True)),
        "note": note,
    }
    _write_json(workspace / _OUT_DIR / f"{stem}_manifest.json", manifest)
    print(f"OK {mode} {src} -> {dst_tif}", flush=True)
    print(f"manifest: {manifest['note']}", flush=True)
    return manifest


def _normalize_header(name: str) -> str:
    return "".join(ch for ch in str(name).strip().lower() if ch not in " \t")


def _map_csv_columns(headers: list[str]) -> dict[str, str | None]:
    """只映射已有列,不发明列名。"""
    found = {_normalize_header(h): h for h in headers}

    def pick(cands: tuple[str, ...]) -> str | None:
        for c in cands:
            if c in found:
                return found[c]
        return None

    return {"lon": pick(_CSV_LON), "lat": pick(_CSV_LAT), "velocity": pick(_CSV_VEL)}


def _register_csv(workspace: Path, src: Path, stem: str) -> dict:
    import csv

    text = src.read_text(encoding="utf-8-sig", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(text.splitlines(), dialect))
    if not rows:
        raise ValueError(f"CSV 为空:{src}")
    headers = [str(h) for h in rows[0]]
    mapping = _map_csv_columns(headers)
    dst = workspace / _OUT_DIR / f"{stem}.csv"
    mode = _materialize_file(src, dst)
    n_data = max(0, len(rows) - 1)
    mapped = mapping["lon"] and mapping["lat"]
    manifest = {
        "format": "csv",
        "path": _rel_posix(workspace, dst),
        "source": _rel_posix(workspace, src),
        "mode": mode,
        "columns": headers,
        "column_map": mapping,
        "n_rows": n_data,
        "note": "按原列写出,未发明列;"
                + ("已识别 lon/lat/velocity 启发式列" if mapped
                   else "启发式未识别 lon/lat,列保持原样"),
    }
    _write_json(workspace / _OUT_DIR / f"{stem}_manifest.json", manifest)
    print(f"OK {mode} {src} -> {dst} rows={n_data}", flush=True)
    return manifest


def register_one(workspace: Path, src: Path, stem: str) -> dict:
    """把一个源文件登记到 analysis/{stem}.* 。"""
    ext = _suffix(src)
    if ext in _H5_EXT:
        return _register_h5(workspace, src, stem)
    if ext in _TIF_EXT:
        return _register_geotiff(workspace, src, stem)
    if ext in _CSV_EXT:
        return _register_csv(workspace, src, stem)
    raise _unsupported_ext_error(ext)


def run_register_sources(workspace: Path, primary_rel: str,
                         secondary_rel: str = "") -> None:
    """作业脚本入口:缺文件 / 不支持扩展名 → 非 0。"""
    ws = Path(workspace).resolve()
    primary = (ws / primary_rel).resolve()
    if not primary.is_file():
        print(f"ERROR: register_sources 源不存在:{primary}", flush=True)
        raise SystemExit(2)
    ext = _suffix(primary)
    if ext not in SUPPORTED_SOURCE_EXT:
        print(f"ERROR: {_unsupported_ext_error(ext)}", flush=True)
        raise SystemExit(2)
    register_one(ws, primary, "source")
    if str(secondary_rel or "").strip():
        sec = (ws / secondary_rel).resolve()
        if not sec.is_file():
            print(f"ERROR: register_sources 次源不存在:{sec}", flush=True)
            raise SystemExit(2)
        if _suffix(sec) not in SUPPORTED_SOURCE_EXT:
            print(f"ERROR: {_unsupported_ext_error(_suffix(sec))}", flush=True)
            raise SystemExit(2)
        register_one(ws, sec, "source_2")
    print("register_sources 完成", flush=True)


def build(*, cap: Capability, method: str, params: dict[str, Any], run: dict,
          workspace: Path) -> CommandPlan:
    if method == "register_sources":
        primary = _safe_rel(workspace, str(params.get("primary") or ""), "primary")
        if not primary.is_file():
            raise FileNotFoundError(f"register_sources 源不存在:{primary}")
        ext = _suffix(primary)
        if ext not in SUPPORTED_SOURCE_EXT:
            raise _unsupported_ext_error(ext)
        primary_rel = _rel_posix(workspace, primary)
        secondary_rel = ""
        secondary = str(params.get("secondary") or "").strip()
        if secondary:
            sec = _safe_rel(workspace, secondary, "secondary")
            if not sec.is_file():
                raise FileNotFoundError(f"register_sources 次源不存在:{sec}")
            if _suffix(sec) not in SUPPORTED_SOURCE_EXT:
                raise _unsupported_ext_error(_suffix(sec))
            secondary_rel = _rel_posix(workspace, sec)
        src_root = Path(__file__).resolve().parents[2]
        script_rel = ".analysis/register_sources.py"
        script = (_REGISTER_PY
                  .replace("__SRC_ROOT_REPR__", repr(str(src_root)))
                  .replace("__PRIMARY_REL__", repr(primary_rel))
                  .replace("__SECONDARY_REL__", repr(secondary_rel)))
        return CommandPlan(
            argv=[wrapper_python(), "-X", "utf8", script_rel],
            cwd=str(workspace),
            env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
            files={script_rel: script},
            shell_line=f"python {script_rel}",
        )

    if method == "passthrough":
        if cap.id not in _PASSTHROUGH_PAIRS and cap.id not in _PASSTHROUGH_MARKERS:
            raise ValueError(f"passthrough 无此步骤的规范链:{cap.id}")
        declared = _PASSTHROUGH_PAIRS.get(cap.id, ())
        pairs = []
        for src_rel, dst_rel, required in declared:
            src = workspace / src_rel
            if required and not src.is_file():
                raise FileNotFoundError(f"passthrough 规范输入不存在:{src_rel}")
            pairs.append((src_rel, dst_rel, required))
        markers = list(_PASSTHROUGH_MARKERS.get(cap.id, ()))
    else:
        raise ValueError(f"passthrough 无此方法:{method}")

    script_rel = ".analysis/passthrough.py"
    return CommandPlan(
        argv=[wrapper_python(), "-X", "utf8", script_rel],
        cwd=str(workspace),
        env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        files={script_rel: _MATERIALIZE_PY.format(pairs=pairs, markers=markers)},
        shell_line=f"python {script_rel}",
    )
