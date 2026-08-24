"""接入模式 A/B/C 与 monitor 产品徽章的纯函数。

右栏要区分全链(A)、HyP3 云端(B)、位移产品直接分析(C)。现有会话 `mode`
(free|strict)语义不变,本模块只根据步骤清单另判 access_mode。

规划事实:analysis run 不排 1–11,所以 C 的 steps 里通常没有 core 步;
B 是 core run 且 2–6 为 skipped(cloud_completed)。缺步不得当成 skipped。
"""

from __future__ import annotations

import math
from typing import Any

from insar_agent.report.product_level import annotate

# 主链步号(全链 / HyP3 导入后时序)
_CORE = frozenset(range(1, 12))
# HyP3/ARIA 云端已完成、本机标 skipped 的干涉处理步;B 必须五步都在且均为 skipped
_CLOUD = (2, 3, 4, 5, 6)
# 分析链(register_sources 起)
_ANALYSIS_MIN = 20

# 对标 52 §8.3 UI 徽章;level 来自 product_level.classify
PRODUCT_LABELS = {
    "basic": "LOS · 相对参考点",
    "calibrated": "LOS · GNSS 锚定",
    "ortho": "垂直 / 东西向",
}

EMPTY_QA_CHIPS = {"crossval_r": None, "gnss_rmse_mm": None}


def empty_qa_chips() -> dict:
    """空壳 / 指标缺席时的 qa_chips(两键都在,值为 null,前端不用分支)。"""
    return dict(EMPTY_QA_CHIPS)


def _step_num(item: Any) -> int | None:
    if isinstance(item, dict):
        raw = item.get("step", item.get("step_id"))
    else:
        raw = getattr(item, "step", None)
        if raw is None:
            raw = getattr(item, "step_id", None)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n


def _state_of(item: Any) -> str:
    if isinstance(item, dict):
        raw = item.get("state")
    else:
        raw = getattr(item, "state", None)
    return "" if raw is None else str(raw)


def classify_access_mode(steps: Any) -> str | None:
    """根据步骤清单判定接入模式。

    - 无有效步骤 → None
    - 存在 step>=20 且没有 1..11 → "C"
    - 存在 1..11,且 2–6 五个号都在 steps 里且全部 state=="skipped" → "B"
    - 否则若有 1..11 → "A"(2–6 缺步或未齐 skipped 都是 A,不得因缺步当 B)
    """
    present: dict[int, list[str]] = {}
    for item in steps or ():
        n = _step_num(item)
        if n is None:
            continue
        present.setdefault(n, []).append(_state_of(item))
    if not present:
        return None
    has_core = any(n in _CORE for n in present)
    has_analysis = any(n >= _ANALYSIS_MIN for n in present)
    if has_analysis and not has_core:
        return "C"
    if not has_core:
        return None
    if all(
        n in present and all(s == "skipped" for s in present[n])
        for n in _CLOUD
    ):
        return "B"
    return "A"


def product_label(level: str | None) -> str | None:
    if level is None:
        return None
    return PRODUCT_LABELS.get(level)


def qa_chips_from_metrics(metrics: Any) -> dict:
    """从 qa.json(或等价 dict)取 chip 数值;缺席/非有限数为 null,不编造 0.92。"""
    return {
        "crossval_r": _chip_number(metrics, "crossval_r"),
        "gnss_rmse_mm": _chip_number(metrics, "gnss_rmse_mm"),
    }


def product_view(metrics: Any, artifacts: Any, *, simulated: bool = False) -> dict:
    """有 run 时的 product 对象:annotate() + §8.3 徽章文案。"""
    info = annotate(metrics, artifacts, simulated=simulated)
    level = info["level"]
    return {
        "level": level,
        "simulated": bool(info["simulated"]),
        "label": product_label(level),
    }


def degraded_basic_product(*, simulated: bool = False) -> dict:
    """qa.json 损坏时的降级:默认产出就是 Basic,simulated 标记照传。"""
    return {
        "level": "basic",
        "simulated": bool(simulated),
        "label": PRODUCT_LABELS["basic"],
    }


def _chip_number(metrics: Any, name: str) -> float | None:
    if not isinstance(metrics, dict):
        return None
    val = metrics.get(name)
    if isinstance(val, dict):
        val = val.get("value")
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f
