"""PyStamps 薄封装(PS 链;依赖 ISCE2→PyStamps 桥)。

PyStamps 的三个已知约束(DESIGN.md:131-139),在桥里处理,这里只构建命令:
  - 路径字符偏移硬编码([nb-22:nb-14] 切日期):文件名必须严格 YYYYMMDD_YYYYMMDD.diff
  - 反选式文件发现:工作目录必须先清 .vrt/.hdr/.aux 残留(tier4 清理是正确性要求)
  - 全程 big-endian:桥必须输出 BE

build 出的脚本先调用 ``isce2_to_pystamps.convert``;失败则非 0 退出并打印
EnvironmentNotReady,成功后再 ``python -m pystamps``。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from insar_agent.registry.model import Capability
from insar_agent.runtime.jobs import CommandPlan

_BRIDGE_PY = '''\
# ISCE2→PyStamps 桥前置:失败则诚实非 0,不启动 pystamps
import sys
from pathlib import Path

_src = Path(__SRC_ROOT_REPR__)
if _src.is_dir() and str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from insar_agent.engines.bridges.isce2_to_pystamps import EnvironmentNotReady, convert

try:
    convert(Path(".").resolve())
except EnvironmentNotReady as exc:
    print(f"EnvironmentNotReady: {exc}", file=sys.stderr)
    raise SystemExit(1) from exc
print("ISCE2→PyStamps 桥完成", flush=True)
'''

_RUN_PS = '''\
#!/usr/bin/env bash
set -euo pipefail
# 前置:isce2_to_pystamps.convert;失败则 EnvironmentNotReady 非 0
"__HOST_PY__" pystamps/run_bridge.py
find pystamps/work -name '*.vrt' -delete   # 反选式发现的残留清理(正确性要求)
find pystamps/work -name '*.hdr' -delete
find pystamps/work -name '*.aux*' -delete
cd pystamps/work
python -m pystamps --steps 1-8
'''


def build(*, cap: Capability, method: str, params: dict[str, Any], run: dict,
          workspace: Path) -> CommandPlan:
    src_root = Path(__file__).resolve().parents[2]
    bridge_py = _BRIDGE_PY.replace("__SRC_ROOT_REPR__", repr(str(src_root)))
    run_ps = _RUN_PS.replace("__HOST_PY__", Path(sys.executable).as_posix())
    return CommandPlan(
        argv=["bash", "pystamps/run_ps.sh"],
        cwd=str(workspace),
        env={},
        files={
            "pystamps/run_bridge.py": bridge_py,
            "pystamps/run_ps.sh": run_ps,
        },
        shell_line="bash pystamps/run_ps.sh",
    )
