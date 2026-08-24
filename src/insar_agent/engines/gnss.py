"""GNSS 对比(第 11 步 gnss_compare):读站点 CSV,与 InSAR 速度场最近像元比 RMSE。

不自造平差、不发明坐标。velocity.h5 必须带地理编码(X_FIRST/Y_FIRST/X_STEP/Y_STEP)
或 mintpy/inputs/geometryGeo.h5 的 lat/lon 栅格;没有则诚实失败。

CSV 列启发式:lon/lat + los_mm,或 ve/vn/vu。ENU 只有在同一行(或参数)提供
入射角/方位角时才投影到 LOS;缺几何则失败,不假设默认入射角。

los_mm 按 mm/yr 与 InSAR velocity(m/yr×1000)对比 —— 本方法不做历时换算,
不把位移冒充速率。站点数 < 3 仍计算 RMSE,但不作为硬门。
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

from insar_agent.registry.model import Capability
from insar_agent.runtime.jobs import CommandPlan, wrapper_python

_QA_REL = "products/report/qa.json"
_GNSS_REL = "products/report/gnss.json"
_RESIDUALS_REL = "products/report/gnss_residuals.csv"
_RESIDUAL_FIELDS = ("name", "lat", "lon", "los_mm", "insar_mm", "residual_mm")

_SCRIPT = '''\
# insar-agent GNSS 对比(指标来自产物与 CSV,绝不编造)
import sys
from pathlib import Path

_src = Path(__SRC_ROOT_REPR__)
if _src.is_dir() and str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from insar_agent.engines.gnss import run_gnss_compare

run_gnss_compare(Path(".").resolve(), __GNSS_CSV__)
'''


def _norm(name: str) -> str:
    return "".join(ch for ch in str(name).strip().lower() if ch not in " \t_")


def _pick(headers: dict[str, str], cands: tuple[str, ...]) -> str | None:
    for c in cands:
        if c in headers:
            return headers[c]
    return None


def _attr_float(attrs: dict, key: str) -> float | None:
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


def enu_to_los(ve: float, vn: float, vu: float, inc_deg: float, az_deg: float) -> float:
    """ENU → LOS,口径对齐 MintPy ut.enu2los(同单位进、同单位出)。"""
    inc = math.radians(inc_deg)
    az = math.radians(az_deg)
    return (-ve * math.sin(inc) * math.sin(az)
            + vn * math.sin(inc) * math.cos(az)
            + vu * math.cos(inc))


def _load_geo_from_velocity(path: Path) -> tuple[Any, dict] | None:
    import h5py
    import numpy as np

    with h5py.File(path, "r") as f:
        if "velocity" not in f:
            raise KeyError(f"{path} 无数据集 velocity")
        vel = np.asarray(f["velocity"][()], dtype="float64")
        attrs = {k: f.attrs[k] for k in f.attrs}
        x0 = _attr_float(attrs, "X_FIRST")
        y0 = _attr_float(attrs, "Y_FIRST")
        dx = _attr_float(attrs, "X_STEP")
        dy = _attr_float(attrs, "Y_STEP")
        if None not in (x0, y0, dx, dy) and dx != 0 and dy != 0:
            return vel, {"kind": "affine", "x0": x0, "y0": y0, "dx": dx, "dy": dy}
    return None


def _load_geo_from_geometry(path: Path, shape: tuple[int, ...]) -> dict | None:
    import h5py
    import numpy as np

    if not path.is_file():
        return None
    with h5py.File(path, "r") as f:
        attrs = {k: f.attrs[k] for k in f.attrs}
        x0 = _attr_float(attrs, "X_FIRST")
        y0 = _attr_float(attrs, "Y_FIRST")
        dx = _attr_float(attrs, "X_STEP")
        dy = _attr_float(attrs, "Y_STEP")
        if None not in (x0, y0, dx, dy) and dx != 0 and dy != 0:
            return {"kind": "affine", "x0": x0, "y0": y0, "dx": dx, "dy": dy}
        if "latitude" in f and "longitude" in f:
            lat = np.asarray(f["latitude"][()], dtype="float64")
            lon = np.asarray(f["longitude"][()], dtype="float64")
            if lat.shape[:2] != shape[:2] or lon.shape[:2] != shape[:2]:
                return None
            return {"kind": "grids", "lat": lat, "lon": lon}
    return None


def _nearest_pixel(geo: dict, shape: tuple[int, ...], lat: float, lon: float
                   ) -> tuple[int, int] | None:
    nrow, ncol = int(shape[0]), int(shape[1])
    if geo["kind"] == "affine":
        rf = (lat - geo["y0"]) / geo["dy"]
        cf = (lon - geo["x0"]) / geo["dx"]
        if not (-0.5 <= rf <= nrow - 0.5 and -0.5 <= cf <= ncol - 0.5):
            return None
        r = min(nrow - 1, max(0, int(round(rf))))
        c = min(ncol - 1, max(0, int(round(cf))))
        return r, c
    import numpy as np

    latg = geo["lat"]
    long = geo["lon"]
    dist = (latg - lat) ** 2 + (long - lon) ** 2
    idx = int(np.nanargmin(dist))
    r, c = divmod(idx, ncol) if latg.ndim == 2 else (0, idx)
    if r >= nrow or c >= ncol:
        return None
    return r, c


def _read_stations(csv_path: Path) -> list[dict]:
    text = csv_path.read_text(encoding="utf-8-sig", errors="replace")
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        raise ValueError(f"GNSS CSV 为空:{csv_path}")
    raw_headers = [str(h) for h in rows[0]]
    headers = {_norm(h): h for h in raw_headers}
    col_lon = _pick(headers, ("lon", "longitude", "long"))
    col_lat = _pick(headers, ("lat", "latitude"))
    col_name = _pick(headers, ("name", "station", "site", "sta", "id",
                               "stationname", "sitename"))
    col_los = _pick(headers, ("losmm", "los", "losvel", "vlos", "dlos"))
    col_ve = _pick(headers, ("ve", "east", "e"))
    col_vn = _pick(headers, ("vn", "north", "n"))
    col_vu = _pick(headers, ("vu", "up", "u"))
    col_inc = _pick(headers, ("inc", "incidence", "incdeg", "incangle"))
    col_az = _pick(headers, ("az", "azimuth", "azdeg", "heading", "azangle"))
    if not col_lon or not col_lat:
        raise ValueError(f"GNSS CSV 缺 lon/lat 列(实际列:{raw_headers})")
    idx = {name: raw_headers.index(name) for name in raw_headers}

    def cell(row: list[str], col: str | None) -> float | None:
        if not col:
            return None
        i = idx[col]
        if i >= len(row) or str(row[i]).strip() == "":
            return None
        try:
            v = float(row[i])
        except ValueError:
            return None
        return v if math.isfinite(v) else None

    def cell_str(row: list[str], col: str | None) -> str:
        if not col:
            return ""
        i = idx[col]
        if i >= len(row):
            return ""
        return str(row[i]).strip()

    stations: list[dict] = []
    for row in rows[1:]:
        if not row or all(not str(x).strip() for x in row):
            continue
        lat = cell(row, col_lat)
        lon = cell(row, col_lon)
        if lat is None or lon is None:
            continue
        los = cell(row, col_los)
        ve, vn, vu = cell(row, col_ve), cell(row, col_vn), cell(row, col_vu)
        inc, az = cell(row, col_inc), cell(row, col_az)
        stations.append({
            "name": cell_str(row, col_name),
            "lat": lat, "lon": lon, "los_mm": los,
            "ve": ve, "vn": vn, "vu": vu, "inc": inc, "az": az,
        })
    if not stations:
        raise ValueError(f"GNSS CSV 无有效站点行:{csv_path}")
    return stations


def _station_los_mm(st: dict) -> float:
    if st.get("los_mm") is not None:
        return float(st["los_mm"])
    if None not in (st.get("ve"), st.get("vn"), st.get("vu")):
        if st.get("inc") is None or st.get("az") is None:
            raise ValueError(
                "GNSS CSV 只有 ve/vn/vu,缺入射角/方位角,拒绝用默认几何投影 LOS")
        return enu_to_los(st["ve"], st["vn"], st["vu"], st["inc"], st["az"])
    raise ValueError("GNSS 站点缺 los_mm,且无法从 ve/vn/vu 投影 LOS")


def _write_residual_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_RESIDUAL_FIELDS, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in _RESIDUAL_FIELDS})


def run_gnss_compare(workspace: Path, gnss_csv: str) -> dict:
    """写 products/report/gnss.json,并增量合并进 qa.json(若已存在)。"""
    import numpy as np

    ws = Path(workspace).resolve()
    csv_path = ws / gnss_csv
    if not csv_path.is_file():
        print(f"ERROR: GNSS CSV 不存在:{csv_path}", flush=True)
        raise FileNotFoundError(f"GNSS CSV 不存在:{csv_path}")

    vel_path = ws / "mintpy" / "velocity.h5"
    if not vel_path.is_file():
        vel_path = ws / "velocity.h5"
    if not vel_path.is_file():
        print("ERROR: mintpy/velocity.h5 不存在", flush=True)
        raise FileNotFoundError("velocity.h5 不存在")

    loaded = _load_geo_from_velocity(vel_path)
    if loaded is None:
        import h5py

        with h5py.File(vel_path, "r") as f:
            if "velocity" not in f:
                raise KeyError(f"{vel_path} 无数据集 velocity")
            vel = np.asarray(f["velocity"][()], dtype="float64")
        geo = _load_geo_from_geometry(ws / "mintpy" / "inputs" / "geometryGeo.h5",
                                      vel.shape)
        if geo is None:
            print("ERROR: velocity.h5 无 X_FIRST/Y_FIRST 且 geometryGeo 不可用,"
                  "拒绝编造坐标", flush=True)
            raise ValueError("velocity.h5 缺少地理编码,无法做 GNSS 最近像元对比")
    else:
        vel, geo = loaded

    stations = _read_stations(csv_path)
    residuals: list[float] = []
    residual_rows: list[dict] = []
    used = 0
    skipped = 0
    for st in stations:
        try:
            los_mm = _station_los_mm(st)
        except ValueError as exc:
            print(f"ERROR: {exc}", flush=True)
            raise
        pix = _nearest_pixel(geo, vel.shape, st["lat"], st["lon"])
        if pix is None:
            skipped += 1
            continue
        r, c = pix
        insar = vel[r, c]
        if not np.isfinite(insar):
            skipped += 1
            continue
        insar_mm = float(insar) * 1000.0
        residual_mm = insar_mm - los_mm
        residuals.append(residual_mm)
        residual_rows.append({
            "name": st.get("name") or "",
            "lat": st["lat"],
            "lon": st["lon"],
            "los_mm": los_mm,
            "insar_mm": insar_mm,
            "residual_mm": residual_mm,
        })
        used += 1

    n = used
    if n == 0:
        rmse = None
        status = "no_overlap"
    else:
        rmse = float(math.sqrt(sum(x * x for x in residuals) / n))
        status = "ok" if n >= 3 else "n_lt_3"
    out = {
        "method": "gnss_compare",
        "simulated": False,
        "gnss_rmse_mm": None if rmse is None else round(rmse, 4),
        "n_stations": n,
        "n_skipped": skipped,
        "status": status,
        "gnss_csv": gnss_csv,
        "residuals_csv": _RESIDUALS_REL,
        "note": "站点数 < 3 仍计算 RMSE,不作为硬门;los_mm 按 mm/yr 与 velocity×1000 对比",
    }
    gnss_path = ws / _GNSS_REL
    gnss_path.parent.mkdir(parents=True, exist_ok=True)
    _write_residual_csv(ws / _RESIDUALS_REL, residual_rows)
    gnss_path.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    qa_path = ws / _QA_REL
    qa: dict[str, Any] = {}
    if qa_path.is_file():
        try:
            qa = json.loads(qa_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            qa = {}
    qa.update({
        "gnss_rmse_mm": out["gnss_rmse_mm"],
        "n_stations": out["n_stations"],
        "gnss_status": out["status"],
    })
    qa_path.parent.mkdir(parents=True, exist_ok=True)
    qa_path.write_text(json.dumps(qa, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"gnss_compare: RMSE={out['gnss_rmse_mm']} mm  n={n} status={status}",
          flush=True)
    print(f"wrote {gnss_path}", flush=True)
    return out


def _safe_rel(workspace: Path, rel: str, label: str) -> Path:
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


def build(*, cap: Capability, method: str, params: dict[str, Any], run: dict,
          workspace: Path) -> CommandPlan:
    if method != "gnss_compare":
        raise ValueError(f"gnss 无此方法:{method}")
    csv_rel = str(params.get("gnss_csv") or "").strip()
    if not csv_rel:
        raise ValueError("gnss_compare 需要 params.gnss_csv(相对工作区的 GNSS CSV)")
    csv_path = _safe_rel(workspace, csv_rel, "gnss_csv")
    if not csv_path.is_file():
        raise FileNotFoundError(f"GNSS CSV 不存在:{csv_path}")
    src_root = Path(__file__).resolve().parents[2]
    script_rel = ".report/run_gnss.py"
    rel = csv_path.resolve().relative_to(workspace.resolve()).as_posix()
    script = (_SCRIPT
              .replace("__SRC_ROOT_REPR__", repr(str(src_root)))
              .replace("__GNSS_CSV__", repr(rel)))
    return CommandPlan(
        argv=[wrapper_python(), "-X", "utf8", script_rel],
        cwd=str(workspace),
        env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        files={script_rel: script},
        shell_line=f"python {script_rel}",
    )
