"""HyP3 / ASF 数据获取薄封装(零决策)。

凭据纪律:绝不明文落盘(竞品反例 submit_insar.py:441)。凭据只从环境变量/
~/.netrc 读取,命令行里不出现。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from insar_agent.registry.model import Capability
from insar_agent.runtime.jobs import CommandPlan

_FETCH_PY = """\
# insar-agent 数据获取脚本(asf_search / hyp3_sdk;凭据走 ~/.netrc,不落盘)
import sys
print("search:", {dates!r}, "platform:", {platform!r}, "scenes:", {scenes!r}, flush=True)
try:
    import asf_search  # noqa: F401
except ImportError:
    print("ERROR: asf_search 未安装 —— 数据获取需要 pip install asf_search", flush=True)
    sys.exit(2)
# 实际检索与下载逻辑在真实环境下补全(需要 AOI 与凭据)
print("asf_search 就绪;等待 AOI 输入", flush=True)
"""


def build(*, cap: Capability, method: str, params: dict[str, Any], run: dict,
          workspace: Path) -> CommandPlan:
    script_rel = "fetch/fetch_data.py"
    content = _FETCH_PY.format(
        dates=str(params.get("dates", "")),
        platform=str(params.get("platform", "sentinel-1")),
        scenes=int(params.get("scenes", 0)),
    )
    return CommandPlan(
        argv=["python", script_rel],
        cwd=str(workspace),
        env={},
        files={script_rel: content},
        shell_line=f"python {script_rel}",
    )
