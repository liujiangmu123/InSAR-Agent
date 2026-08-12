"""配置渲染:CommandPlan.files → 工作区(原子写)+ 配置哈希。

cmd.sh / 引擎配置都是渲染产物,可 diff、可复现(§4.7 副产品:等价裸命令)。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from insar_agent.runtime.jobs import CommandPlan


def render_plan_files(workspace: Path, plan: CommandPlan) -> str:
    """把 plan.files 写进工作区(tmp+replace 原子写),返回全部内容的聚合哈希。"""
    h = hashlib.sha256()
    for rel in sorted(plan.files):
        content = plan.files[rel]
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8", newline="\n")
        os.replace(tmp, target)
        h.update(rel.encode())
        h.update(b"\x00")
        h.update(content.encode())
        h.update(b"\x00")
    # argv 也参与配置哈希:同样的文件、不同的命令行 = 不同配置
    for arg in plan.argv:
        h.update(arg.encode())
        h.update(b"\x00")
    return h.hexdigest()
