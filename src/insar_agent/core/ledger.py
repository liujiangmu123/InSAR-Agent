"""provenance 账本导出(AGENT-DESIGN §6.2;schema 蓝本:agentic-swmm audit_run.py)。"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from insar_agent.audit.contract import Threshold
from insar_agent.audit.ladder import cloud_evidence, compute_evidence
from insar_agent.core.fsio import atomic_write_text
from insar_agent.core.store import Store
from insar_agent.skills.loader import load_skills

SCHEMA_VERSION = "1.0"


def export_provenance(store: Store, run_id: str, *, contract: dict[str, Threshold],
                      workspace: Path | None = None) -> dict:
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    steps = store.load_steps(run_id)
    # workspace 传给阶梯:云端跳过的 manifest 证据参与证据级判定(#12),
    # evidence 段随之携带每步证据来源(step_sources)与父链验证清单(parent_validations)
    evidence = compute_evidence(store, run_id, contract, workspace=workspace)

    cloud_ev: dict | None = None  # 全部跳过步骤共享同一份云端证据,惰性求值一次
    skills = load_skills()  # 步骤技能:目录整扫一次,逐步查表(无技能目录=空表)
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
        skill = skills.get(s.step_id)
        if skill is not None:  # 技能版本随步入账(可复现契约);无技能不写字段
            steps_out[str(s.step_id)]["skill"] = {
                "name": skill.name, "version": skill.version,
                "content_hash": skill.content_hash}
        if s.state == "skipped":
            if cloud_ev is None:
                cloud_ev = cloud_evidence(workspace)
            steps_out[str(s.step_id)]["cloud_evidence"] = cloud_ev

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

    # 只导出归属本 run 的干预(REVIEW P1:全库查询会把所有历史 run 的干预混进
    # 任一账本,污染溯源)。run_id 为 NULL 的旧行/未定向行宁可不进账本也不错记
    # ——溯源的原则是"可归属才可声明"。
    interventions = [
        {"action": a["action"], "target": a["target"], "payload": a["payload"],
         "deliver_as": a["deliver_as"], "consumed_at": a["consumed_at"]}
        for a in store.db.query(
            "SELECT * FROM pending_actions WHERE consumed_at IS NOT NULL AND run_id=?"
            " ORDER BY id", (run_id,))
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
    atomic_write_text(target, json.dumps(doc, ensure_ascii=False, indent=1))
    return target
