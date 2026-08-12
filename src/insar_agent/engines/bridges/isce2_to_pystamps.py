"""ISCE2 → PyStamps 桥(主 novelty,Phase 6 —— 当前为诚实接口边界)。

三个已知难点(DESIGN.md:123-132;AGENT-DESIGN §9 Phase 6):
  1. par 几何字段自洽(最大风险):range/azimuth pixel spacing、incidence、
     near/center/far range 必须互洽,错了 bperp 与高程误差全崩。
  2. TCN 基线反算:ISCE2 的基线产品 → GAMMA 风格 TCN 分量。
  3. 全程 big-endian:PyStamps 无条件 .byteswap(),桥输出必须 BE。

实现前置条件(本机尚不满足,§0.5.1):
  - WSL + ISCE2 环境(配准产物 merged/SLC + geom_reference)
  - 真实 Sentinel-1 数据(Ridgecrest 11 对可用于 SBAS,PS 需 ≥20 景)

在条件满足前,本模块的公开函数显式拒绝执行 —— 绝不产出以假乱真的桥产物。
"""

from __future__ import annotations

from pathlib import Path


class EnvironmentNotReady(RuntimeError):
    """桥的执行环境未就绪(WSL/ISCE2/数据任一缺失)。"""


REQUIRED_INPUTS = (
    "isce2/merged/SLC",            # 配准后 SLC 堆栈
    "isce2/merged/geom_reference", # lat/lon/hgt/los 几何
    "isce2/baselines",             # 基线(TCN 反算输入)
)

PLANNED_OUTPUTS = (
    "pystamps/work/*.diff",   # YYYYMMDD_YYYYMMDD.diff(严格命名,BE)
    "pystamps/work/*.par",    # GAMMA 风格参数文件(几何字段自洽)
    "pystamps/work/*.base",   # 基线文件(缺失会静默丢干涉图 —— 输入校验必须拦)
)


def check_ready(workspace: Path) -> list[str]:
    """返回缺失项列表;空 = 可以实现/执行。"""
    missing = [rel for rel in REQUIRED_INPUTS if not (workspace / rel).exists()]
    return missing


def convert(workspace: Path) -> None:
    missing = check_ready(workspace)
    if missing:
        raise EnvironmentNotReady(
            f"ISCE2→PyStamps 桥前置输入缺失:{missing}。"
            "该桥是 Phase 6 交付物,依赖 WSL + ISCE2 真实产物验证正确性"
            "(par 字段自洽/TCN 基线/big-endian 三个难点不允许在无真值环境下猜测实现)。")
    raise NotImplementedError(
        "Phase 6:在真实 ISCE2 产物上实现并验证(见模块 docstring 的三个难点)")
