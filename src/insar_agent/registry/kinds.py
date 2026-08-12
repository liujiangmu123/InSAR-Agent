"""DataKind(kind × layout × crs)声明 —— 桥的可行性判定依据(DESIGN §2)。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DataKind:
    kind: str  # SLC | DEM | RSLC | IFG_WRAPPED | IFG_UNWRAPPED | TIMESERIES | VELOCITY | FIGURE | REPORT | CONFIG | PROVENANCE
    layout: str = ""  # isce2 | mintpy_h5 | pystamps | hyp3 | gamma_par
    crs: str = ""  # radar | wgs84 | utm


KINDS = {
    "SLC": DataKind("SLC", "isce2", "radar"),
    "DEM": DataKind("DEM", "isce2", "wgs84"),
    "RSLC": DataKind("RSLC", "isce2", "radar"),
    "IFG_WRAPPED": DataKind("IFG_WRAPPED", "isce2", "radar"),
    "IFG_UNWRAPPED": DataKind("IFG_UNWRAPPED", "isce2", "radar"),
    "TIMESERIES": DataKind("TIMESERIES", "mintpy_h5", "radar"),
    "VELOCITY": DataKind("VELOCITY", "mintpy_h5", "wgs84"),
    "FIGURE": DataKind("FIGURE", "", "wgs84"),
    "REPORT": DataKind("REPORT", "", ""),
    "CONFIG": DataKind("CONFIG", "", ""),
    "PROVENANCE": DataKind("PROVENANCE", "", ""),
}
