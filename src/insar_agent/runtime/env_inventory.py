"""环境盘点:本地优先、WSL 兜底,区分「可用 / 可自动装 / 仅手动 / 前置未就绪」。

check_env 与自主循环开工预检共用本模块。WSL 里已有的 isce2/snaphu
算可用(作业本就路由到 WSL),不得再报缺失、更不得在 Windows 上硬装。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping

from insar_agent.runtime.install_guide import (
    ENGINE_ORDER,
    INSTALL_GUIDE,
    REQUIRED_ENGINES,
    plan_installs,
    resolve_engines,
)

if TYPE_CHECKING:
    from insar_agent.runtime.probe import ProbeResult

#: 槽位状态闭集
AVAILABLE = "available"
MISSING_INSTALLABLE = "missing_installable"
MISSING_MANUAL = "missing_manual"
MISSING_BLOCKED = "missing_blocked"


@dataclass(frozen=True)
class EngineSlot:
    name: str
    status: str
    where: str | None  # local | wsl
    version: str | None
    route: str | None  # conda | wsl | manual
    hint: str


def classify(probe: "ProbeResult | Mapping[str, str | None]") -> list[EngineSlot]:
    """七引擎逐项分类。resolve_engines 已做本地优先 + WSL 兜底。"""
    engines = resolve_engines(probe)
    wsl = getattr(probe, "wsl", None) or {}
    plans = {p["engine"]: p for p in plan_installs(probe, wsl)}
    slots: list[EngineSlot] = []
    for name in ENGINE_ORDER:
        ver = engines.get(name)
        if ver:
            where = "wsl" if "wsl" in str(ver).lower() else "local"
            slots.append(EngineSlot(name, AVAILABLE, where, str(ver), None, ""))
            continue
        plan = plans.get(name) or {}
        route = plan.get("recommend")
        unmet = list(plan.get("unmet") or [])
        hint = str(plan.get("why") or INSTALL_GUIDE.get(name, {}).get("hint") or "")
        if route == "manual":
            status = MISSING_MANUAL
        elif unmet:
            status = MISSING_BLOCKED
        elif route in ("conda", "wsl"):
            status = MISSING_INSTALLABLE
        else:
            status = MISSING_MANUAL
        slots.append(EngineSlot(name, status, None, None, route, hint))
    return slots


def open_installables(slots: list[EngineSlot]) -> list[str]:
    """可自动安装(conda/wsl 途径且前置未明确失败)的缺失引擎名。"""
    return [s.name for s in slots if s.status == MISSING_INSTALLABLE]


def required_ready(slots: list[EngineSlot]) -> bool:
    """主链必需项(mintpy/gdal)是否到位。可选引擎缺失不挡分析。"""
    by = {s.name: s for s in slots}
    return all(by[e].status == AVAILABLE for e in REQUIRED_ENGINES if e in by)


def inventory_text(probe: "ProbeResult | Mapping", *,
                   disk_gb: float | None = None, cpu_count: int | None = None) -> str:
    """check_env / 预检共用的人话摘要。WSL 在位的引擎进「可用」不进「缺失」。"""
    slots = classify(probe)
    ok, missing = [], []
    for s in slots:
        if s.status == AVAILABLE:
            loc = "WSL" if s.where == "wsl" else "本机"
            ok.append(f"{s.name} {s.version}({loc})")
        elif s.status == MISSING_INSTALLABLE:
            missing.append(f"{s.name}(可自动装/{s.route})")
        elif s.status == MISSING_BLOCKED:
            missing.append(f"{s.name}(前置未就绪)")
        else:
            missing.append(f"{s.name}(需手动)")
    if disk_gb is None:
        disk_gb = float(getattr(probe, "disk_free_gb", 0) or 0)
    if cpu_count is None:
        cpu_count = int(getattr(probe, "cpu_count", 0) or 0)
    ready = "主链就绪" if required_ready(slots) else "主链未就绪"
    return (f"{ready};可用:{'、'.join(ok) if ok else '无'};"
            f"缺失:{'、'.join(missing) if missing else '无'};"
            f"磁盘 {disk_gb:.0f} GB,CPU {cpu_count} 核")
