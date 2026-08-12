"""PyStamps 薄封装(PS 链;依赖 ISCE2→PyStamps 桥,Phase 6 主 novelty)。

PyStamps 的三个已知约束(DESIGN.md:131-139),在桥里处理,这里只构建命令:
  - 路径字符偏移硬编码([nb-22:nb-14] 切日期):文件名必须严格 YYYYMMDD_YYYYMMDD.diff
  - 反选式文件发现:工作目录必须先清 .vrt/.hdr/.aux 残留(tier4 清理是正确性要求)
  - 全程 big-endian:桥必须输出 BE
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from insar_agent.registry.model import Capability
from insar_agent.runtime.jobs import CommandPlan


def build(*, cap: Capability, method: str, params: dict[str, Any], run: dict,
          workspace: Path) -> CommandPlan:
    script_rel = "pystamps/run_ps.sh"
    script = """#!/usr/bin/env bash
set -euo pipefail
# 前置:isce2_to_pystamps 桥已产出 pystamps 输入(见 engines/bridges)
find pystamps/work -name '*.vrt' -delete   # 反选式发现的残留清理(正确性要求)
find pystamps/work -name '*.hdr' -delete
find pystamps/work -name '*.aux*' -delete
cd pystamps/work
python -m pystamps --steps 1-8
"""
    return CommandPlan(
        argv=["bash", script_rel],
        cwd=str(workspace),
        env={},
        files={script_rel: script},
        shell_line=f"bash {script_rel}",
    )
