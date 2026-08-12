"""契约台账文献 ref 落地守护(2026-08-12 调研收编,RESEARCH 报告映射表 a)。

守护对象:reference/RESEARCH-insar-params-2026-08-12.md 落地后的台账状态 ——
literature 条目必须给出含年份的可追溯引用;min_coherence 与 max_temporal_baseline
解除 PENDING;剩余 PENDING 收敛为 3 项且 ref 升级为「有参照系的待标定」;
证据阶梯的封顶清单同步变化(全绿 run 对照:解除前 5 项封顶,现 3 项,
剩余项标定完成后同一 run 可达 publishable)。
"""

from __future__ import annotations

import re

from insar_agent.audit.contract import Threshold, load_contract, pending_keys
from insar_agent.audit.ladder import compute_evidence
from insar_agent.registry.scenarios import scenario_of

# 调研后仍待本地标定的阈值(增减须走显式决策,并同步 contract.yaml 头注与本文件)
EXPECTED_PENDING = ["corr_threshold", "nan_fraction_below", "unwrap_coverage"]
# 本轮调研解除 PENDING 的阈值(文献锚点见 contract.yaml 对应 ref)
RESOLVED_BY_RESEARCH = ("min_coherence", "max_temporal_baseline")


def test_literature_entries_have_citable_ref():
    contract = load_contract()
    lit = {k: t for k, t in contract.items() if t.source == "literature"}
    assert set(lit) == set(RESOLVED_BY_RESEARCH)
    for key, th in lit.items():
        # contract.py 已强制非空;这里再守护「可追溯」:引用必须含文献年份
        assert th.ref.strip(), f"{key}: literature 必须填 ref"
        assert re.search(r"(19|20)\d{2}", th.ref), f"{key}: ref 缺可追溯年份: {th.ref}"


def test_ok_pending_partition_after_research():
    contract = load_contract()
    assert pending_keys(contract) == EXPECTED_PENDING
    ok = sorted(k for k, t in contract.items() if not t.pending)
    # OK 条目 5 项:原 3 项 upstream_default + 本轮解除的 2 项 literature
    assert ok == ["esd_coherence_threshold", "max_temporal_baseline",
                  "min_coherence", "stepFuncDate", "weightFunc"]


def test_min_coherence_pending_resolved_with_berardino_anchor():
    th = load_contract()["min_coherence"]
    assert not th.pending and th.status == "OK"
    assert th.source == "literature"
    # 直接文献锚点:Berardino et al. 2002 §V 明文 0.25 + GIAnT 惯例(Yunjun 2019 §6.5)
    assert "Berardino" in th.ref and "2002" in th.ref and "0.25" in th.ref
    assert "Yunjun" in th.ref
    assert th.value == 0.25  # 解除 PENDING 未动阈值本身:文献锚点即现值


def test_max_temporal_baseline_literature_and_scenario_divergence():
    th = load_contract()["max_temporal_baseline"]
    assert not th.pending and th.source == "literature"
    assert "Yunjun" in th.ref and "2019" in th.ref  # 宽松阈值+冗余网络的官方主张
    assert "24" in th.ref                           # 同数据源 ASF Ridgecrest 教程对照值
    assert th.value == 120
    # 场景差异(跨冻融季应收紧)按调研结论落在 permafrost 场景包领域知识段
    knowledge = scenario_of("permafrost").knowledge()
    assert "120" in knowledge and "收紧" in knowledge


def test_remaining_pending_refs_upgraded_to_reference_frame():
    contract = load_contract()
    for key in EXPECTED_PENDING:
        th = contract[key]
        assert th.pending
        assert th.ref.strip(), f"{key}: PENDING 也必须有 ref"
        # ref 从「待查文献/待定」升级为「有文献参照系的待标定」,不再是空泛占位
        assert "待查" not in th.ref and th.ref != "待定", f"{key}: {th.ref}"
    # corr_threshold 维持 local_calibration,参照系按互检惯例用速度差 std 量级表述
    corr = contract["corr_threshold"]
    assert corr.source == "local_calibration" and "mm/yr" in corr.ref
    # unwrap_coverage 的参照系:LiCSBAS 0.3 下限惯例 + 本项目从严说明
    assert "0.3" in contract["unwrap_coverage"].ref
    assert "从严" in contract["unwrap_coverage"].ref


def test_evidence_ceiling_tracks_resolved_thresholds(store):
    """全绿 run 的 ceiling 对照:封顶清单只剩 3 项,解除项不再出现;
    剩余 PENDING 假设标定完成后,同一 run 不再封顶、可达 publishable。"""
    from tests.test_audit_deep import _validated_state

    _validated_state(store, intent={"cross_env_reproduced": True})
    contract = load_contract()

    ev = compute_evidence(store, "r1", contract)
    assert (ev.level, ev.ceiling) == ("audited", "audited")
    assert f"{len(EXPECTED_PENDING)} 项阈值待标定" in ev.ceiling_reason
    for key in EXPECTED_PENDING:
        assert key in ev.ceiling_reason
    for key in RESOLVED_BY_RESEARCH:  # 调研前这里是 5 项(含下列两项)
        assert key not in ev.ceiling_reason

    # 对照:剩余 PENDING 全部转 OK → 同一 run 无封顶,满足 publishable 全部条件
    all_ok = {k: (Threshold(key=t.key, value=t.value, source=t.source,
                            ref=t.ref, status="OK") if t.pending else t)
              for k, t in contract.items()}
    ev2 = compute_evidence(store, "r1", all_ok)
    assert ev2.ceiling is None
    assert ev2.level == "publishable"
