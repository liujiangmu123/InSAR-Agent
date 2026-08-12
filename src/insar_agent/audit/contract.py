"""阈值台账加载与来源纪律(AGENT-DESIGN §4.13)。"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources

import yaml

VALID_SOURCES = ("upstream_default", "literature", "local_calibration")


@dataclass(frozen=True)
class Threshold:
    key: str
    value: object
    source: str
    ref: str
    status: str  # OK | PENDING

    @property
    def pending(self) -> bool:
        return self.status.upper() == "PENDING"


def load_contract(text: str | None = None) -> dict[str, Threshold]:
    if text is None:
        text = (resources.files("insar_agent.audit") / "contract.yaml").read_text("utf-8")
    raw = yaml.safe_load(text)
    out: dict[str, Threshold] = {}
    for key, spec in (raw.get("thresholds") or {}).items():
        source = spec.get("source", "")
        if source not in VALID_SOURCES:
            raise ValueError(f"threshold {key}: 非法 source {source!r}(拍脑袋值禁止入库)")
        if source in ("literature", "local_calibration") and not spec.get("ref"):
            raise ValueError(f"threshold {key}: {source} 必须填 ref")
        out[key] = Threshold(
            key=key, value=spec.get("value"), source=source,
            ref=spec.get("ref", ""), status=str(spec.get("status", "OK")))
    return out


def pending_keys(contract: dict[str, Threshold]) -> list[str]:
    return sorted(k for k, t in contract.items() if t.pending)
