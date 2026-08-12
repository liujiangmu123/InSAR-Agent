"""可行性收窄:此刻哪些候选可选(AGENT-DESIGN planner/feasibility)。

每个被排除的候选必须给出理由 —— 面板 9「候选收窄」的可解释性数据源(§7.2)。
"""

from __future__ import annotations

from dataclasses import dataclass

from insar_agent.registry.model import Capability, Method
from insar_agent.runtime.probe import ProbeResult


@dataclass(frozen=True)
class MethodFeasibility:
    method: Method
    ok: bool
    simulated: bool = False  # 引擎缺失但演示模式放行
    blocked_reason: str = ""


def narrow_methods(cap: Capability, probe: ProbeResult, *, scenario: str | None = None,
                   allow_simulated: bool = False) -> list[MethodFeasibility]:
    out: list[MethodFeasibility] = []
    for m in cap.methods:
        reasons: list[str] = []
        missing_engines = [e for e in (m.requires_engines or ())
                           if not probe.engine_ok(e)]
        if m.engine not in ("-", "") and not probe.engine_ok(m.engine):
            if m.engine not in missing_engines:
                missing_engines.append(m.engine)
        if missing_engines:
            reasons.append(f"工具链缺失:{','.join(missing_engines)}")
        missing_creds = [c for c in (m.requires_credentials or ())
                         if not probe.credentials.get(c)]
        if missing_creds:
            reasons.append(f"缺凭据:{','.join(missing_creds)}")
        if m.scenario_only and scenario and scenario not in m.scenario_only:
            reasons.append(f"场景不匹配(仅 {'/'.join(m.scenario_only)})")

        if not reasons:
            out.append(MethodFeasibility(m, True))
        elif allow_simulated and not any("场景不匹配" in r for r in reasons):
            # 演示模式:引擎/凭据类阻塞放行为「模拟执行」,但场景约束仍然生效
            out.append(MethodFeasibility(m, True, simulated=True,
                                         blocked_reason=";".join(reasons)))
        else:
            out.append(MethodFeasibility(m, False, blocked_reason=";".join(reasons)))
    return out
