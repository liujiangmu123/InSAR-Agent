"""provenance 账本导出(AGENT-DESIGN §6.2;schema 蓝本:agentic-swmm audit_run.py)。"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

from insar_agent.audit.contract import Threshold
from insar_agent.audit.ladder import compute_evidence
from insar_agent.core.store import Store

SCHEMA_VERSION = "1.0"

# 云端(HyP3)完成声明的本地证据候选(工作区相对路径,按序查找)
_CLOUD_MANIFEST_CANDIDATES = ("hyp3/hyp3_manifest.json", "hyp3_manifest.json")


def _cloud_evidence(workspace: Path | None) -> dict:
    """跳过步骤(云端已完成)的证据:HyP3 manifest 存在即引用并指纹,缺失如实声明。

    诚实原则:跳过 ≠ 免检 —— provenance 里必须能看到「凭什么说云端做过」,
    而不是一句无凭据的 skipped。
    """
    if workspace is None:
        return {"present": False, "detail": "未提供 workspace,无法查找云端证据"}
    for rel in _CLOUD_MANIFEST_CANDIDATES:
        p = workspace / rel
        if not p.is_file():
            continue
        raw = p.read_bytes()
        out: dict = {"present": True, "kind": "hyp3_manifest", "path": rel,
                     "sha256": hashlib.sha256(raw).hexdigest()}
        try:
            data = json.loads(raw.decode("utf-8"))
            out["entries"] = len(data) if isinstance(data, (list, dict)) else 0
        except (json.JSONDecodeError, UnicodeDecodeError):
            out["parse_error"] = True
        return out
    return {"present": False,
            "detail": "未找到 hyp3_manifest.json(云端完成声明缺本地证据)"}


def export_provenance(store: Store, run_id: str, *, contract: dict[str, Threshold],
                      workspace: Path | None = None) -> dict:
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    steps = store.load_steps(run_id)
    evidence = compute_evidence(store, run_id, contract)

    cloud_ev: dict | None = None  # 全部跳过步骤共享同一份云端证据,惰性求值一次
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
        if s.state == "skipped":
            if cloud_ev is None:
                cloud_ev = _cloud_evidence(workspace)
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
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    import os

    os.replace(tmp, target)
    return target
