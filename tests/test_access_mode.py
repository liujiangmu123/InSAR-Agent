"""接入模式 A/B/C 纯函数(src/insar_agent/api/access_mode.py)。"""

from __future__ import annotations

import math

from insar_agent.api.access_mode import (
    classify_access_mode,
    empty_qa_chips,
    product_label,
    product_view,
    qa_chips_from_metrics,
)


def test_empty_steps_none():
    assert classify_access_mode([]) is None
    assert classify_access_mode(None) is None
    assert classify_access_mode([{"name": "no-id", "state": "done"}]) is None


def test_analysis_only_is_c():
    steps = [{"step": i, "state": "pending"} for i in range(20, 26)]
    assert classify_access_mode(steps) == "C"
    # 手造分析步用 step_id 也算
    assert classify_access_mode([{"step_id": 20, "state": "done"}]) == "C"


def test_core_with_cloud_skipped_is_b():
    steps = [{"step": i, "state": "done"} for i in range(1, 12)]
    for s in steps:
        if s["step"] in (2, 3, 4, 5, 6):
            s["state"] = "skipped"
    assert classify_access_mode(steps) == "B"


def test_core_normal_is_a():
    steps = [{"step": i, "state": "pending"} for i in range(1, 12)]
    assert classify_access_mode(steps) == "A"
    mixed = [
        {"step": 1, "state": "done"},
        {"step": 2, "state": "skipped"},
        {"step": 3, "state": "running"},
        {"step": 4, "state": "pending"},
        {"step": 5, "state": "done"},
    ]
    assert classify_access_mode(mixed) == "A"


def test_incomplete_cloud_skipped_is_a_not_b():
    """2–6 缺步不得当 B:出现的那些全是 skipped 也不够。"""
    steps = [{"step": i, "state": "skipped"} for i in (2, 3, 4, 5)]  # 无 6
    steps.append({"step": 1, "state": "done"})
    assert classify_access_mode(steps) == "A"


def test_cloud_present_but_one_not_skipped_is_a():
    steps = [{"step": i, "state": "skipped"} for i in range(1, 12)]
    steps[3]["state"] = "done"  # step 4
    assert classify_access_mode(steps) == "A"


def test_core_plus_analysis_is_a_not_c():
    steps = [{"step": 1, "state": "done"}, {"step": 20, "state": "pending"}]
    assert classify_access_mode(steps) == "A"


def test_neither_core_nor_analysis_is_none():
    assert classify_access_mode([{"step": 15, "state": "done"}]) is None


def test_product_label_maps_52_section_8_3():
    assert product_label("basic") == "LOS · 相对参考点"
    assert product_label("calibrated") == "LOS · GNSS 锚定"
    assert product_label("ortho") == "垂直 / 东西向"
    assert product_label(None) is None
    assert product_label("unknown") is None


def test_qa_chips_absent_are_null_not_fabricated():
    chips = qa_chips_from_metrics({})
    assert chips == empty_qa_chips()
    assert chips["crossval_r"] is None
    assert chips["gnss_rmse_mm"] is None
    assert chips["crossval_r"] != 0.92


def test_qa_chips_reads_present_numbers():
    chips = qa_chips_from_metrics({"gnss_rmse_mm": 4.25, "crossval_r": 0.87})
    assert chips["gnss_rmse_mm"] == 4.25
    assert chips["crossval_r"] == 0.87
    nested = qa_chips_from_metrics({"gnss_rmse_mm": {"value": 1.5}})
    assert nested["gnss_rmse_mm"] == 1.5
    assert nested["crossval_r"] is None


def test_qa_chips_rejects_nonfinite():
    assert qa_chips_from_metrics({"crossval_r": math.nan})["crossval_r"] is None
    assert qa_chips_from_metrics({"gnss_rmse_mm": math.inf})["gnss_rmse_mm"] is None


def test_product_view_calibrated_from_gnss():
    view = product_view({"gnss_rmse_mm": 3.5}, [], simulated=True)
    assert view["level"] == "calibrated"
    assert view["simulated"] is True
    assert view["label"] == "LOS · GNSS 锚定"
