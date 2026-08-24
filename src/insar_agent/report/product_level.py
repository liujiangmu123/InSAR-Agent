"""EGMS 对齐的位移产品分级:Basic / Calibrated / Ortho。

纯函数,不读盘、不编造。级别不得掩盖模拟:simulated=True 时 annotate 另带
标记,调用方必须把模拟警示与级别一起展示。
"""

from __future__ import annotations

import math
from typing import Any

LEVELS = ("basic", "calibrated", "ortho")


def _as_records(artifacts: Any) -> list[dict]:
    if artifacts is None:
        return []
    if isinstance(artifacts, dict):
        out: list[dict] = []
        for key, val in artifacts.items():
            if isinstance(val, dict):
                rec = dict(val)
                rec.setdefault("id", key)
                out.append(rec)
            else:
                out.append({"id": key, "path": str(val)})
        return out
    if isinstance(artifacts, (list, tuple)):
        out = []
        for item in artifacts:
            if isinstance(item, dict):
                out.append(item)
            else:
                out.append({"path": str(item)})
        return out
    return [{"path": str(artifacts)}]


def _metric_value(metrics: Any, name: str) -> Any:
    if metrics is None:
        return None
    if isinstance(metrics, dict):
        val = metrics.get(name)
        if isinstance(val, dict):
            return val.get("value")
        return val
    if isinstance(metrics, (list, tuple)):
        for item in metrics:
            if isinstance(item, dict) and item.get("name") == name:
                return item.get("value")
    return None


def _path_of(rec: dict) -> str:
    return str(rec.get("path") or rec.get("id") or "").replace("\\", "/")


def _is_ortho(artifacts: Any) -> bool:
    for rec in _as_records(artifacts):
        path = _path_of(rec)
        if path.rstrip("/").endswith("decomposed.h5") or "/decomposed.h5" in path:
            return True
        ds = rec.get("dataset") or rec.get("datasets") or ""
        if isinstance(ds, (list, tuple)):
            ds = " ".join(str(x) for x in ds)
        text = f"{ds} {rec.get('kind') or ''}".lower()
        if "vertical" in text or "east" in text:
            return True
        if rec.get("has_vertical"):
            return True
    return False


def _has_gnss_rmse(metrics: Any) -> bool:
    val = _metric_value(metrics, "gnss_rmse_mm")
    if val is None:
        return False
    try:
        f = float(val)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f)


def classify(metrics: Any, artifacts: Any) -> str:
    """返回 basic | calibrated | ortho(小写,对标 EGMS L2a/L2b/L3 形态)。"""
    if _is_ortho(artifacts):
        return "ortho"
    if _has_gnss_rmse(metrics):
        return "calibrated"
    return "basic"


def annotate(metrics: Any, artifacts: Any, *, simulated: bool = False) -> dict:
    """级别 + 模拟标记。模拟不得被级别徽章洗白。"""
    return {
        "level": classify(metrics, artifacts),
        "simulated": bool(simulated),
    }


def methods_section(metrics: Any, artifacts: Any, *, simulated: bool = False) -> list[str]:
    """methods.md 用的一小段「产品级别」(无裸数字,以免打绿溯源测试)。"""
    info = annotate(metrics, artifacts, simulated=simulated)
    level = info["level"]
    labels = {
        "basic": "相对参考点的 LOS(EGMS Basic 形态)",
        "calibrated": "GNSS 锚定后的绝对 LOS(EGMS Calibrated 形态)",
        "ortho": "垂直 + 东西向分解(EGMS Ortho 形态)",
    }
    lines = [
        "## 产品级别",
        "",
        f"- 本产物按 EGMS 形态标注为 **{level}**({labels[level]})。",
    ]
    if info["simulated"]:
        lines.append("- 本次为模拟执行:产品级别标注不掩盖模拟属性,不构成科学证据。")
    return lines
