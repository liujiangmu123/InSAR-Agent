"""审计 / provenance API 契约测试(防契约漂移)。

锁定两个端点字段:
  - GET /api/provenance → run_id / parent_run_id / simulated / steps / qa /
                          thresholds + evidence.{level,level_index,ladder,
                          reasons,ceiling,ceiling_reason,step_sources,
                          parent_validations}
  - GET /api/env        → thresholds[{key,value,source,ref,status}]
旅程夹具参考 tests/test_e2e_contract.py:密封环境 + simulated 全链 done。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from insar_agent.api.app import create_app

# ---------------- auditlive.js 消费的字段清单(与前端一一对应) ----------------

# 后端六级词汇闭集(audit/ladder.py LADDER;审计面板级别词汇以此为准,顺序钉死)
LADDER = ("runnable", "checked", "audited", "calibrated", "validated", "publishable")

# auditlive.normalize() 读取 /api/provenance 的顶层键
PROV_TOP_FIELDS = {"run_id", "parent_run_id", "simulated", "steps", "thresholds",
                   "qa", "evidence"}
# auditlive.normalize() 读取 evidence.* 的键(EvidenceResult.to_dict 的输出契约)
EVIDENCE_FIELDS = {"level", "level_index", "ladder", "reasons",
                   "ceiling", "ceiling_reason", "step_sources", "parent_validations"}
# 每步证据来源条目的必需键(徽标渲染);origin 是闭集
STEP_SOURCE_FIELDS = {"origin", "source"}
ORIGINS = {"local", "inherited", "cloud", "missing"}
# 父链验证清单条目读取的键(名称/值/来源 run/参数指纹/「不继承」提示)
PARENT_VALIDATION_FIELDS = {"name", "value", "unit", "run_id", "args_hash", "note"}
# 阈值台账行(/api/env thresholds;provenance.thresholds 值对象少一个 key 字段)
THRESHOLD_FIELDS = {"key", "value", "source", "ref", "status"}
THRESHOLD_SOURCES = {"upstream_default", "literature", "local_calibration"}
# steps[sid] 里 auditlive 读取的键(来源行的名称与状态)
PROV_STEP_FIELDS = {"name", "state"}


# ---------------- 密封夹具:同 test_e2e_contract.py(simulated 秒级全链) ----------------

def _wsl_unreachable(*args, **kwargs):
    return {
        "ok": False, "distro": "insar", "error": "wsl.exe 不存在（未安装 WSL）",
        "engine_prefix": None,
        "engines": {name: {"present": False, "path": None, "version": None, "error": None}
                    for name in ("isce2", "mintpy", "snaphu")},
    }


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from insar_agent.runtime.probe import ProbeResult

    def empty_probe(*args, **kwargs):
        return ProbeResult(
            engines={k: None for k in ("isce2", "mintpy", "snaphu", "gdal", "snap",
                                       "pystamps", "pyaps")},
            credentials={"earthdata": False, "cds": False, "gacos": False},
            disk_free_gb=100.0, cpu_count=8)

    monkeypatch.setattr("insar_agent.loop.driver.probe_environment", empty_probe)
    # /api/env 会做 WSL 引擎探测:密封为不可达,不受宿主是否装 WSL 影响
    monkeypatch.setattr("insar_agent.runtime.wsl_probe.probe_wsl_engines",
                        _wsl_unreachable)
    for var in ("INSAR_LLM_BASE_URL", "INSAR_LLM_MODEL",
                "INSAR_LLM_FALLBACK_BASE_URL", "INSAR_LLM_FALLBACK_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("INSAR_ALLOW_SIMULATED", "1")
    app = create_app(home=tmp_path / "home")
    with TestClient(app) as c:
        yield c


def _drain(client: TestClient, url: str, body: dict) -> None:
    """消费 NDJSON 流至终态(事件内容的正确性由 test_e2e_contract.py 把关)。"""
    with client.stream("POST", url, json=body) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line.strip():
                json.loads(line)


def _execute_until_done(client: TestClient, session: str,
                        run_id: str | None = None, attempts: int = 3) -> dict:
    """执行流水线至 run 完成。

    高负载宿主上模拟作业可能超过 startup_grace 被判 orphaned → run 收尾
    interrupted(环境事件,非计算失败);断点续跑是产品语义(§7.8),
    重发 /api/pipeline 即从断点继续,不重跑已完成步骤。"""
    body = {"session": session} | ({"run_id": run_id} if run_id else {})
    last = None
    for _ in range(attempts):
        _drain(client, "/api/pipeline", body)
        params = {"session": session} | ({"run_id": run_id} if run_id else {})
        last = client.get("/api/state", params=params).json()
        if last["run"]["status"] == "done":
            return last
    raise AssertionError(f"{attempts} 轮续跑后 run 仍未完成:{last and last['run']}")


def _run_done_journey(client: TestClient, session: str = "audit-live") -> str:
    """造一个 simulated 全链 done 的 run(审计面板真实数据的前置条件)。"""
    r = client.post("/api/sessions", json={"id": session})
    assert r.status_code == 200
    _drain(client, "/api/turn",
           {"session": session, "text": "分析 Ridgecrest 2019 地震同震形变"})
    state = _execute_until_done(client, session)
    return state["run"]["run_id"]


# ---------------- GET /api/provenance:evidence 段(审计面板主数据源) ----------------

def test_provenance_evidence_contract_after_done_run(client):
    """run done 后 provenance 必须携带 auditlive.js 消费的全部字段。"""
    _run_done_journey(client)
    prov = client.get("/api/provenance", params={"session": "audit-live"}).json()

    # 顶层键(normalize 读取 run_id/simulated/qa.status/steps/thresholds)
    assert set(prov) >= PROV_TOP_FIELDS
    assert isinstance(prov["simulated"], bool) and prov["simulated"] is True
    assert prov["qa"]["status"] in ("pass", "fail")
    assert prov["parent_run_id"] is None  # 非 fork:父 run 为空

    ev = prov["evidence"]
    assert set(ev) >= EVIDENCE_FIELDS

    # 级别 ∈ 六级闭集;ladder 与后端词汇表逐项一致(顺序钉死,面板原样透传)
    assert ev["level"] in LADDER
    assert tuple(ev["ladder"]) == LADDER
    assert isinstance(ev["level_index"], int)
    assert ev["ladder"][ev["level_index"]] == ev["level"]

    # 「为何停在这一级」原因清单:字符串列表(面板逐行展示)
    assert isinstance(ev["reasons"], list)
    assert all(isinstance(r, str) for r in ev["reasons"])

    # simulated run 必须封顶 runnable(演示不冒充证据),且封顶必带原因文本
    assert ev["ceiling"] == "runnable" and ev["level"] == "runnable"
    assert ev["ceiling"] in LADDER
    assert isinstance(ev["ceiling_reason"], str) and ev["ceiling_reason"]

    # 每步证据来源:覆盖 run 的全部步骤,origin 闭集,source 已格式化
    sources = ev["step_sources"]
    assert isinstance(sources, dict) and sources
    assert set(sources) == set(prov["steps"]), "step_sources 必须覆盖全部步骤"
    for sid, src in sources.items():
        assert set(src) >= STEP_SOURCE_FIELDS, f"步骤 {sid} 来源缺必需键"
        assert src["origin"] in ORIGINS
        assert isinstance(src["source"], str) and src["source"]
        step = prov["steps"][sid]
        assert set(step) >= PROV_STEP_FIELDS
        if step["state"] == "skipped":
            # 密封工作区无 hyp3_manifest:云端完成声明必须如实标 missing + 缺口说明
            assert src["origin"] == "missing"
            assert src.get("detail")
        else:
            assert src["origin"] == "local"

    # 非 fork run:父链验证清单为空列表(字段本身必须在)
    assert isinstance(ev["parent_validations"], list)
    assert ev["parent_validations"] == []

    # provenance 内嵌阈值台账(env 不可用时 auditlive 的回落源):同款契约少 key 字段
    assert isinstance(prov["thresholds"], dict) and prov["thresholds"]
    for key, t in prov["thresholds"].items():
        assert set(t) >= (THRESHOLD_FIELDS - {"key"}), f"阈值 {key} 缺字段"
        assert str(t["status"]).upper() in ("OK", "PENDING")
        assert t["source"] in THRESHOLD_SOURCES


def test_provenance_404_without_run_is_demo_fallback_signal(client):
    """无 run 时 404:auditlive.fetchAuditLive 以此回落演示渲染(返回 null)。"""
    client.post("/api/sessions", json={"id": "audit-empty"})
    r = client.get("/api/provenance", params={"session": "audit-empty"})
    assert r.status_code == 404


# ---------------- GET /api/env:thresholds(审计面板阈值台账数据源) ----------------

def test_env_thresholds_contract(client):
    """阈值台账行五键齐全;status/source 是 PENDING 标橙与来源标签的分支依据。"""
    body = client.get("/api/env", params={"session": "audit-live"}).json()
    assert body["thresholds"], "阈值台账不应为空"
    for t in body["thresholds"]:
        assert set(t) >= THRESHOLD_FIELDS
        assert str(t["status"]).upper() in ("OK", "PENDING")
        assert t["source"] in THRESHOLD_SOURCES


def test_env_and_provenance_thresholds_are_same_contract(client):
    """auditlive 的回落等价性:两端点透出同一份契约(键集一致)。"""
    _run_done_journey(client, session="audit-thr")
    env = client.get("/api/env", params={"session": "audit-thr"}).json()
    prov = client.get("/api/provenance", params={"session": "audit-thr"}).json()
    assert {t["key"] for t in env["thresholds"]} == set(prov["thresholds"])


# ---------------- fork 场景:inherited 徽标与父链验证清单 ----------------

def test_fork_step_sources_inherited_and_parent_validations(client):
    """fork 复用步骤标 inherited(带父 run id);父链外部验证只列出、不继承。"""
    parent_run = _run_done_journey(client, session="audit-fork")

    # 改第 9 步方法 → fork → 只重跑受影响子集(9/10/11)
    fork = client.post("/api/fork", json={
        "session": "audit-fork", "run_id": parent_run,
        "changes": {"9": {"method": "exponential"}}}).json()
    assert fork["runId"] != parent_run
    _execute_until_done(client, "audit-fork", run_id=fork["runId"])

    prov = client.get("/api/provenance", params={
        "session": "audit-fork", "run_id": fork["runId"]}).json()
    assert prov["parent_run_id"] == parent_run
    ev = prov["evidence"]

    # 复用步骤(done 且未重跑)→ inherited + parent_run_id(徽标短 id 的数据源);
    # 重跑步骤(9/10/11)→ local;云端跳过步骤在密封工作区仍是 missing
    reused = {sid for sid, s in prov["steps"].items()
              if s["state"] == "done" and int(sid) < 9}
    assert reused, "fork 应复用第 9 步之前已完成的步骤"
    for sid, src in ev["step_sources"].items():
        state = prov["steps"][sid]["state"]
        if sid in reused:
            assert src["origin"] == "inherited", f"复用步骤 {sid} 应标 inherited"
            assert src.get("parent_run_id") == parent_run
            assert src["source"] == f"inherited(parent={parent_run})"
        elif state == "skipped":
            assert src["origin"] == "missing"
        else:
            assert src["origin"] == "local"

    # 父链验证清单:simulated 质检产出 crossval_* 指标 → 必须被列出且声明不继承
    pv = ev["parent_validations"]
    assert isinstance(pv, list) and pv, "父链有 crossval_* 指标,清单不应为空"
    for item in pv:
        assert set(item) >= PARENT_VALIDATION_FIELDS
        assert item["run_id"] == parent_run
        assert item["name"].startswith(("gnss_", "crossval_", "leveling_"))
        assert "重新验证" in item["note"]  # 「fork 后需重新验证」提示的数据源
    assert {i["name"] for i in pv} >= {"crossval_r"}
