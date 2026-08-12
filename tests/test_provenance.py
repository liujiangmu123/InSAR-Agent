"""Phase 3 验收:契约纪律、指标重解析、证据阶梯封顶、provenance/run.sh 导出。"""

from __future__ import annotations

import asyncio
import json

from insar_agent.audit.contract import load_contract, pending_keys
from insar_agent.audit.ladder import compute_evidence
from insar_agent.audit.verify import verify_metrics
from insar_agent.core.ledger import export_provenance
from insar_agent.core.store import Store
from insar_agent.registry.capabilities import PIPELINE
from insar_agent.report.methods import methods_markdown
from insar_agent.report.script import export_run_script

from tests.test_executor import (  # 复用执行器测试的脚手架
    GATE_JOB, _gate_cap, make_ctx, make_step, script_builder,
)
from insar_agent.runtime.executor import execute_step


def run_async(coro):
    return asyncio.run(coro)


# ---------------- 契约纪律(§4.13 的 CI 守护) ----------------

def test_contract_discipline():
    contract = load_contract()
    # 全部阈值有合法 source(load 已强制);literature/local_calibration 必有 ref
    for key, th in contract.items():
        assert th.source in ("upstream_default", "literature", "local_calibration")
        if th.source != "upstream_default":
            assert th.ref, f"{key} 缺 ref"
    # registry 里所有硬 gate 引用的阈值必须在台账里
    for cap in PIPELINE:
        for check in cap.quality_gate:
            if check.threshold_key and check.on_fail == "stop":
                assert check.threshold_key in contract, \
                    f"步骤 {cap.id} 的硬 gate 引用了台账外阈值 {check.threshold_key}"
    # 当前诚实状态:仍有 PENDING(标定完成前不许消失 —— 消失须走显式决策)
    assert "corr_threshold" in pending_keys(contract)


# ---------------- 端到端小链:执行 → 重解析 → 账本 ----------------

def _run_gate_pipeline(store: Store, workspace):
    """跑一个带 qa.json 的单步模拟链,返回 run_id。"""
    cap = _gate_cap("corr_threshold")
    registry = {cap.id: cap}
    make_step(store, cap)
    ctx = make_ctx(store, workspace, registry, builder=script_builder(GATE_JOB))
    result = run_async(execute_step(ctx, "r1", cap.id))
    assert result.outcome == "done"
    return "r1"


def test_verify_metrics_reparse(store, workspace):
    rid = _run_gate_pipeline(store, workspace)
    results = verify_metrics(store, rid, workspace)
    assert results and all(r["ok"] for r in results)
    metrics = {m["name"]: m for m in store.metrics_of(rid)}
    assert metrics["gate_metric"]["reparsed_ok"] == 1

    # 篡改产物 → 重解析必须发现不一致
    qa_path = workspace / "products" / "qa.json"
    qa_path.write_text(json.dumps({"gate_metric": 0.99}), encoding="utf-8")
    results2 = verify_metrics(store, rid, workspace)
    assert not results2[0]["ok"]


def test_evidence_simulated_capped_at_runnable(store, workspace):
    rid = _run_gate_pipeline(store, workspace)  # make_step 默认 simulated=True
    verify_metrics(store, rid, workspace)
    ev = compute_evidence(store, rid, load_contract())
    assert ev.level == "runnable"
    assert "模拟执行" in ev.ceiling_reason


def test_evidence_pending_capped_at_audited(store, workspace):
    """§4.13 连带修正:PENDING 阈值存在时,最高只能 audited,不能声称 validated。"""
    cap = _gate_cap("corr_threshold")
    registry = {cap.id: cap}
    make_step(store, cap, simulated=False)
    ctx = make_ctx(store, workspace, registry, builder=script_builder(GATE_JOB))
    assert run_async(execute_step(ctx, "r1", cap.id)).outcome == "done"
    verify_metrics(store, "r1", workspace)
    # 人为补齐 calibrated/validated 的条件
    store.record_metric("r1", "gnss_rmse_mm", value=0.4, source_artifact="qa.json",
                        source_field="gate_metric", reparsed_ok=True)
    store.record_metric("r1", "crossval_r", value=0.92, source_artifact="qa.json",
                        source_field="gate_metric", reparsed_ok=True)
    ev = compute_evidence(store, "r1", load_contract())
    assert ev.level == "audited"  # 被 PENDING 封顶
    assert "待标定" in ev.ceiling_reason


def test_provenance_export_complete(store, workspace):
    rid = _run_gate_pipeline(store, workspace)
    verify_metrics(store, rid, workspace)
    doc = export_provenance(store, rid, contract=load_contract(), workspace=workspace)

    assert doc["schema_version"] == "1.0"
    assert doc["simulated"] is True
    step = doc["steps"]["6"]
    assert step["eval_hash"] and step["run_ok"] == 1
    assert step["commands"] and step["commands"][0]["exit_code"] == 0
    assert doc["artifacts"]["qa_report"]["fp"]
    assert doc["metrics"]["gate_metric"]["reparsed_ok"] is True
    assert doc["evidence_level"] == "runnable"  # simulated 封顶
    assert doc["thresholds"]["corr_threshold"]["status"] == "PENDING"
    # PENDING 警告如实进 warnings
    assert any("PENDING" in w for w in doc["warnings"])


def test_provenance_on_failed_run(store, workspace):
    """审计在失败时同样产出(§1.6):失败步骤的 failure_class 与命令轨迹俱在。"""
    from insar_agent.registry.model import ArtifactSpec
    from tests.test_executor import custom_cap

    cap = custom_cap(artifacts=(ArtifactSpec("must", ("data/never.h5",)),))
    registry = {cap.id: cap}
    make_step(store, cap)
    ctx = make_ctx(store, workspace, registry, builder=script_builder("print('x', flush=True)\n"))
    assert run_async(execute_step(ctx, "r1", cap.id)).outcome == "failed"

    doc = export_provenance(store, "r1", contract=load_contract(), workspace=workspace)
    step = doc["steps"]["6"]
    assert step["state"] == "failed" and step["failure_class"] == "contract_broken"
    assert doc["qa"]["status"] == "fail"
    assert doc["evidence_level"] == "runnable"


def test_run_script_export(store, workspace):
    rid = _run_gate_pipeline(store, workspace)
    text = export_run_script(store, rid, workspace)
    assert "第 6 步" in text
    assert "python" in text  # 真实执行的命令行
    assert "set -euo pipefail" in text


def test_methods_markdown(store, workspace):
    rid = _run_gate_pipeline(store, workspace)
    verify_metrics(store, rid, workspace)
    doc = export_provenance(store, rid, contract=load_contract(), workspace=workspace)
    md = methods_markdown(doc)
    assert "模拟执行" in md          # simulated 免责声明
    assert "gate_metric" in md       # 指标表
    assert "证据边界" in md
    assert "corr_threshold" in md    # PENDING 阈值披露
