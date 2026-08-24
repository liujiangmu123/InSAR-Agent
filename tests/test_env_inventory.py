"""环境盘点:WSL 在位不算缺失;可自动装与仅手动分开。"""

from __future__ import annotations

from insar_agent.runtime.env_inventory import (
    AVAILABLE,
    MISSING_MANUAL,
    classify,
    inventory_text,
    open_installables,
    required_ready,
)
from insar_agent.runtime.install_guide import ENGINE_ORDER
from insar_agent.runtime.probe import ProbeResult


def _probe(engines=None, wsl=None):
    base = {e: None for e in ENGINE_ORDER}
    base.update(engines or {})
    return ProbeResult(engines=base, wsl=dict(wsl or {}), disk_free_gb=338.0, cpu_count=32)


def test_wsl_isce2_and_snaphu_are_available_not_missing():
    probe = _probe({"mintpy": "1.6.4", "gdal": "3.13", "pyaps": "0.3.7",
                    "isce2 (wsl)": "2.6.5", "snaphu (wsl)": "2.0.6"},
                   wsl={"installed": True, "distros": ["insar"]})
    slots = {s.name: s for s in classify(probe)}
    assert slots["isce2"].status == AVAILABLE
    assert slots["isce2"].where == "wsl"
    assert slots["snaphu"].status == AVAILABLE
    assert "isce2" not in open_installables(list(slots.values()))
    text = inventory_text(probe)
    assert "isce2" in text and "缺失" in text
    assert "isce2(可自动装" not in text
    assert "isce2" not in text.split("缺失:")[1]


def test_snap_and_pystamps_are_manual():
    probe = _probe({"mintpy": "1.6.4", "gdal": "3.13"})
    slots = {s.name: s for s in classify(probe)}
    assert slots["snap"].status == MISSING_MANUAL
    assert slots["pystamps"].status == MISSING_MANUAL
    assert required_ready(list(slots.values())) is True


def test_missing_conda_engine_is_installable():
    probe = _probe({}, wsl={"installed": True, "distros": ["insar"]})
    slots = classify(probe)
    names = open_installables(slots)
    assert "mintpy" in names
    assert "gdal" in names
    assert "snap" not in names
    assert required_ready(slots) is False
