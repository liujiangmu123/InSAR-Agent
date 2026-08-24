"""OPERA Dolphin PS/DS 混合相位连接(第 7 步实验方法 dolphin_ps_ds)。

输出约定:本方法只负责 wrapped phase / 干涉栈(dolphin 工作目录),
不假装写出 velocity.h5 或 timeseries.h5。后续仍接 MintPy 第 8-11 步
(需把 load.processor / 文件 glob 指到 Dolphin 产物,由场景或干预覆写)。

命令形态对齐上游 CLI:`dolphin config` → `dolphin run`(两条 argv,Windows
可跑,不经 bash -lc)。dolphin 不在 PATH 时 resolve_builder 仍返回本构建器;
运行期 CLI 缺失则非 0 失败 —— 不在 resolve 阶段 ToolMissing,以便命令快照可测。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from insar_agent.registry.model import Capability
from insar_agent.runtime.jobs import CommandPlan, shell_quote, wrapper_python

_RUNNER = '''\
# insar-agent dolphin runner(零决策,argv 已烘焙;不经 bash)
import subprocess, sys
from pathlib import Path
Path("dolphin").mkdir(parents=True, exist_ok=True)
JOBS = {jobs!r}
for argv in JOBS:
    print("RUN", " ".join(argv), flush=True)
    r = subprocess.run(argv)
    if r.returncode:
        sys.exit(r.returncode)
print("OK dolphin_ps_ds (wrapped phase / interferogram stack only; no velocity.h5)",
      flush=True)
'''


def build(*, cap: Capability, method: str, params: dict[str, Any], run: dict,
          workspace: Path) -> CommandPlan:
    if method != "dolphin_ps_ds":
        raise ValueError(f"dolphin 无此方法:{method}")
    slc = str(params.get("source") or params.get("slc_dir") or "data/slc").strip() or "data/slc"
    work = str(params.get("dolphin_work_dir") or "dolphin").strip() or "dolphin"
    cfg = f"{work}/dolphin_config.yaml"
    jobs = [
        ["dolphin", "config", "--slc-files", slc, "--work-directory", work, "-o", cfg],
        ["dolphin", "run", cfg],
    ]
    script_rel = "dolphin/run_dolphin.py"
    argv = [wrapper_python(), "-X", "utf8", script_rel]
    return CommandPlan(
        argv=argv,
        cwd=str(workspace),
        env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        files={script_rel: _RUNNER.format(jobs=jobs)},
        shell_line=" ".join(shell_quote(a) for a in argv),
    )
