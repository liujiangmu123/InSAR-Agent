"""等价裸命令导出(DESIGN.md:765 第 3 条 baseline;AGENT-DESIGN §4.7 副产品)。

run.sh 按序拼接各步真实执行过的 cmd.sh —— 是执行过的那一份,不是重新生成的近似品。
脱离 agent 可复现整条链。
"""

from __future__ import annotations

from pathlib import Path

from insar_agent.core.fsio import atomic_write_text
from insar_agent.core.store import Store


def export_run_script(store: Store, run_id: str, workspace: Path) -> str:
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    lines = [
        "#!/usr/bin/env bash",
        "# insar-agent 等价裸命令脚本 —— 按序拼接真实执行过的每步 cmd.sh",
        f"# run_id: {run_id}",
        f"# scenario: {run['scenario']}",
        f"# simulated: {bool(run['simulated'])}",
        "set -euo pipefail",
        "",
    ]
    for step in store.load_steps(run_id):
        lines.append(f"# ── 第 {step.step_id} 步 · {step.name} · method={step.method} "
                     f"· state={step.state} ──")
        if step.state == "skipped":
            # skipped 兼有两种来源(云端 HyP3 已完成 / 人工跳过),store 未区分,如实并述
            lines.append("# (跳过:云端(HyP3)已完成或人工跳过 —— 本地无等价命令)")
            lines.append("")
            continue
        cmds = store.commands_of(run_id, step.step_id)
        if not cmds:
            lines.append("# (未执行 —— fork 复用或尚未运行)")
            lines.append("")
            continue
        settled = [c for c in cmds if c["exit_code"] is not None]
        chosen = settled[-1] if settled else cmds[-1]
        cmd_path = Path(chosen["cmd_path"]) if chosen["cmd_path"] else None
        if cmd_path and cmd_path.exists():
            body = cmd_path.read_text(encoding="utf-8").strip().splitlines()
            body = [line for line in body if not line.startswith("#!")]
            lines.extend(body)
        else:
            import json as _json

            argv = _json.loads(chosen["argv"])
            lines.append(" ".join(argv))
        lines.append("")
    return "\n".join(lines) + "\n"


def write_run_script(store: Store, run_id: str, workspace: Path) -> Path:
    text = export_run_script(store, run_id, workspace)
    target = workspace / "run.sh"
    atomic_write_text(target, text, newline="\n")
    return target
