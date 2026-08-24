"""R5-lite EGMS 产品分级:basic / calibrated / ortho + simulated 标记。"""

from __future__ import annotations

from insar_agent.report.methods import methods_markdown
from insar_agent.report.product_level import annotate, classify, methods_section
from insar_agent.report.results import results_skeleton


def test_classify_basic():
    assert classify({}, {}) == "basic"
    assert classify({"crossval_r": 0.9}, {"velocity": {"path": "mintpy/velocity.h5"}}) == "basic"


def test_classify_calibrated_from_gnss_rmse():
    assert classify({"gnss_rmse_mm": 4.2}, {}) == "calibrated"
    assert classify([{"name": "gnss_rmse_mm", "value": 1.5}], {}) == "calibrated"


def test_classify_ortho_from_decomposed():
    assert classify({}, {"decomposed": {"path": "analysis/decomposed.h5"}}) == "ortho"
    assert classify({"gnss_rmse_mm": 3.0},
                    {"x": {"path": "analysis/decomposed.h5"}}) == "ortho"


def test_classify_ortho_from_vertical_dataset():
    assert classify({}, {"comp": {"dataset": "vertical"}}) == "ortho"


def test_gnss_nonfinite_is_not_calibrated():
    assert classify({"gnss_rmse_mm": float("nan")}, {}) == "basic"
    assert classify({"gnss_rmse_mm": None}, {}) == "basic"


def test_simulated_flag_does_not_hide_level():
    info = annotate({"gnss_rmse_mm": 2.0}, {}, simulated=True)
    assert info["level"] == "calibrated"
    assert info["simulated"] is True
    lines = methods_section({"gnss_rmse_mm": 2.0}, {}, simulated=True)
    text = "\n".join(lines)
    assert "calibrated" in text
    assert "模拟" in text


def test_methods_markdown_includes_product_level():
    md = methods_markdown({
        "run_id": "r1", "simulated": True,
        "steps": {}, "artifacts": {}, "metrics": {},
    })
    assert "## 产品级别" in md
    assert "**basic**" in md
    assert "模拟" in md


def test_results_skeleton_simulated_not_masked_by_level():
    facts = {
        "run_id": "r1", "scenario": "x", "simulated": True,
        "generated_at_utc": "t", "qa_status": "pass",
        "evidence_level": "runnable", "metrics": [],
        "velocity": {"available": False, "reason": "velocity h5 不存在"},
        "model": None,
        "product_level": {"level": "basic", "simulated": True},
    }
    text = results_skeleton(facts)
    assert "产品级别:basic" in text
    assert "不掩盖模拟" in text
    assert "不构成科学证据" in text
