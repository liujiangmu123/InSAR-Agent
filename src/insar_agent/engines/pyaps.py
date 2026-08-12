"""PyAPS(ERA5 大气校正)薄封装 —— 通过 MintPy 的 dostep 间接调用。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from insar_agent.engines import mintpy
from insar_agent.registry.model import Capability
from insar_agent.runtime.jobs import CommandPlan


def build(*, cap: Capability, method: str, params: dict[str, Any], run: dict,
          workspace: Path) -> CommandPlan:
    # 误差校正走 MintPy correct_troposphere(method 决定 cfg 里的 tropo 方法)
    return mintpy.build(cap=cap, method=method, params=params, run=run, workspace=workspace)
