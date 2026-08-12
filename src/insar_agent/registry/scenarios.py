"""场景规则(冻土→周期,地震→阶跃)。intent 的规则层依据,brain 拔除后仍可用。

吸收 pi skills 的渐进披露思路(PI_FRAMEWORK_ANALYSIS absorb-E7):
描述常驻候选清单,详情(选参依据/文献)后续外置为场景技能包目录。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Scenario:
    key: str
    label: str
    match: str  # 正则(规则层意图识别)
    chain: str  # SBAS | PS
    model: str  # 形变模型 method id
    pick_7: str  # 第 7 步方法
    diag: str
    reason: str
    data_ready: bool = False
    region: str = ""
    dates: str = ""
    scenes: str = ""
    step_overrides: dict = field(default_factory=dict)  # {step_id: {method?, params?}}
    # 云端(HyP3)已完成的步骤:计划时标 skipped,不做可行性检查,不执行
    cloud_completed: tuple = ()


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        key="quake",
        label="同震形变",
        match=r"地震|同震|quake|Ridgecrest|玛多",
        chain="SBAS",
        model="step",
        pick_7="mintpy_sbas",
        diag="同震形变量级大(数十 cm),HyP3 已提供解缠相位与相干性",
        reason="阶跃形变 → step(20190706) 模型;日期取自实测配置",
        data_ready=True,
        region="Ridgecrest(加州)",
        dates="2019-06-10 — 2019-08-15",
        scenes="7 个获取日期 / 11 个干涉对",
        step_overrides={
            9: {"method": "step", "params": {"step_date": "20190706T0320"}},
            # PS 链未建成前,交叉验证不可行 —— 诚实降级为真实统计质检
            11: {"method": "coherence_mask"},
        },
        # HyP3 产品已含解缠相位:配准/干涉/滤波/解缠(2-6)已由 ASF 云端完成
        cloud_completed=(2, 3, 4, 5, 6),
    ),
    Scenario(
        key="permafrost",
        label="冻土季节冻融",
        match=r"冻土|permafrost|玉树|青海|青藏",
        chain="SBAS",
        model="poly_periodic",
        pick_7="mintpy_sbas",
        diag="冻土区植被与季节冻融导致时间去相干严重,点状 PS 目标稀疏",
        reason="低相干面状形变 → SBAS 优于 PS;形变含季节冻融 → poly_periodic 模型",
        region="青海玉树",
        dates="2020-01 — 2023-12",
        scenes="数据待获取",
        step_overrides={
            9: {"method": "poly_periodic", "params": {"periods": [1, 0.5], "poly_order": 1}},
        },
    ),
    Scenario(
        key="landslide",
        label="滑坡点状目标",
        match=r"滑坡|landslide|雅鲁藏布",
        chain="PS",
        model="linear",
        pick_7="pystamps_ps",
        diag="陡坡地形几何畸变明显,裸岩区高相干点密集",
        reason="高相干点状目标 → PS 链;需 ISCE2→PyStamps 桥",
        region="雅鲁藏布江",
        dates="待定",
        scenes="数据待获取",
        step_overrides={
            7: {"method": "pystamps_ps"},
            9: {"method": "linear"},
        },
    ),
)


def classify_text(text: str) -> Scenario | None:
    """规则层场景识别。返回 None 表示无法判定(交给 LLM 或表单)。"""
    for sc in SCENARIOS:
        if re.search(sc.match, text, re.IGNORECASE):
            return sc
    return None


def scenario_of(key: str) -> Scenario | None:
    for sc in SCENARIOS:
        if sc.key == key:
            return sc
    return None
