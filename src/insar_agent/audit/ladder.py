"""六级证据阶梯的机器可判定实现(AGENT-DESIGN §6.3)。

    runnable → checked → audited → calibrated → validated → publishable

原则:
  - 取最差(InSAR-Pro result_metadata.py:148 的 overall_tier):有一步降级则整体降级。
  - PENDING 阈值封顶 audited(§4.13):还有待标定的阈值就不能声称 validated。
  - simulated run 封顶 runnable:演示不冒充证据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from insar_agent.audit.contract import Threshold, pending_keys
from insar_agent.core.store import Store

LADDER = ("runnable", "checked", "audited", "calibrated", "validated", "publishable")


@dataclass
class EvidenceResult:
    level: str
    level_index: int
    reasons: list[str] = field(default_factory=list)  # 为什么停在这一级
    ceiling: str | None = None
    ceiling_reason: str = ""

    def to_dict(self) -> dict:
        return {"level": self.level, "level_index": self.level_index,
                "ladder": list(LADDER), "reasons": self.reasons,
                "ceiling": self.ceiling, "ceiling_reason": self.ceiling_reason}


def compute_evidence(store: Store, run_id: str,
                     contract: dict[str, Threshold]) -> EvidenceResult:
    run = store.get_run(run_id) or {}
    steps = store.load_steps(run_id)
    metrics = {m["name"]: m for m in store.metrics_of(run_id)}
    reasons: list[str] = []

    # 封顶判定
    ceiling: str | None = None
    ceiling_reason = ""
    pending = pending_keys(contract)
    if run.get("simulated"):
        ceiling = "runnable"
        ceiling_reason = "模拟执行(引擎缺失),演示结果不构成证据"
    elif pending:
        ceiling = "audited"
        ceiling_reason = f"{len(pending)} 项阈值待标定:{pending}"

    def capped(result: EvidenceResult) -> EvidenceResult:
        if ceiling is not None:
            cap_idx = LADDER.index(ceiling)
            if result.level_index > cap_idx:
                result.reasons.append(f"封顶 {ceiling}:{ceiling_reason}")
                result.level = ceiling
                result.level_index = cap_idx
        result.ceiling = ceiling
        result.ceiling_reason = ceiling_reason
        return result

    # L0 runnable:全部步骤 VERIFIED(或显式 skipped)
    incomplete = [s.step_id for s in steps
                  if s.stage != "VERIFIED" and s.state != "skipped"]
    if incomplete or not steps:
        reasons.append(f"未完成步骤:{incomplete or '无步骤'}")
        return capped(EvidenceResult("runnable", 0, reasons)) if not incomplete and steps \
            else EvidenceResult("runnable", 0, reasons, ceiling=ceiling,
                                ceiling_reason=ceiling_reason)

    # L1 checked:全部 run_ok
    not_ok = [s.step_id for s in steps if s.state != "skipped" and s.run_ok != 1]
    if not_ok:
        reasons.append(f"run_ok 未通过:{not_ok}")
        return capped(EvidenceResult("runnable", 0, reasons))

    # L2 audited:provenance 完整(产物有指纹,指标全部重解析通过)
    arts = store.artifacts_of(run_id)
    missing_fp = [a["art_id"] for a in arts if not a["fp"]]
    unparsed = [m["name"] for m in metrics.values() if m["reparsed_ok"] != 1]
    if missing_fp or unparsed:
        reasons.append(f"审计不完整 —— 缺指纹:{missing_fp};未重解析:{unparsed}")
        return capped(EvidenceResult("checked", 1, reasons))

    # L3 calibrated:存在 GNSS/水准比对
    if "gnss_rmse_mm" not in metrics:
        reasons.append("无 GNSS/水准外部比对记录")
        return capped(EvidenceResult("audited", 2, reasons))

    # L4 validated:双链交叉验证达标(阈值必须非 PENDING —— 已被封顶逻辑保证)
    cv = metrics.get("crossval_r")
    th = contract.get("corr_threshold")
    if cv is None or th is None or cv["value"] is None or float(cv["value"]) < float(th.value):  # type: ignore[arg-type]
        reasons.append("双链交叉验证缺失或未达契约阈值")
        return capped(EvidenceResult("calibrated", 3, reasons))

    # L5 publishable:证据边界表 + 跨环境复现记录(运行元数据里显式声明)
    intent = run.get("intent") or "{}"
    if "cross_env_reproduced" not in str(intent):
        reasons.append("无跨环境复现记录/证据边界表")
        return capped(EvidenceResult("validated", 4, reasons))

    return capped(EvidenceResult("publishable", 5, reasons))
