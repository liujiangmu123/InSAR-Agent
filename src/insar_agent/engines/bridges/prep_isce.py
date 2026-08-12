"""官方桥:ISCE2/HyP3 → MintPy(prep_isce / prep_hyp3,直接调官方)。"""

from __future__ import annotations

from pathlib import Path


def build_prep_command(layout_from: str, workspace: Path) -> list[str]:
    if layout_from == "hyp3":
        return ["bash", "-lc", "cd mintpy && prep_hyp3.py ../hyp3/*/*.tif"]
    return ["bash", "-lc",
            "cd mintpy && prep_isce.py -f ../isce2/merged/interferograms -m ../isce2/reference"]
