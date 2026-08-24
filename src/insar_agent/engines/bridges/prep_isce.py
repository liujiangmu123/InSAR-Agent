"""官方桥:ISCE2/HyP3 → MintPy(prep_isce / prep_hyp3,直接调官方)。

argv 由 tests/test_isce2_pystamps_bridge.py 锁定 hyp3 vs isce 两条路径;
本模块只拼装命令,不跑 prep(重型计算禁区)。
"""

from __future__ import annotations

from pathlib import Path


def build_prep_command(layout_from: str, workspace: Path) -> list[str]:
    """返回官方预备命令 argv。

    ``workspace`` 不进 argv(相对路径约定与 MintPy 工作目录一致),保留参数
    以对齐 Bridge.impl 签名。
    """
    if layout_from == "hyp3":
        return ["bash", "-lc", "cd mintpy && prep_hyp3.py ../hyp3/*/*.tif"]
    return ["bash", "-lc",
            "cd mintpy && prep_isce.py -f ../isce2/merged/interferograms -m ../isce2/reference"]
