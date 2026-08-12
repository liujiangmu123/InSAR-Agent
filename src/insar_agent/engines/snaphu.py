"""SNAPHU 薄封装:解缠配置渲染 + 逐对执行(零决策)。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from insar_agent.registry.model import Capability
from insar_agent.runtime.jobs import CommandPlan

_CONF = """\
# insar-agent 渲染的 SNAPHU 配置
STATCOSTMODE  {cost_mode}
CORRFILEFORMAT FLOAT_DATA
LOGFILE       snaphu.log
"""


def build(*, cap: Capability, method: str, params: dict[str, Any], run: dict,
          workspace: Path) -> CommandPlan:
    conf_rel = "params/snaphu.conf"
    script_rel = "snaphu/run_unwrap.sh"
    cost = str(params.get("cost_mode", "SMOOTH"))
    if method == "snaphu_smooth":
        cost = "SMOOTH"
    min_coh = params.get("min_coherence", 0.25)
    # 逐对解缠:任一对失败即停;已解缠的对保留(部分完成有价值,§1.6)
    script = f"""#!/usr/bin/env bash
set -euo pipefail
mkdir -p data/unw
for ifg in data/ifg_filt/*.int; do
  pair=$(basename "$ifg" .int)
  echo "unwrapping $pair (min_coherence={min_coh})"
  snaphu -f {conf_rel} "$ifg" $(cat data/ifg_filt/width.txt) \\
      -c "data/ifg_filt/$pair.cor" -o "data/unw/$pair.unw"
done
"""
    return CommandPlan(
        argv=["bash", script_rel],
        cwd=str(workspace),
        env={},
        files={conf_rel: _CONF.format(cost_mode=cost), script_rel: script},
        shell_line=f"bash {script_rel}",
    )
