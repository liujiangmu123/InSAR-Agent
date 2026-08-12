"""provenance 账本导出(AGENT-DESIGN §6.2;schema 蓝本:agentic-swmm audit_run.py)。"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from insar_agent.audit.contract import Threshold
from insar_agent.audit.ladder import compute_evidence
from insar_agent.core.store import Store

SCHEMA_VERSION = "1.0"


def export_provenance(store: Store, run_id: str, *, contract: dict[str, Threshold],
                      workspace: Path | None = None) -> dict:
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    steps = store.load_steps(run_id)
    evidence = compute_evidence(store, run_id, contract)

    steps_out: dict[str, dict] = {}
    for s in steps:
        upstream = [str(p) for p, c in store.edges(run_id) if c == s.step_id]
        commands = [{
            "argv": json.loads(c["argv"]), "exit_code": c["exit_code"],
            "duration": c["duration"], "attempt": c["attempt"], "cmd_path": c["cmd_path"],
        } for c in store.commands_of(run_id, s.step_id)]
        steps_out[str(s.step_id)] = {
            "name": s.name, "capability": s.capability, "method": s.method,
            "params": s.params, "task_hash": s.task_hash, "args_hash": s.args_hash,
            "local_hash": s.local_hash, "eval_hash": s.eval_hash, "upstream": upstream,
            "stage": s.stage, "state": s.state, "stale": s.stale,
            "stale_reason": s.stale_reason, "failure_class": s.failure_class,
            "run_ok": s.run_ok, "qa": s.qa, "exit_code": s.exit_code,
            "commands": commands,
        }

    artifacts_out = {}
    for a in store.artifacts_of(run_id):
        artifacts_out[a["art_id"]] = {
            "path": a["path"], "kind": a["kind"], "layout": a["layout"],
            "policy": a["policy"], "fp": a["fp"], "size": a["size"],
            "produced_by": a["step_id"],
        }

    metrics_out = {}
    for m in store.metrics_of(run_id):
        metrics_out[m["name"]] = {
            "value": m["value"], "unit": m["unit"],
            "source_artifact": m["source_artifact"], "source_field": m["source_field"],
            "reparsed_ok": bool(m["reparsed_ok"]) if m["reparsed_ok"] is not None else None,
        }

    interventions = [
        {"action": a["action"], "target": a["target"], "payload": a["payload"],
         "deliver_as": a["deliver_as"], "consumed_at": a["consumed_at"]}
        for a in store.db.query(
            "SELECT * FROM pending_actions WHERE consumed_at IS NOT NULL ORDER BY id")
    ]
    for item in interventions:
        if isinstance(item["payload"], str):
            item["payload"] = json.loads(item["payload"] or "{}")

    warnings: list[str] = []
    for s in steps:
        for check in s.qa or []:
            if check.get("severity") == "warn":
                warnings.append(f"step {s.step_id}: {check.get('detail', '')}")

    thresholds_out = {
        k: {"value": t.value, "source": t.source, "ref": t.ref, "status": t.status}
        for k, t in contract.items()
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "session_id": run["session_id"],
        "parent_run_id": run["parent_run_id"],
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "simulated": bool(run["simulated"]),
        "environment": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "tools": json.loads(run["tool_versions"] or "{}"),
        },
        "repo": {"git_head": run["git_head"], "git_dirty": run["git_dirty"]},
        "agent": {"agent_hash": run["agent_hash"]},
        "intent": json.loads(run["intent"] or "{}"),
        "scenario": run["scenario"],
        "steps": steps_out,
        "artifacts": artifacts_out,
        "metrics": metrics_out,
        "thresholds": thresholds_out,
        "qa": {
            "status": "pass" if all(
                (s.run_ok == 1 or s.state == "skipped") for s in steps) and steps else "fail",
        },
        "evidence": evidence.to_dict(),
        "evidence_level": evidence.level,
        "warnings": warnings,
        "interventions": interventions,
    }


def write_provenance(store: Store, run_id: str, *, contract, workspace: Path) -> Path:
    doc = export_provenance(store, run_id, contract=contract, workspace=workspace)
    target = workspace / "provenance.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    import os

    os.replace(tmp, target)
    return target
