"""形变趋势外推(有纪律的预测):已拟合时间函数 + 不确定度传播,拒绝黑箱。

流程(全部真实数据,零网络):
  1. 读 timeseries*.h5 与 velocity.h5;
  2. 按第 9 步落账的时间函数族以最小二乘重估系数与协方差(不重新选模型);
  3. 外推 horizon_years,置信带 = 设计矩阵外推行 × 系数协方差;
  4. 上限检查:horizon > min(0.5×span, 2yr) 且无 override_reason → 直接失败;
  5. 输出 prediction.json / prediction.png(标题含 extrapolation, not a forecast);
  6. 阶跃项在外推期贡献为常数(不再跳变)。

模拟 run:直接 raise(与导出 409 同理:占位字节不可外推)。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np

from insar_agent.registry.model import Capability
from insar_agent.runtime.jobs import CommandPlan, shell_quote

MAX_HORIZON_YEARS = 2.0
HORIZON_FRAC = 0.5

NOT_A_FORECAST_OF = (
    "earthquake occurrence or timing",
    "landslide failure time",
    "new coseismic or outburst step events",
)


class HorizonCapError(ValueError):
    """外推时长超过有效期上限且未提供 override 理由。"""


class SimulatedRunRefused(ValueError):
    """模拟 run 的占位产物不可外推。"""


class IncompletePrediction(ValueError):
    """assumptions / not_a_forecast_of / ci 缺一,拒绝落盘。"""


class ModelSourceError(ValueError):
    """无法从第 9 步账本或产物属性读取时间函数族。"""


def z_of(confidence: float) -> float:
    return float(NormalDist().inv_cdf(0.5 + float(confidence) / 2.0))


def allowed_horizon(span_years: float) -> float:
    return min(HORIZON_FRAC * float(span_years), MAX_HORIZON_YEARS)


def enforce_horizon(horizon_years: float, span_years: float,
                    override_reason: str = "") -> float:
    cap = allowed_horizon(span_years)
    if float(horizon_years) > cap and not str(override_reason).strip():
        raise HorizonCapError(
            f"外推时长 {horizon_years} yr 超过上限 {cap:.6f} yr "
            f"(= min(0.5×观测时长 {span_years:.6f} yr, {MAX_HORIZON_YEARS} yr));"
            f"超限须提供 horizon_override_reason")
    return cap


def dates_to_years(dates) -> np.ndarray:
    """MintPy date(YYYYMMDD bytes/str/int) → 相对首历元的年。"""
    dts: list[datetime] = []
    for d in dates:
        if isinstance(d, bytes):
            d = d.decode()
        s = str(d).replace("-", "").replace("T", "")[:8]
        dts.append(datetime(int(s[:4]), int(s[4:6]), int(s[6:8])))
    t0 = dts[0]
    return np.array([(dt - t0).days / 365.25 for dt in dts], dtype=float)


def design_matrix(t: np.ndarray, *, family: str, poly_order: int = 1,
                  periods: tuple | list = (), step_year: float | None = None,
                  exp_tau: float = 1.0) -> np.ndarray:
    """MintPy timeFunc 语义的设计矩阵(列:截距,多项式,周期,阶跃,指数)。

    阶跃列是 Heaviside H(t - t_step):拟合期内的一次跳变保留,外推期为常数,
    未来不会再跳一次。
    """
    t = np.asarray(t, dtype=float)
    cols = [np.ones_like(t)]
    family = str(family or "linear")
    if family == "exponential":
        cols.append(t)
        tau = float(exp_tau) or 1.0
        cols.append(1.0 - np.exp(-np.maximum(t, 0.0) / tau))
    else:
        order = max(int(poly_order), 1)
        for p in range(1, order + 1):
            cols.append(t ** p)
        if family == "poly_periodic":
            for per in (periods or (1.0, 0.5)):
                per_f = float(per)
                if per_f <= 0:
                    continue
                cols.append(np.sin(2 * np.pi * t / per_f))
                cols.append(np.cos(2 * np.pi * t / per_f))
        if family == "step" or step_year is not None:
            if step_year is None:
                raise ModelSourceError("step 模型缺少 step_year")
            cols.append((t >= float(step_year)).astype(float))
    return np.column_stack(cols)


def fit_ols(G: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mask = np.isfinite(y) & np.all(np.isfinite(G), axis=1)
    Gm, ym = G[mask], np.asarray(y, dtype=float)[mask]
    if Gm.shape[0] < Gm.shape[1]:
        raise ValueError("观测不足,无法拟合时间函数")
    coef, residuals, _rank, _ = np.linalg.lstsq(Gm, ym, rcond=None)
    n, k = Gm.shape
    rss = float(residuals[0]) if residuals.size else float(np.sum((Gm @ coef - ym) ** 2))
    sigma2 = rss / max(n - k, 1)
    gram = Gm.T @ Gm
    try:
        cov = sigma2 * np.linalg.inv(gram)
    except np.linalg.LinAlgError:
        cov = sigma2 * np.linalg.pinv(gram)
    return coef, cov


def predict_mean_ci(G: np.ndarray, coef: np.ndarray, cov: np.ndarray,
                    confidence: float = 0.95) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mean = G @ coef
    var = np.einsum("ij,jk,ik->i", G, cov, G)
    se = np.sqrt(np.maximum(var, 0.0))
    z = z_of(confidence)
    return mean, mean - z * se, mean + z * se


def extrapolate_series(t_obs: np.ndarray, y_obs: np.ndarray, t_fut: np.ndarray, *,
                       family: str = "linear", confidence: float = 0.95,
                       poly_order: int = 1, periods: tuple | list = (),
                       step_year: float | None = None,
                       exp_tau: float = 1.0) -> dict[str, np.ndarray]:
    """对一维序列拟合并外推。纯函数核,供数学自洽测试调用。"""
    kw = dict(family=family, poly_order=poly_order, periods=periods,
              step_year=step_year, exp_tau=exp_tau)
    G_obs = design_matrix(t_obs, **kw)
    coef, cov = fit_ols(G_obs, y_obs)
    G_fut = design_matrix(t_fut, **kw)
    mean, lo, hi = predict_mean_ci(G_fut, coef, cov, confidence)
    return {"coef": coef, "cov": cov, "mean": mean, "lower": lo, "upper": hi,
            "G_fut": G_fut}


def prediction_payload_complete(payload: dict) -> bool:
    if not payload.get("assumptions"):
        return False
    if not payload.get("not_a_forecast_of"):
        return False
    ci = payload.get("ci")
    if not isinstance(ci, dict):
        return False
    if ci.get("lower") is None or ci.get("upper") is None:
        return False
    return True


def write_prediction_json(path: Path, payload: dict) -> bool:
    """三要素缺一 → 不产出文件。"""
    if not prediction_payload_complete(payload):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return True


def write_prediction_png(path: Path, t_obs, y_obs, t_fut, y_fut, lo, hi) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("WARN: matplotlib 不可用,跳过 prediction.png", flush=True)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    ax.plot(t_obs, y_obs, "-", color="#1d4ed8", lw=1.2, label="observed")
    ax.plot(t_fut, y_fut, "--", color="#b45309", lw=1.2, label="extrapolation")
    ax.fill_between(t_fut, lo, hi, color="#b45309", alpha=0.25, label="confidence interval")
    ax.set_xlabel("years since first epoch")
    ax.set_ylabel("LOS displacement (m)")
    ax.set_title("LOS displacement extrapolation, not a forecast")
    ax.legend(frameon=False, fontsize=8)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return True


def assumptions_of(*, family: str, horizon: float, span: float, cap: float,
                   override: str, source: str) -> list[str]:
    items = [
        "if the current trend continues",
        "no new step events assumed",
        f"time function family locked to ledger/product: {family} (source={source})",
        f"horizon {horizon} yr; validity cap min(0.5×span, 2 yr) = {cap:.6f} yr "
        f"(span={span:.6f} yr)",
        "uncertainty from OLS coefficient covariance (or velocityStd if field summary)",
        "InSAR LOS trend only — not a physical event forecast",
    ]
    if str(override).strip():
        items.append(f"horizon_override_reason: {override.strip()}")
    return items


def _chain_step9(run: dict, spec: dict) -> dict:
    chain = run.get("chain") or {}
    entry = chain.get(9) or chain.get("9") or spec.get("chain_step9") or {}
    return entry if isinstance(entry, dict) else {}


def resolve_family(run: dict, spec: dict, h5_attrs: dict | None = None) -> dict:
    """只读已落账的时间函数族,不重新选模型。"""
    entry = _chain_step9(run, spec)
    method = str(entry.get("method") or "").strip()
    params = entry.get("params") or {}
    if method:
        return {"family": method, "params": params, "source": "run.chain[9]"}
    attrs = {str(k): str(v) for k, v in (h5_attrs or {}).items()}
    poly = attrs.get("mintpy.timeFunc.polynomial") or attrs.get("TIMEFUNC_POLYNOMIAL")
    step = attrs.get("mintpy.timeFunc.stepDate") or attrs.get("STEP_DATE") or ""
    periodic = attrs.get("mintpy.timeFunc.periodic") or ""
    if step and step not in ("auto", "no", ""):
        return {"family": "step", "params": {"step_date": step},
                "source": "velocity.h5 attrs"}
    if periodic and periodic not in ("auto", "no", ""):
        periods = [float(x) for x in periodic.replace(",", " ").split() if x]
        return {"family": "poly_periodic",
                "params": {"periods": periods, "poly_order": int(float(poly or 1))},
                "source": "velocity.h5 attrs"}
    if poly and poly not in ("auto", "no", ""):
        return {"family": "linear", "params": {"poly_order": int(float(poly))},
                "source": "velocity.h5 attrs"}
    raise ModelSourceError(
        "无法从第 9 步账本或 velocity.h5 属性读取时间函数族,拒绝自行选模型")


def _step_year_from_params(params: dict, t_obs: np.ndarray, dates) -> float | None:
    raw = str(params.get("step_date") or "").strip()
    if not raw:
        return None
    s = raw.replace("-", "").replace("T", "")[:8]
    try:
        step_dt = datetime(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None
    d0 = dates[0]
    if isinstance(d0, bytes):
        d0 = d0.decode()
    s0 = str(d0).replace("-", "").replace("T", "")[:8]
    t0 = datetime(int(s0[:4]), int(s0[4:6]), int(s0[6:8]))
    return (step_dt - t0).days / 365.25


def _find_h5(workspace: Path, names: tuple[str, ...]) -> Path | None:
    for n in names:
        p = workspace / n
        if p.exists():
            return p
    mintpy = workspace / "mintpy"
    if mintpy.is_dir():
        for p in sorted(mintpy.glob("timeseries*.h5")):
            return p
    return None


def _read_velocity_field(path: Path) -> tuple[np.ndarray, np.ndarray | None, dict]:
    import h5py
    with h5py.File(path, "r") as f:
        if "velocity" not in f:
            raise FileNotFoundError(f"{path} 无 velocity 数据集")
        vel = np.asarray(f["velocity"][:], dtype=float)
        std = np.asarray(f["velocityStd"][:], dtype=float) if "velocityStd" in f else None
        atr = {k: str(v) for k, v in f.attrs.items()}
    return vel, std, atr


def _read_timeseries(path: Path) -> tuple[np.ndarray, np.ndarray, dict]:
    import h5py
    with h5py.File(path, "r") as f:
        key = "timeseries" if "timeseries" in f else next(
            k for k in f.keys() if str(k).lower().startswith("timeseries"))
        ts = np.asarray(f[key][:], dtype=float)
        dates = f["date"][:] if "date" in f else None
        if dates is None:
            raise FileNotFoundError(f"{path} 无 date 数据集")
        atr = {k: str(v) for k, v in f.attrs.items()}
    return ts, dates, atr


def _lalo_to_yx(atr: dict, lat: float, lon: float) -> tuple[int, int]:
    need = ("X_FIRST", "Y_FIRST", "X_STEP", "Y_STEP")
    if not all(k in atr for k in need):
        raise ModelSourceError("点位预测需要地理编码属性 X_FIRST/Y_FIRST/X_STEP/Y_STEP")
    x0, y0, dx, dy = (float(atr[k]) for k in need)
    col = int(round((lon - x0) / dx))
    row = int(round((lat - y0) / dy))
    return row, col


def _host_env() -> dict[str, str]:
    import insar_agent
    src = str(Path(insar_agent.__file__).resolve().parents[1])
    prev = os.environ.get("PYTHONPATH", "")
    return {
        "PYTHONPATH": src if not prev else src + os.pathsep + prev,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "HDF5_USE_FILE_LOCKING": "FALSE",
    }


def _predict_python() -> str:
    """优先引擎 Python(有 matplotlib);否则宿主。零 MintPy API 依赖。"""
    try:
        from insar_agent.engines.mintpy import engine_python
        py = engine_python()
        if py and py != "python":
            return py
    except Exception:
        pass
    return sys.executable


def refuse_if_simulated(run: dict) -> None:
    if run.get("simulated"):
        raise SimulatedRunRefused(
            "模拟 run 的产物是演示占位字节(runs.simulated=1),"
            "不可外推预测 —— 请配置真实引擎后重跑,再对外推真实时序")


def run_predict(workspace: Path, spec: dict, *, run: dict | None = None) -> dict:
    run = run or spec.get("run") or {}
    refuse_if_simulated(run)
    horizon = float(spec.get("horizon_years", 1.0))
    override = str(spec.get("horizon_override_reason") or "")
    confidence = float(spec.get("confidence", 0.95))
    points = spec.get("points_lalo") or []

    vel_path = _find_h5(workspace, ("mintpy/velocity.h5", "velocity.h5",
                                    "analysis/corrected.h5", "analysis/decomposed.h5",
                                    "analysis/source.h5"))
    ts_path = _find_h5(workspace, ("mintpy/timeseries.h5",
                                   "mintpy/timeseries_ERA5_ramp_demErr.h5",
                                   "mintpy/timeseries_ERA5_demErr.h5",
                                   "mintpy/timeseries_ramp_demErr.h5",
                                   "mintpy/timeseries_demErr.h5"))
    if vel_path is None:
        raise FileNotFoundError("未找到 velocity.h5(或分析链规范速度场),拒绝无数据外推")

    vel, vel_std, atr = _read_velocity_field(vel_path)
    family_info = resolve_family(run, spec, atr)
    family = family_info["family"]
    fparams = family_info["params"]

    t_obs = None
    dates = None
    ts = None
    if ts_path is not None:
        ts, dates, ts_atr = _read_timeseries(ts_path)
        t_obs = dates_to_years(dates)
        span = float(t_obs[-1] - t_obs[0]) if t_obs.size > 1 else 0.0
        atr = {**ts_atr, **atr}
    else:
        # 无时序时,用速度场属性里的日期跨度;再没有则拒绝(不能捏造观测时长)
        start, end = atr.get("START_DATE", ""), atr.get("END_DATE", "")
        if start and end:
            t_obs = dates_to_years([start, end])
            span = float(t_obs[-1] - t_obs[0])
        else:
            raise ModelSourceError("无法确定观测时长(缺 timeseries date 与 START_DATE/END_DATE)")

    if span <= 0:
        raise ModelSourceError(f"观测时长非正:{span}")
    cap = enforce_horizon(horizon, span, override)

    step_year = _step_year_from_params(fparams, t_obs, dates if dates is not None else [atr.get("START_DATE", "20000101")])
    periods = tuple(fparams.get("periods") or ())
    poly_order = int(fparams.get("poly_order") or 1)
    t_fut = np.array([t_obs[-1] + horizon], dtype=float)

    points_out: list[dict] = []
    plot_series = None
    if points and ts is not None:
        for pt in points:
            lat, lon = float(pt[0]), float(pt[1])
            row, col = _lalo_to_yx(atr, lat, lon)
            if not (0 <= row < ts.shape[1] and 0 <= col < ts.shape[2]):
                raise ValueError(f"点位 ({lat},{lon}) 超出栅格")
            y = ts[:, row, col]
            pred = extrapolate_series(
                t_obs, y, t_fut, family=family, confidence=confidence,
                poly_order=poly_order, periods=periods, step_year=step_year)
            points_out.append({
                "lat": lat, "lon": lon, "row": row, "col": col,
                "predicted_m": float(pred["mean"][0]),
                "ci": {"level": confidence,
                       "lower": float(pred["lower"][0]),
                       "upper": float(pred["upper"][0]),
                       "units": "m"},
            })
            if plot_series is None:
                yhat_obs = design_matrix(
                    t_obs, family=family, poly_order=poly_order,
                    periods=periods, step_year=step_year) @ pred["coef"]
                plot_series = (t_obs, y, t_fut, pred["mean"], pred["lower"], pred["upper"],
                               yhat_obs)

    finite = vel[np.isfinite(vel)]
    if finite.size == 0:
        raise ValueError("velocity 全 NaN,拒绝外推")
    med_v = float(np.median(finite))
    field_disp = med_v * horizon
    if vel_std is not None:
        finite_std = vel_std[np.isfinite(vel) & np.isfinite(vel_std)]
        med_std = float(np.median(finite_std)) if finite_std.size else float("nan")
    else:
        med_std = float("nan")
    z = z_of(confidence)
    if not np.isfinite(med_std):
        raise IncompletePrediction("velocity.h5 无 velocityStd,没有不确定度就没有预测")
    field_lo = field_disp - z * med_std * horizon
    field_hi = field_disp + z * med_std * horizon
    field_ci = {"level": confidence, "lower": field_lo, "upper": field_hi,
                "z": z, "units": "m", "median_velocity_m_per_yr": med_v}

    # 点预测优先作为主 CI;否则用整场
    main_ci = points_out[0]["ci"] if points_out else field_ci

    payload = {
        "status": "extrapolate_fitted",
        "phrase": "if the current trend continues",
        "family": family,
        "family_source": family_info["source"],
        "horizon_years": horizon,
        "span_years": span,
        "validity_cap_years": cap,
        "horizon_override_reason": override,
        "confidence": confidence,
        "n_valid_velocity": int(finite.size),
        "field": {"predicted_median_m": field_disp, "ci": field_ci},
        "points": points_out,
        "ci": main_ci,
        "assumptions": assumptions_of(
            family=family, horizon=horizon, span=span, cap=cap,
            override=override, source=family_info["source"]),
        "not_a_forecast_of": list(NOT_A_FORECAST_OF),
    }
    out_json = workspace / "analysis" / "prediction.json"
    if not write_prediction_json(out_json, payload):
        raise IncompletePrediction("prediction.json 三要素不完整,拒绝落盘")
    print(f"OK {out_json}", flush=True)

    png = workspace / "analysis" / "prediction.png"
    if plot_series is not None:
        t_o, y_o, t_f, y_f, lo, hi, _fit = plot_series
        write_prediction_png(png, t_o, y_o, np.concatenate([t_o[-1:], t_f]),
                             np.concatenate([y_o[-1:], y_f]),
                             np.concatenate([y_o[-1:], lo]),
                             np.concatenate([y_o[-1:], hi]))
    else:
        # 整场:用中位速度的直线示意(观测段 = 0 → v*span,外推段虚线)
        t_line = np.array([0.0, span, span + horizon])
        y_line = np.array([0.0, med_v * span, med_v * (span + horizon)])
        lo_line = y_line - z * med_std * t_line
        hi_line = y_line + z * med_std * t_line
        write_prediction_png(png, t_line[:2], y_line[:2], t_line[1:], y_line[1:],
                             lo_line[1:], hi_line[1:])
    return payload


def build(*, cap: Capability, method: str, params: dict[str, Any], run: dict,
          workspace: Path) -> CommandPlan:
    refuse_if_simulated(run)
    spec = {
        "horizon_years": params.get("horizon_years", 1.0),
        "horizon_override_reason": params.get("horizon_override_reason") or "",
        "points_lalo": params.get("points_lalo") or [],
        "confidence": params.get("confidence", 0.95),
        "chain_step9": (run.get("chain") or {}).get(9) or (run.get("chain") or {}).get("9") or {},
        "run": {"simulated": bool(run.get("simulated")), "run_id": run.get("run_id")},
    }
    spec_rel = "analysis/.predict_spec.json"
    py = _predict_python()
    argv = [py, "-u", "-m", "insar_agent.engines.predict", spec_rel]
    return CommandPlan(
        argv=argv,
        cwd=str(workspace),
        env=_host_env(),
        files={spec_rel: json.dumps(spec, ensure_ascii=False, indent=1)},
        shell_line=" ".join(shell_quote(a) for a in argv),
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: python -m insar_agent.engines.predict <spec.json>", flush=True)
        return 2
    spec = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    run_predict(Path(".").resolve(), spec, run=spec.get("run") or {})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
