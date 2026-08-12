"""auto 模式打分:候选集内挑一个(纯规则;LLM 的 select 只是另一个打分器)。"""

from __future__ import annotations

from insar_agent.planner.feasibility import MethodFeasibility


def pick_method(feasible: list[MethodFeasibility], *, prefer: str | None = None) -> MethodFeasibility | None:
    """优先级:显式指定(场景 override)> recommend 标记 > 首个可行。全不可行 → None。"""
    ok = [f for f in feasible if f.ok]
    if not ok:
        return None
    if prefer:
        for f in ok:
            if f.method.id == prefer:
                return f
    for f in ok:
        if f.method.recommend:
            return f
    return ok[0]
