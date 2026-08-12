"""layout 转换桥声明(纯数据)。桥的实现在 engines/bridges/。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Bridge:
    id: str
    from_layout: str
    to_layout: str
    impl: str  # engines.bridges 模块内的入口
    official: bool  # 官方工具还是自建桥
    notes: str = ""


BRIDGES = (
    Bridge(
        id="prep_isce",
        from_layout="isce2",
        to_layout="mintpy_h5",
        impl="insar_agent.engines.bridges.prep_isce",
        official=True,
        notes="MintPy 官方 prep_isce,直接调用",
    ),
    Bridge(
        id="isce2_to_pystamps",
        from_layout="isce2",
        to_layout="pystamps",
        impl="insar_agent.engines.bridges.isce2_to_pystamps",
        official=False,
        notes="主 novelty 桥。三个已知难点:par 几何字段自洽 / TCN 基线反算 / 全程 big-endian"
              "(DESIGN.md:123-132)。",
    ),
    Bridge(
        id="hyp3_to_mintpy",
        from_layout="hyp3",
        to_layout="mintpy_h5",
        impl="insar_agent.engines.bridges.prep_isce",
        official=True,
        notes="MintPy prep_hyp3,Ridgecrest 真实数据路径",
    ),
)
