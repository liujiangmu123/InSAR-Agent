"""MintPy 后处理薄封装:独立 CLI 子命令(零决策)。

与 engines/mintpy.py 的区别:那个跑 smallbaselineApp 的分段区间,这个跑
asc_desc2horz_vert / mask / subset / plate_motion / spatial_average /
plot_transection 等独立命令。引擎 Python 解析复用 mintpy.engine_python()
(同一真值来源)。

纪律:本模块只拼 argv,不做任何方法/参数选择;路径一律相对 workspace 解析,
绝不接受绝对路径逃逸出工作区(路径面防御,与 export_router 同款)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from insar_agent.engines.mintpy import engine_python
from insar_agent.registry.model import Capability
from insar_agent.runtime.jobs import CommandPlan, shell_quote

_OUT_DIR = "analysis"
_BRIDGE = "analysis/bridge"
_FIGS = "analysis/figures"
_ENV = {
    "HDF5_USE_FILE_LOCKING": "FALSE",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
    "MPLBACKEND": "Agg",
}

# ITRF2014-PMM 闭集(拼写 Antartica 对齐 MintPy 上游)
_ITRF_PLATES = {
    "Antartica", "Arabia", "Australia", "Eurasia", "India", "Nazca",
    "NorthAmerica", "Nubia", "Pacific", "SouthAmerica", "Somalia",
}

ACCEL_SIGMA_RATIO = 2.0


def accel_significance(accel: np.ndarray, accel_std: np.ndarray,
                       threshold: float = ACCEL_SIGMA_RATIO) -> tuple[np.ndarray, np.ndarray]:
    """显著性 = |a|/σ_a ≥ threshold。返回 (mask, ratio)。"""
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.abs(accel) / accel_std
    sig = np.isfinite(ratio) & (ratio >= float(threshold))
    return sig, ratio


def summarize_accel(accel: np.ndarray, accel_std: np.ndarray, *,
                    threshold: float = ACCEL_SIGMA_RATIO,
                    atr: dict | None = None) -> dict:
    sig, ratio = accel_significance(accel, accel_std, threshold)
    valid = np.isfinite(accel)
    n = int(valid.sum())
    n_sig = int(sig.sum())
    abs_a = np.abs(accel)
    abs_a = np.where(valid, abs_a, np.nan)
    summary: dict[str, Any] = {
        "method": "quadratic_accel",
        "significance_threshold": float(threshold),
        "criterion": "|a|/sigma_a >= 2",
        "n_valid": n,
        "n_significant": n_sig,
        "significant_fraction": (n_sig / n) if n else 0.0,
    }
    if n == 0:
        summary["max_abs_accel"] = None
        return summary
    idx = int(np.nanargmax(abs_a))
    yx = np.unravel_index(idx, accel.shape)
    summary["max_abs_accel"] = float(abs_a[yx])
    summary["max_abs_accel_yx"] = [int(yx[0]), int(yx[1])]
    summary["max_abs_accel_ratio"] = float(ratio[yx]) if np.isfinite(ratio[yx]) else None
    atr = atr or {}
    if all(k in atr for k in ("X_FIRST", "Y_FIRST", "X_STEP", "Y_STEP")):
        x0, y0, dx, dy = (float(atr[k]) for k in ("X_FIRST", "Y_FIRST", "X_STEP", "Y_STEP"))
        lat = y0 + dy * yx[0]
        lon = x0 + dx * yx[1]
        summary["max_abs_accel_lalo"] = [lat, lon]
    return summary


def _require_engine_python() -> str:
    from insar_agent.engines import ToolMissing
    py = engine_python()
    path = Path(py)
    if path.is_absolute() and not path.exists():
        raise ToolMissing(f"MintPy 引擎 Python 不存在:{py}")
    return py

_RUNNER = '''\
# insar-agent mintpy_post runner(零决策,argv 已烘焙)
import subprocess, sys
from pathlib import Path
Path("analysis").mkdir(parents=True, exist_ok=True)
JOBS = {jobs!r}
for argv in JOBS:
    print("RUN", " ".join(argv), flush=True)
    r = subprocess.run(argv)
    if r.returncode:
        sys.exit(r.returncode)
print("OK mintpy_post", flush=True)
'''

# spatial_average 不走 mintpy.cli:上游对 FILE_TYPE=velocity 把标量当列表写 txt
# (IndexError);saveList=False 仍无条件回读 SpatialAvg.txt(FileNotFoundError)。
# 在引擎 Python 里按 ut.spatial_average 的内存路径计算,结果写入 analysis/measure.json。
_SPATIAL_AVG_RUNNER = r'''
# insar-agent spatial_average runner(零决策;bypass mintpy.cli)
import json
import sys
from pathlib import Path

import numpy as np
from mintpy.utils import readfile
from mintpy.utils import utils1 as ut

FNAME = __FNAME__
DATASET = __DATASET__


def _as_json(v):
    if v is None:
        return None
    if isinstance(v, (bytes, np.bytes_)):
        return v.decode()
    if isinstance(v, (np.floating, float)):
        return float(v)
    if isinstance(v, (np.integer, int)):
        return int(v)
    return str(v)


def _spatial_average(fname, datasetName=None, maskFile=None, box=None,
                     saveList=False, **_ignored):
    """ut.spatial_average 内存路径:saveList=False,不写/不读 SpatialAvg.txt。"""
    atr = readfile.read_attribute(fname)
    k = atr.get("FILE_TYPE", "")
    if not box:
        box = (0, 0, int(atr["WIDTH"]), int(atr["LENGTH"]))
    if k == "ifgramStack":
        from mintpy.objects import ifgramStack
        obj = ifgramStack(fname)
        obj.open(print_msg=False)
        out = obj.spatial_average(datasetName=datasetName or "coherence",
                                  maskFile=maskFile, box=box)
        obj.close()
        return out
    if k == "timeseries":
        from mintpy.objects import timeseries
        return timeseries(fname).spatial_average(maskFile=maskFile, box=box)
    read_kw = {"box": box}
    if datasetName:
        read_kw["datasetName"] = datasetName
    data = readfile.read(fname, **read_kw)[0]
    return np.nanmean(data), None


def main():
    Path("analysis").mkdir(parents=True, exist_ok=True)
    ut.spatial_average = _spatial_average
    kwargs = {"saveList": False}
    if DATASET:
        kwargs["datasetName"] = DATASET
    mean_list, date_list = ut.spatial_average(FNAME, **kwargs)
    values = [float(x) for x in np.atleast_1d(mean_list).ravel().tolist()]
    if any(not np.isfinite(v) for v in values):
        raise RuntimeError("spatial_average 结果非有限值")
    atr = readfile.read_attribute(FNAME)
    ftype = str(atr.get("FILE_TYPE") or "")
    dates = None
    if date_list is not None and ftype in ("timeseries", "ifgramStack"):
        dates = [_as_json(x) for x in np.atleast_1d(date_list).ravel().tolist()]
    report = {
        "method": "spatial_average",
        "file": FNAME,
        "dataset": DATASET,
        "n": len(values),
        "values": values,
        "dates": dates,
    }
    if len(values) == 1:
        report["mean"] = values[0]
    unit = atr.get("UNIT")
    if unit not in (None, "", "None"):
        report["unit"] = _as_json(unit)
    text = json.dumps(report, ensure_ascii=False, allow_nan=False)
    Path("analysis/measure.json").write_text(text + "\n", encoding="utf-8")
    print(text, flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        sys.stderr.write(f"spatial_average failed: {exc}\n")
        sys.exit(1)
'''


def _spatial_average_runner(fname: str, dataset: str | None) -> str:
    return (_SPATIAL_AVG_RUNNER
            .replace("__FNAME__", repr(fname))
            .replace("__DATASET__", repr(dataset)))


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


def _rel(workspace: Path, target: Path) -> str:
    return target.resolve().relative_to(workspace.resolve()).as_posix()


def _find_geometry(workspace: Path) -> Path:
    for rel in ("mintpy/inputs/geometryRadar.h5", "mintpy/inputs/geometryGeo.h5"):
        cand = workspace / rel
        if cand.is_file():
            return cand
    raise FileNotFoundError(
        "板块运动改正需要几何文件 mintpy/inputs/geometryRadar.h5 或 "
        "geometryGeo.h5,未找到 —— 拒绝跳过改正装作做了")


def _split_pair(text: str, label: str, sep: str = ",") -> list[str]:
    parts = [p.strip() for p in str(text or "").replace(":", sep).split(sep) if p.strip()]
    if len(parts) != 2:
        raise ValueError(f"{label} 须为两个值(如 35.6:36.0 或 35.6,-117.9),得到:{text!r}")
    return parts


def _runner_plan(jobs: list[list[str]], workspace: Path) -> CommandPlan:
    script_rel = ".analysis/run_post.py"
    argv = [engine_python(), "-u", script_rel]
    return CommandPlan(
        argv=argv,
        cwd=str(workspace),
        env=dict(_ENV),
        files={script_rel: _RUNNER.format(jobs=jobs),
               f"{_OUT_DIR}/.keep": "# analysis output dir\n"},
        shell_line=" ".join(shell_quote(a) for a in argv),
    )


def _plate_jobs(workspace: Path, plate: str) -> list[list[str]]:
    geom = _rel(workspace, _find_geometry(workspace))
    py = engine_python()
    streams = [("analysis/masked.h5", "analysis/corrected.h5")]
    if (workspace / "analysis" / "masked_2.h5").is_file():
        streams.append(("analysis/masked_2.h5", "analysis/corrected_2.h5"))
    jobs: list[list[str]] = []
    for velo, out in streams:
        src = workspace / velo
        if not src.is_file():
            raise FileNotFoundError(f"plate_motion 输入不存在:{velo}")
        jobs.append([py, "-u", "-m", "mintpy.cli.plate_motion",
                     "-g", geom, "-v", velo, "--plate", plate, "-o", out])
    return jobs


def build(*, cap: Capability, method: str, params: dict[str, Any], run: dict,
          workspace: Path) -> CommandPlan:
    from insar_agent.engines import ToolMissing

    py = engine_python()

    if method == "plate_motion_itrf":
        plate = str(params.get("plate") or "").strip()
        if not plate:
            raise ValueError("plate_motion_itrf 要求 params.plate 必填"
                             "(ITRF2014-PMM 板块名,如 NorthAmerica)")
        if plate not in _ITRF_PLATES:
            raise ValueError(f"未知板块 {plate!r}(闭集:{sorted(_ITRF_PLATES)})")
        return _runner_plan(_plate_jobs(workspace, plate), workspace)

    if method == "asc_desc_horz_vert":
        primary = workspace / _OUT_DIR / "corrected.h5"
        secondary = workspace / _OUT_DIR / "corrected_2.h5"
        if not secondary.is_file():
            raise ValueError("升降轨分解需要双源:analysis/corrected_2.h5 不存在"
                             "(第 20 步 register_sources 是否给了 secondary?)")
        if not primary.is_file():
            raise FileNotFoundError("升降轨分解主源不存在:analysis/corrected.h5")
        argv = [py, "-u", "-m", "mintpy.cli.asc_desc2horz_vert",
                _rel(workspace, primary), _rel(workspace, secondary),
                "--az", str(params.get("horz_az_angle", -90.0)),
                "--oo", f"{_OUT_DIR}/decomposed.h5"]
        if params.get("use_geometry_files"):
            geom = _find_geometry(workspace)
            argv.extend(["-g", _rel(workspace, geom), _rel(workspace, geom)])
        return _runner_plan([argv], workspace)

    if method == "mask_by_coherence":
        src = workspace / _OUT_DIR / "source.h5"
        if not src.is_file():
            raise FileNotFoundError("mask 输入不存在:analysis/source.h5")
        mask = _safe_rel(workspace, str(params.get("mask_file") or "mintpy/maskTempCoh.h5"),
                         "mask_file")
        jobs = [[py, "-u", "-m", "mintpy.cli.mask", _rel(workspace, src),
                 "-m", _rel(workspace, mask), "-o", f"{_OUT_DIR}/masked.h5"]]
        src2 = workspace / _OUT_DIR / "source_2.h5"
        if src2.is_file():
            jobs.append([py, "-u", "-m", "mintpy.cli.mask", _rel(workspace, src2),
                         "-m", _rel(workspace, mask), "-o", f"{_OUT_DIR}/masked_2.h5"])
        return _runner_plan(jobs, workspace)

    if method == "subset_lalo":
        src = workspace / _OUT_DIR / "source.h5"
        if not src.is_file():
            raise FileNotFoundError("subset 输入不存在:analysis/source.h5")
        lat = _split_pair(str(params.get("subset_lat") or ""), "subset_lat", sep=":")
        lon = _split_pair(str(params.get("subset_lon") or ""), "subset_lon", sep=":")
        argv = [py, "-u", "-m", "mintpy.cli.subset", _rel(workspace, src),
                "--lat", lat[0], lat[1], "--lon", lon[0], lon[1],
                "-o", f"{_OUT_DIR}/masked.h5"]
        return _runner_plan([argv], workspace)

    if method == "raster_diff":
        primary = workspace / _OUT_DIR / "corrected.h5"
        secondary = workspace / _OUT_DIR / "corrected_2.h5"
        if not primary.is_file() or not secondary.is_file():
            raise FileNotFoundError("raster_diff 需要 analysis/corrected.h5 与 corrected_2.h5")
        argv = [py, "-u", "-m", "mintpy.cli.diff",
                _rel(workspace, primary), _rel(workspace, secondary),
                "-o", f"{_OUT_DIR}/decomposed.h5"]
        return _runner_plan([argv], workspace)

    if method == "transection":
        src = workspace / _OUT_DIR / "decomposed.h5"
        if not src.is_file():
            raise FileNotFoundError("transection 输入不存在:analysis/decomposed.h5")
        start = _split_pair(str(params.get("start_lalo") or ""), "start_lalo")
        end = _split_pair(str(params.get("end_lalo") or ""), "end_lalo")
        argv = [py, "-u", "-m", "mintpy.cli.plot_transection", _rel(workspace, src),
                "--start-lalo", start[0], start[1], "--end-lalo", end[0], end[1],
                "-o", f"{_OUT_DIR}/transect.txt", "--nodisplay"]
        dset = str(params.get("dataset") or "").strip()
        if dset:
            argv.extend(["--dset", dset])
        return _runner_plan([argv], workspace)

    if method == "spatial_average":
        src = workspace / _OUT_DIR / "decomposed.h5"
        if not src.is_file():
            raise FileNotFoundError("spatial_average 输入不存在:analysis/decomposed.h5")
        fname = _rel(workspace, src)
        dset = str(params.get("dataset") or "").strip() or None
        script_rel = ".analysis/run_post.py"
        argv = [py, "-u", script_rel]
        return CommandPlan(
            argv=argv,
            cwd=str(workspace),
            env=dict(_ENV),
            files={script_rel: _spatial_average_runner(fname, dset),
                   f"{_OUT_DIR}/.keep": "# analysis output dir\n"},
            shell_line=" ".join(shell_quote(a) for a in argv),
        )

    if method in ("temporal_average", "timeseries_rms"):
        src = workspace / _OUT_DIR / "decomposed.h5"
        if not src.is_file():
            raise FileNotFoundError(f"{method} 输入不存在:analysis/decomposed.h5")
        cli = {"temporal_average": "temporal_average",
               "timeseries_rms": "timeseries_rms"}[method]
        argv = [py, "-u", "-m", f"mintpy.cli.{cli}", _rel(workspace, src)]
        dset = str(params.get("dataset") or "").strip()
        if dset:
            argv.extend(["-d", dset])
        # CLI 把数字打到 stdout:原样落盘为规范产物,不改写、不编造
        script_rel = ".analysis/run_post.py"
        runner = (
            "import subprocess, sys\n"
            "from pathlib import Path\n"
            "Path('analysis').mkdir(parents=True, exist_ok=True)\n"
            f"argv = {argv!r}\n"
            "print('RUN', ' '.join(argv), flush=True)\n"
            "r = subprocess.run(argv, capture_output=True, text=True)\n"
            "sys.stdout.write(r.stdout or '')\n"
            "sys.stderr.write(r.stderr or '')\n"
            "Path('analysis/measure.json').write_text(r.stdout or '', encoding='utf-8')\n"
            "sys.exit(r.returncode)\n"
        )
        return CommandPlan(
            argv=[py, "-u", script_rel],
            cwd=str(workspace),
            env=dict(_ENV),
            files={script_rel: runner, f"{_OUT_DIR}/.keep": "# analysis output dir\n"},
            shell_line=" ".join(shell_quote(a) for a in [py, "-u", script_rel]),
        )

    py = _require_engine_python()
    figs = workspace / _FIGS
    bridge = workspace / _BRIDGE

    if method == "view_snapshot":
        inp = _safe_rel(workspace, str(params.get("input") or "analysis/decomposed.h5"), "input")
        figs.mkdir(parents=True, exist_ok=True)
        dset = str(params.get("dataset") or "").strip()
        argv = [py, "-u", "-m", "mintpy.cli.view", _rel(workspace, inp)]
        if dset:
            argv.append(dset)
        argv.extend(["--dpi", str(int(params.get("dpi", 300))), "--nodisplay", "--save",
                     "-o", f"{_FIGS}/view_{inp.stem}.png"])
        cmap = str(params.get("cmap") or "").strip()
        if cmap:
            argv.extend(["-c", cmap])
        return _runner_plan([argv], workspace)

    if method == "transection_figure":
        inp = _safe_rel(workspace, str(params.get("input") or "analysis/decomposed.h5"), "input")
        figs.mkdir(parents=True, exist_ok=True)
        argv = [py, "-u", "-m", "mintpy.cli.plot_transection", _rel(workspace, inp),
                "--nodisplay", "--save", "-o", f"{_FIGS}/transection.png"]
        return _runner_plan([argv], workspace)

    if method == "kmz":
        inp = _safe_rel(workspace, str(params.get("input") or "analysis/decomposed.h5"), "input")
        figs.mkdir(parents=True, exist_ok=True)
        argv = [py, "-u", "-m", "mintpy.cli.save_kmz", _rel(workspace, inp),
                "-o", f"{_FIGS}/{inp.stem}.kmz"]
        return _runner_plan([argv], workspace)

    if method == "kmz_timeseries":
        inp = _safe_rel(workspace, str(params.get("ts_file") or "mintpy/timeseries.h5"), "ts")
        figs.mkdir(parents=True, exist_ok=True)
        argv = [py, "-u", "-m", "mintpy.cli.save_kmz_timeseries", _rel(workspace, inp),
                "-o", f"{_FIGS}/{inp.stem}_ts.kmz"]
        return _runner_plan([argv], workspace)

    if method in ("epoch_diff", "velocity_compare"):
        if method == "epoch_diff":
            a_rel = str(params.get("epoch1") or "analysis/corrected.h5")
            b_rel = str(params.get("epoch2") or "analysis/corrected_2.h5")
        else:
            a_rel = "analysis/corrected.h5"
            b_rel = str(params.get("secondary_velocity") or "analysis/corrected_2.h5")
        a = _safe_rel(workspace, a_rel, "file1")
        b = _safe_rel(workspace, b_rel, "file2")
        argv = [py, "-u", "-m", "mintpy.cli.diff",
                _rel(workspace, a), _rel(workspace, b), "-o", f"{_OUT_DIR}/change.h5"]
        return _runner_plan([argv], workspace)

    if method == "quadratic_accel":
        ts = _safe_rel(workspace, str(params.get("ts_file") or "mintpy/timeseries.h5"), "ts")
        argv = [py, "-u", "-m", "mintpy.cli.timeseries2velocity", _rel(workspace, ts),
                "--poly", "2", "-o", f"{_OUT_DIR}/change.h5"]
        return _runner_plan([argv], workspace)

    if method == "bridge_gbis":
        bridge.mkdir(parents=True, exist_ok=True)
        src = _safe_rel(workspace, str(params.get("input") or "analysis/decomposed.h5"), "input")
        geom = _safe_rel(workspace, str(params.get("geom_file") or "mintpy/inputs/geometryGeo.h5"),
                         "geom")
        argv = [py, "-u", "-m", "mintpy.cli.save_gbis", str(src),
                "-g", str(geom), "--nodisplay", "-o", str(bridge / "gbis.mat")]
        mask = str(params.get("mask_file") or "").strip()
        if mask:
            argv.extend(["-m", str(_safe_rel(workspace, mask, "mask"))])
        return CommandPlan(argv=argv, cwd=str(workspace), env=dict(_ENV), files={},
                           shell_line=" ".join(shell_quote(a) for a in argv))

    if method == "bridge_kite":
        bridge.mkdir(parents=True, exist_ok=True)
        src = _safe_rel(workspace, str(params.get("input") or "analysis/decomposed.h5"), "input")
        dset = str(params.get("dataset") or "velocity").strip() or "velocity"
        argv = [py, "-u", "-m", "mintpy.cli.save_kite", str(src),
                "-d", dset, "-o", str(bridge / "kite")]
        geom = str(params.get("geom_file") or "").strip()
        if geom:
            argv.extend(["-g", str(_safe_rel(workspace, geom, "geom"))])
        return CommandPlan(argv=argv, cwd=str(workspace), env=dict(_ENV), files={},
                           shell_line=" ".join(shell_quote(a) for a in argv))

    if method == "bridge_gmt":
        bridge.mkdir(parents=True, exist_ok=True)
        src = _safe_rel(workspace, str(params.get("input") or "analysis/decomposed.h5"), "input")
        argv = [py, "-u", "-m", "mintpy.cli.save_gmt", str(src),
                "-o", str(bridge / "velocity.grd")]
        return CommandPlan(argv=argv, cwd=str(workspace), env=dict(_ENV), files={},
                           shell_line=" ".join(shell_quote(a) for a in argv))

    if method == "bridge_qgis":
        bridge.mkdir(parents=True, exist_ok=True)
        ts = _safe_rel(workspace, str(params.get("ts_file") or "mintpy/timeseries.h5"), "ts")
        geom = _safe_rel(workspace, str(params.get("geom_file") or "mintpy/inputs/geometryGeo.h5"),
                         "geom")
        argv = [py, "-u", "-m", "mintpy.cli.save_qgis", str(ts),
                "-g", str(geom), "-o", str(bridge / "timeseries.shp")]
        return CommandPlan(argv=argv, cwd=str(workspace), env=dict(_ENV), files={},
                           shell_line=" ".join(shell_quote(a) for a in argv))

    if method == "bridge_hdfeos5":
        bridge.mkdir(parents=True, exist_ok=True)
        ts = _safe_rel(workspace, str(params.get("ts_file") or "mintpy/timeseries.h5"), "ts")
        argv = [py, "-u", "-m", "mintpy.cli.save_hdfeos5", str(ts)]
        geom = str(params.get("geom_file") or "").strip()
        if geom:
            argv.extend(["-g", str(_safe_rel(workspace, geom, "geom"))])
        return CommandPlan(
            argv=argv, cwd=str(bridge), env=dict(_ENV), files={},
            shell_line=" ".join(shell_quote(a) for a in argv),
        )

    raise ToolMissing(f"mintpy_post 无此方法:{method}")
