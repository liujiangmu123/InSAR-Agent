"""真实质检(step 11):从产物算真指标,绝不编造。

指标(全部可由 verify.py 重解析):
    velocity_coverage    速度场有效像元占比
    nan_fraction         NaN 占比
    mean_coherence       平均空间相干性(avgSpatialCoh.h5,invert_network 副产物)
    vel_p2 / vel_p98     速度 2/98 分位(mm/yr)
    residual_rms_mm      时序残差 RMS(若 rms_timeseriesResidual 存在)

method 编进生成脚本:
  coherence_mask     只做上述统计;不写 crossval_r(即使 PS 场碰巧存在)
  crossval_ps_sbas   重叠有限像元上 Pearson r 与 RMSE(mm/yr);
                     PS 缺失或重叠不足 → crossval_r=null + crossval_status
  loop_closure       尚无独立实现;复用 coherence_mask 统计并标注 method

禁止用随机数或固定 0.92 冒充交叉验证。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from insar_agent.engines.mintpy import engine_python
from insar_agent.registry.model import Capability
from insar_agent.runtime.jobs import CommandPlan

MIN_OVERLAP = 10
SBAS_VELOCITY_CANDIDATES = ("mintpy/velocity.h5", "velocity.h5")
PS_VELOCITY_CANDIDATES = (
    "pystamps/velocity.h5",
    "products/ps_velocity.h5",
    "ps/velocity.h5",
)

_QA_PY = '''\
# insar-agent 真实质检脚本:指标全部来自产物重解析,绝不编造
import sys
from pathlib import Path

_src = Path(__SRC_ROOT_REPR__)
if _src.is_dir() and str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from insar_agent.engines.qa import run_qa

METHOD = __METHOD__
WS = Path(".").resolve()
try:
    run_qa(WS, METHOD)
except FileNotFoundError:
    raise SystemExit(2)
'''


def compute_crossval(sbas: Any, ps: Any, *, min_overlap: int = MIN_OVERLAP) -> dict:
    """重叠有限像元上的 Pearson r 与 RMSE(mm/yr)。

    sbas/ps 必须已是 mm/yr、同形状。不编造:重叠 < min_overlap 则 r 为 None。

    Pearson 积矩相关(重叠像元上的点估计;1/n 与 1/(n-1) 对 r 相同)::

        r = Σ (x_i - x̄)(y_i - ȳ) / sqrt( Σ (x_i - x̄)² · Σ (y_i - ȳ)² )

    RMSE::

        RMSE = sqrt( mean( (v_sbas - v_ps)² ) )    # mm/yr
    """
    import numpy as np

    x = np.asarray(sbas, dtype=np.float64)
    y = np.asarray(ps, dtype=np.float64)
    if x.shape != y.shape:
        return {
            "crossval_r": None,
            "crossval_rmse_mm": None,
            "n_overlap": 0,
            "crossval_status": "shape_mismatch",
        }
    mask = np.isfinite(x) & np.isfinite(y)
    n = int(mask.sum())
    if n < min_overlap:
        return {
            "crossval_r": None,
            "crossval_rmse_mm": None,
            "n_overlap": n,
            "crossval_status": "overlap_too_small",
        }
    xv = x[mask]
    yv = y[mask]
    xc = xv - xv.mean()
    yc = yv - yv.mean()
    denom = float(np.sqrt(np.sum(xc * xc) * np.sum(yc * yc)))
    if denom == 0.0 or not np.isfinite(denom):
        return {
            "crossval_r": None,
            "crossval_rmse_mm": None,
            "n_overlap": n,
            "crossval_status": "undefined_correlation",
        }
    r = float(np.sum(xc * yc) / denom)
    rmse = float(np.sqrt(np.mean((xv - yv) ** 2)))
    if not np.isfinite(r):
        return {
            "crossval_r": None,
            "crossval_rmse_mm": None,
            "n_overlap": n,
            "crossval_status": "undefined_correlation",
        }
    return {
        "crossval_r": round(r, 6),
        "crossval_rmse_mm": round(rmse, 4),
        "n_overlap": n,
        "crossval_status": "ok",
    }


def find_velocity_h5(workspace: Path, candidates: tuple[str, ...]) -> Path | None:
    ws = Path(workspace)
    for rel in candidates:
        p = ws / rel
        if p.is_file():
            return p
    return None


def load_velocity_mm(path: Path) -> Any:
    """读 HDF5 数据集 ``velocity``(米/年)并转为 mm/yr。"""
    import h5py
    import numpy as np

    with h5py.File(path, "r") as f:
        if "velocity" not in f:
            raise KeyError(f"{path} 无数据集 velocity")
        return np.asarray(f["velocity"][:], dtype=np.float64) * 1000.0


def run_qa(workspace: Path, method: str) -> dict:
    """从工作区产物写 ``products/report/qa.json``,返回同一 dict。

    SBAS ``velocity.h5`` 缺失时打印 ERROR 并 raise FileNotFoundError(脚本 exit 2)。
    """
    import numpy as np

    ws = Path(workspace).resolve()
    method = str(method)
    qa: dict = {"simulated": False, "method": method}

    vel_path = find_velocity_h5(ws, SBAS_VELOCITY_CANDIDATES)
    if vel_path is None:
        print("ERROR: velocity.h5 不存在", flush=True)
        raise FileNotFoundError("velocity.h5 不存在")

    vel = load_velocity_mm(vel_path)
    total = int(np.asarray(vel).size)
    finite = np.isfinite(vel)
    qa["velocity_coverage"] = round(float(finite.sum()) / total, 4) if total else 0.0
    qa["nan_fraction"] = round(1.0 - qa["velocity_coverage"], 4)
    if finite.any():
        vals = np.asarray(vel)[finite]
        qa["vel_p2"] = round(float(np.percentile(vals, 2)), 2)
        qa["vel_p98"] = round(float(np.percentile(vals, 98)), 2)
        print(f"velocity: 覆盖 {qa['velocity_coverage']:.1%} · "
              f"p2={qa['vel_p2']} p98={qa['vel_p98']} mm/yr", flush=True)
    else:
        print("velocity 全为 NaN,跳过分位", flush=True)

    coh_path = ws / "mintpy/avgSpatialCoh.h5"
    if coh_path.is_file():
        import h5py

        with h5py.File(coh_path, "r") as f:
            coh = f["coherence"][:]
        qa["mean_coherence"] = round(float(np.nanmean(coh)), 4)
        print(f"平均空间相干性: {qa['mean_coherence']}", flush=True)
    else:
        print("avgSpatialCoh.h5 不存在,跳过相干性指标", flush=True)

    _maybe_residual_rms(ws, qa)

    if method == "crossval_ps_sbas":
        _fill_crossval(ws, qa, vel)
    elif method == "loop_closure":
        print("注:loop_closure 尚无独立实现,复用 coherence_mask 统计;"
              "不编造闭合环残差", flush=True)
    else:
        print("method=coherence_mask:不计算 crossval_r(需 crossval_ps_sbas);"
              "PS 速度场缺失时诚实缺席,阈值 PENDING 仅警告", flush=True)

    out = ws / "products" / "report" / "qa.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    import json

    out.write_text(json.dumps(qa, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out}", flush=True)
    return qa


def build(*, cap: Capability, method: str, params: dict[str, Any], run: dict,
          workspace: Path) -> CommandPlan:
    src_root = Path(__file__).resolve().parents[2]
    script_rel = ".report/run_qa.py"
    script = (_QA_PY
              .replace("__SRC_ROOT_REPR__", repr(str(src_root)))
              .replace("__METHOD__", repr(method)))
    return CommandPlan(
        argv=[engine_python(), "-u", script_rel],
        cwd=str(workspace),
        env={"PYTHONIOENCODING": "utf-8", "HDF5_USE_FILE_LOCKING": "FALSE"},
        files={script_rel: script},
        shell_line=f"python {script_rel}",
    )


def _fill_crossval(ws: Path, qa: dict, sbas_mm) -> None:
    ps_path = find_velocity_h5(ws, PS_VELOCITY_CANDIDATES)
    if ps_path is None:
        qa["crossval_r"] = None
        qa["crossval_status"] = "ps_velocity_missing"
        cands = " | ".join(PS_VELOCITY_CANDIDATES)
        print(f"注:crossval_r 缺失 —— PS 速度场不存在 ({cands}),诚实缺席",
              flush=True)
        return
    try:
        ps_mm = load_velocity_mm(ps_path)
    except (OSError, KeyError) as exc:
        qa["crossval_r"] = None
        qa["crossval_status"] = "ps_velocity_missing"
        print(f"注:crossval_r 缺失 —— 无法读取 PS 速度场 {ps_path}: {exc}",
              flush=True)
        return
    result = compute_crossval(sbas_mm, ps_mm, min_overlap=MIN_OVERLAP)
    qa.update(result)
    if result["crossval_status"] == "ok":
        print(f"crossval_ps_sbas: r={result['crossval_r']} "
              f"RMSE={result['crossval_rmse_mm']} mm/yr "
              f"n_overlap={result['n_overlap']}", flush=True)
        return
    if result["crossval_status"] == "overlap_too_small":
        print(f"注:crossval_r 缺失 —— 重叠有限像元 "
              f"{result['n_overlap']} < {MIN_OVERLAP}(overlap_too_small),诚实缺席",
              flush=True)
        return
    print(f"注:crossval_r 缺失 —— {result['crossval_status']},诚实缺席",
          flush=True)


def _maybe_residual_rms(ws: Path, qa: dict) -> None:
    rms_files = sorted((ws / "mintpy").glob("rms_timeseriesResidual*.txt"))
    if not rms_files:
        return
    try:
        rows = [line.split() for line in rms_files[0].read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith(("#", "date"))]
        rms = [float(r[1]) for r in rows if len(r) >= 2]
        if rms:
            qa["residual_rms_mm"] = round(sum(rms) / len(rms) * 1000.0, 2)
            print(f"时序残差 RMS: {qa['residual_rms_mm']} mm", flush=True)
    except (ValueError, IndexError):
        print("residual RMS 解析失败,跳过", flush=True)
