"""真数据验收:Ridgecrest 11 对 HyP3 → MintPy(Windows 原生,无 WSL)。

对应 AGENT-DESIGN §9 Phase 1 验收「用 11 对 HyP3 真实数据跑通 MintPy 链」。
云端(HyP3)已完成 2-6 步;本脚本驱动 agent 执行 1(导入)、7-9(MintPy)、
10(出图)、11(质检),全程走五阶段执行器 + 作业目录契约 + provenance。

用法:python scripts/real_ridgecrest.py [--fresh]
环境:INSAR_ENGINE_PREFIX(conda 环境)、INSAR_HYP3_SOURCE(数据源)可覆盖默认值。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("INSAR_ENGINE_PREFIX", r"E:\miniforge3\envs\insar")
os.environ.setdefault(
    "INSAR_HYP3_SOURCE",
    r"E:\01所有项目\06定职讲师\InSAR-Pro\insar-pro\backend\data\real_data\RidgecrestSenDT71")

from insar_agent.brain.facade import Brain  # noqa: E402
from insar_agent.core.db import Database  # noqa: E402
from insar_agent.core.store import Store  # noqa: E402
from insar_agent.loop.driver import Driver  # noqa: E402

HOME = ROOT / "workspace" / "realtest"


def fmt(e: dict) -> str:
    t = e.get("t")
    if t == "tool.log":
        return f"    | {e['line']}"
    if t == "tool.start":
        return f"  ▶ {e.get('verb', '')} {e.get('label', '')} $ {e.get('cmd', '')}"
    if t == "tool.end":
        return f"  ■ exit={e.get('exit')} {e.get('summary', '')}"
    if t in ("step.start", "step.end"):
        return f"[{t} {e.get('stepId')}]" + (f" exit={e['exit']}" if "exit" in e else "")
    if t == "note":
        return f"  ({e.get('tone')}) {e.get('text')}"
    if t == "say":
        return "AGENT: " + "".join(p if isinstance(p, str) else str(p) for p in e.get("parts", []))
    if t == "plan":
        return "PLAN: " + " / ".join(i["text"] for i in e.get("items", []))
    if t in ("thinking",):
        return f"THINKING: {e.get('title')}"
    return f"[{t}] " + json.dumps({k: v for k, v in e.items() if k != 't'},
                                  ensure_ascii=False)[:200]


async def main(fresh: bool) -> int:
    if fresh and HOME.exists():
        shutil.rmtree(HOME)
    HOME.mkdir(parents=True, exist_ok=True)
    db = Database(HOME / "insar.db")
    store = Store(db)
    driver = Driver(store, workspace=HOME / "ws", brain=Brain(None),
                    allow_simulated=False)  # 真实模式:不可行就失败,绝不静默模拟

    t0 = time.time()
    print("=" * 70)
    print("Phase A · 规划回合")
    print("=" * 70, flush=True)
    async for e in driver.turn("real", "Ridgecrest 地震同震形变分析"):
        print(fmt(e), flush=True)

    print("=" * 70)
    print("Phase B · 执行回合(1 导入 → 7-9 MintPy → 10 出图 → 11 质检)")
    print("=" * 70, flush=True)
    async for e in driver.execute("real"):
        print(fmt(e), flush=True)

    run = store.latest_run("real")
    print("=" * 70)
    print(f"RUN: {run['run_id']}  status={run['status']}  simulated={bool(run['simulated'])}"
          f"  wall={time.time() - t0:.0f}s")
    for s in store.load_steps(run["run_id"]):
        arts = store.artifacts_of(run["run_id"], s.step_id)
        print(f"  step {s.step_id:2d} {s.name:6s} {s.state:12s} stage={s.stage:9s}"
              f" run_ok={s.run_ok} artifacts={len(arts)}"
              + (f" failure={s.failure_class}" if s.failure_class else ""))

    from insar_agent.audit.contract import load_contract
    from insar_agent.core.ledger import export_provenance

    prov = export_provenance(store, run["run_id"], contract=load_contract(),
                             workspace=driver.workspace)
    print(f"EVIDENCE: {prov['evidence_level']}  (ceiling: {prov['evidence']['ceiling']}"
          f" — {prov['evidence']['ceiling_reason']})")
    print("METRICS:")
    for name, m in prov["metrics"].items():
        print(f"  {name} = {m['value']}  reparsed_ok={m['reparsed_ok']}"
              f"  ← {m['source_artifact']}#{m['source_field']}")
    if prov["warnings"]:
        print("WARNINGS:", *[f"  {w}" for w in prov["warnings"]], sep="\n")
    db.close()
    return 0 if run["status"] == "done" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(fresh="--fresh" in sys.argv)))
