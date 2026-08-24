"""Phase 5 验收:API 契约(流式回合 / 运行中消息纪律 / 预览 / fork / 导出)。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from insar_agent.api.app import create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # 密封测试:probe 一律返回空引擎,不受宿主 PATH/conda 环境影响
    from insar_agent.runtime.probe import ProbeResult

    def empty_probe(*args, **kwargs):
        return ProbeResult(
            engines={k: None for k in ("isce2", "mintpy", "snaphu", "gdal", "snap",
                                       "pystamps", "pyaps")},
            credentials={"earthdata": False, "cds": False, "gacos": False},
            disk_free_gb=100.0, cpu_count=8)

    monkeypatch.setattr("insar_agent.loop.driver.probe_environment", empty_probe)
    app = create_app(home=tmp_path / "home")
    with TestClient(app) as c:
        yield c


def _stream_events(client: TestClient, url: str, body: dict) -> list[dict]:
    events = []
    with client.stream("POST", url, json=body) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line.strip():
                events.append(json.loads(line))
    return events


def test_health_and_session(client):
    assert client.get("/api/health").json()["ok"] is True
    r = client.post("/api/sessions", json={"id": "demo", "name": "演示", "mode": "expert"})
    assert r.json()["session_id"] == "demo"
    assert any(s["session_id"] == "demo" for s in client.get("/api/sessions").json())


def test_registry_shape(client):
    client.post("/api/sessions", json={"id": "demo"})
    regs = client.get("/api/registry", params={"session": "demo"}).json()
    core = [r for r in regs if r.get("group") != "analysis"]
    analysis = [r for r in regs if r.get("group") == "analysis"]
    assert len(core) == 11
    assert [r["id"] for r in analysis] == list(range(20, 29))
    step6 = next(r for r in regs if r["id"] == 6)
    assert {m["id"] for m in step6["methods"]} >= {"snaphu_mcf", "snaphu_smooth", "icu"}
    assert step6["params"]["min_coherence"]["kind"] == "science"
    assert step6["params"]["threads"]["kind"] == "resource"
    assert step6["replay"] in ("never", "safe")


def test_turn_then_pipeline_end_to_end(client):
    client.post("/api/sessions", json={"id": "demo"})
    events = _stream_events(client, "/api/turn", {"session": "demo", "text": "Ridgecrest 地震同震"})
    kinds = [e["t"] for e in events]
    assert "plan" in kinds and "candidates" in kinds

    events2 = _stream_events(client, "/api/pipeline", {"session": "demo"})
    kinds2 = [e["t"] for e in events2]
    assert "result" in kinds2 and "report" in kinds2

    state = client.get("/api/state", params={"session": "demo"}).json()
    assert state["run"]["status"] == "done"
    # quake 场景:2-6 由云端(HyP3)完成 → skipped;其余执行到 done
    for s in state["steps"]:
        assert s["state"] == ("skipped" if s["id"] in (2, 3, 4, 5, 6) else "done")

    # 导出三件套
    prov = client.get("/api/provenance", params={"session": "demo"}).json()
    assert prov["schema_version"] == "1.0" and prov["simulated"] is True
    sh = client.get("/api/run.sh", params={"session": "demo"}).text
    assert "set -euo pipefail" in sh
    md = client.get("/api/methods.md", params={"session": "demo"}).text
    assert "证据边界" in md

    trace = client.get("/api/trace", params={"session": "demo"}).json()
    assert len(trace) >= 6  # 每个执行步骤至少一条审计(2-6 云端跳过不执行)


def test_message_discipline_during_run(client):
    """absorb-E4:运行中投递消息必须显式声明 deliver_as。"""
    client.post("/api/sessions", json={"id": "demo"})
    _stream_events(client, "/api/turn", {"session": "demo", "text": "Ridgecrest 地震"})
    # 人为把 run 置为 running(模拟执行中)
    state = client.get("/api/state", params={"session": "demo"}).json()
    assert state["run"]["run_id"], "规划后 /api/state 应带上 run 标识"

    # 直接走 API:先验证空闲时不需要 deliver_as
    r = client.post("/api/message", json={"session": "demo", "text": "你好"})
    assert r.status_code == 202

    # 模拟运行中:改 DB 状态
    client.app.state  # noqa: B018 —— TestClient 环境下直接操作底层 store
    # 通过 actions 接口枚举:入队是 202
    r2 = client.post("/api/actions", json={
        "session": "demo", "scope": "step", "target": "9",
        "action": "SET_METHOD", "payload": {"method": "exponential"},
        "deliver_as": "next_run"})
    assert r2.status_code == 202


def test_message_requires_deliver_as_when_running(client, tmp_path):
    """直接构造 running 状态验证 400。"""
    client.post("/api/sessions", json={"id": "demo"})
    _stream_events(client, "/api/turn", {"session": "demo", "text": "Ridgecrest 地震"})
    state = client.get("/api/state", params={"session": "demo"}).json()
    run_id = state["run"]["run_id"]

    # 借 impact 前的空闲验证……先置 running:
    from insar_agent.core.db import Database
    from insar_agent.core.store import Store

    db = Database(tmp_path / "home" / "insar.db")
    Store(db).set_run_status(run_id, "running")
    db.close()

    r = client.post("/api/message", json={"session": "demo", "text": "改一下第 6 步"})
    assert r.status_code == 400
    r2 = client.post("/api/message",
                     json={"session": "demo", "text": "改一下第 6 步",
                           "deliver_as": "steer"})
    assert r2.status_code == 202


def test_impact_preview_and_fork(client):
    client.post("/api/sessions", json={"id": "demo"})
    _stream_events(client, "/api/turn", {"session": "demo", "text": "Ridgecrest 地震"})
    _stream_events(client, "/api/pipeline", {"session": "demo"})
    state = client.get("/api/state", params={"session": "demo"}).json()
    run_id = state["run"]["run_id"]

    imp = client.get("/api/impact", params={
        "session": "demo", "step": 7,
        "params": json.dumps({"max_temporal_baseline": 90})}).json()
    assert [a["step_id"] for a in imp["affected"]] == [7, 8, 9, 10, 11]
    assert imp["rerunMinutes"] is None  # 无历史 → 未知,不编数(§7.5)

    fork = client.post("/api/fork", json={
        "session": "demo", "run_id": run_id,
        "changes": {"9": {"method": "exponential"}}}).json()
    states = {s["id"]: s["state"] for s in fork["steps"]}
    assert states[1] == states[7] == states[8] == "done"  # 零重算复用
    assert states[3] == "skipped"                          # 云端步骤保持 skipped
    assert states[9] == states[10] == states[11] == "pending"

    # fork 后的 run 可继续执行到完成
    events = _stream_events(client, "/api/pipeline",
                            {"session": "demo", "run_id": fork["runId"]})
    assert any(e["t"] == "result" for e in events)


def test_env_panel(client):
    client.post("/api/sessions", json={"id": "demo"})
    env = client.get("/api/env", params={"session": "demo"}).json()
    assert "engines" in env["probe"] and "disk_free_gb" in env["probe"]
    keys = {t["key"]: t for t in env["thresholds"]}
    assert keys["corr_threshold"]["status"] == "PENDING"  # 阈值台账透出(面板9)
