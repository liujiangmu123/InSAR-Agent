"""变化检测第 26 步 + 有纪律外推第 27 步:数学自洽,不冒充科学样本。"""

from __future__ import annotations

import numpy as np
import pytest

from insar_agent.engines import predict as predict_engine
from insar_agent.engines import resolve_builder
from insar_agent.engines.mintpy_post import accel_significance, summarize_accel
from insar_agent.engines.predict import (
    HorizonCapError,
    SimulatedRunRefused,
    enforce_horizon,
    extrapolate_series,
    prediction_payload_complete,
    write_prediction_json,
)
from insar_agent.registry.capabilities import REGISTRY


def test_linear_extrapolation_recovers_known_slope():
    """对解析构造的直线,外推值与解析解一致(数学正确性,非科学样本)。"""
    t = np.linspace(0.0, 1.0, 21)
    intercept, slope = 0.01, 0.04
    y = intercept + slope * t
    t_fut = np.array([1.25])
    pred = extrapolate_series(t, y, t_fut, family="linear")
    expected = intercept + slope * 1.25
    assert abs(float(pred["mean"][0]) - expected) < 1e-9
    assert pred["lower"][0] < pred["mean"][0] < pred["upper"][0]


def test_horizon_cap_enforced():
    """span=1yr, horizon=2yr, 无 override_reason → 必须失败。"""
    with pytest.raises(HorizonCapError):
        enforce_horizon(horizon_years=2.0, span_years=1.0, override_reason="")
    cap = enforce_horizon(horizon_years=0.4, span_years=1.0, override_reason="")
    assert cap == 0.5
    enforce_horizon(horizon_years=2.0, span_years=1.0, override_reason="explicit research request")


def test_step_function_not_repeated_in_future():
    """含 step 的模型,外推段导数不含阶跃 —— 断言未来无新跳变。"""
    t = np.linspace(0.0, 2.0, 41)
    step_year = 0.8
    intercept, slope, jump = 0.01, 0.02, 0.05
    y = intercept + slope * t + jump * (t >= step_year).astype(float)
    t_fut = np.linspace(2.0, 2.5, 11)
    pred = extrapolate_series(t, y, t_fut, family="step", step_year=step_year)
    assert np.allclose(pred["G_fut"][:, -1], 1.0)
    dy = np.diff(pred["mean"]) / np.diff(t_fut)
    assert np.allclose(dy, slope, atol=1e-6)
    assert np.max(np.abs(np.diff(pred["mean"]))) < abs(jump) * 0.5


def test_prediction_json_carries_assumptions_and_ci(tmp_path):
    """assumptions / not_a_forecast_of / ci 三字段缺一 → 不产出文件。"""
    complete = {
        "assumptions": ["if the current trend continues", "no new step events assumed"],
        "not_a_forecast_of": ["earthquake occurrence or timing"],
        "ci": {"level": 0.95, "lower": -0.01, "upper": 0.02, "units": "m"},
    }
    ok_path = tmp_path / "analysis" / "prediction.json"
    assert write_prediction_json(ok_path, complete) is True
    assert ok_path.is_file()
    assert prediction_payload_complete(complete)

    for i, broken in enumerate((
        {**complete, "assumptions": []},
        {**complete, "not_a_forecast_of": []},
        {**complete, "ci": {"level": 0.95}},
        {k: v for k, v in complete.items() if k != "ci"},
    )):
        p = tmp_path / f"bad_{i}.json"
        assert write_prediction_json(p, broken) is False
        assert not p.exists()


def test_simulated_run_refused(tmp_path):
    with pytest.raises(SimulatedRunRefused):
        predict_engine.build(
            cap=REGISTRY[27], method="extrapolate_fitted",
            params=REGISTRY[27].default_params(),
            run={"simulated": 1}, workspace=tmp_path)
    assert resolve_builder(REGISTRY[27], "extrapolate_fitted",
                           simulated=True) is predict_engine.build


def test_quadratic_accel_significance_ratio():
    """加速检测显著性判据 |a|/σ_a ≥ 2。"""
    accel = np.array([[0.1, 0.4], [np.nan, -0.9]])
    std = np.array([[0.1, 0.1], [0.1, 0.3]])  # ratios 1, 4, nan, 3
    sig, _ratio = accel_significance(accel, std, threshold=2.0)
    assert sig.tolist() == [[False, True], [False, True]]
    summary = summarize_accel(accel, std)
    assert summary["significance_threshold"] == 2.0
    assert summary["n_valid"] == 3
    assert summary["n_significant"] == 2
    assert abs(summary["significant_fraction"] - 2 / 3) < 1e-12
    assert summary["max_abs_accel"] == pytest.approx(0.9)
