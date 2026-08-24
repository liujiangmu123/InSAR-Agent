"""pi 桥接端点契约(src/insar_agent/api/bridge_router.py)。

覆盖:
  - GET /api/monitor 数据面:步骤矩阵(state/stage/stage_letter)、进度百分比、
    脏标(taint)计数、当前步识别、证据键存在、模式回显;
  - GET/POST /api/mode:free|strict 切换与非法值 400、持久化;
  - 端到端:/api/turn(规划)+ /api/pipeline(模拟执行)后,/api/monitor 如实
    反映 run 终态(status=done、进度 100、证据级为 runnable 家族)。

这些是无 LLM 密钥即可跑的路径(模拟引擎 engines/simulate.py 诚实跑全 11 步)。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from insar_agent.api.app import create_app
from insar_agent.core.db import Database
from insar_agent.core.store import Store

_HASHES = {"task_hash": "t0", "args_hash": "a0",
           "local_hash": "l0", "eval_hash": "e0" * 6}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # 密封 probe:本文件端到端必须走模拟引擎,禁止宿主 MintPy 真跑
    from insar_agent.runtime.probe import ProbeResult

    def empty_probe(*args, **kwargs):
        return ProbeResult(
            engines={k: None for k in ("isce2", "mintpy", "snaphu", "gdal", "snap",
                                       "pystamps", "pyaps")},
            credentials={"earthdata": False, "cds": False, "gacos": False},
            disk_free_gb=100.0, cpu_count=8)

    monkeypatch.setattr("insar_agent.loop.driver.probe_environment", empty_probe)
    monkeypatch.setenv("INSAR_ALLOW_SIMULATED", "1")
    home = tmp_path / "home"
    app = create_app(home=home)
    with TestClient(app) as c:
        c._home = home  # type: ignore[attr-defined]
        yield c


def _seed_run(home):
    """sess-m 名下一个 run,5 步覆盖 done/skipped/running/pending + 一个脏标。"""
    store = Store(Database(home / "insar.db"))
    ws = home / "sessions" / "sess-m"
    rid = "20260814T120000-monitor0"
    store.create_run(rid, "sess-m", workspace=str(ws), scenario="quake")
    for sid in range(1, 6):
        store.upsert_step(rid, sid, capability=f"cap{sid}", name=f"步骤{sid}",
                          method="m_x", params={}, hashes=_HASHES)
    # 显式设定 stage/state/stale(确定性,不依赖真实执行)
    seed = [
        (1, "VERIFIED", "done", 0),
        (2, "PREPARED", "skipped", 0),
        (3, "RUNNING", "running", 0),
        (4, "PREPARED", "pending", 0),
        (5, "VERIFIED", "done", 1),   # 脏标:外部改动/级联标脏
    ]
    with store.db.tx() as cur:
        for sid, stage, state, stale in seed:
            cur.execute("UPDATE steps SET stage=?, state=?, stale=? "
                        "WHERE run_id=? AND step_id=?", (stage, state, stale, rid, sid))
    return rid


def test_monitor_contract(client):
    client.post("/api/sessions", json={"id": "sess-m"})
    rid = _seed_run(client._home)

    data = client.get("/api/monitor", params={"session": "sess-m"}).json()
    assert data["session"] == "sess-m"
    assert data["run"]["run_id"] == rid
    assert data["run"]["scenario"] == "quake"
    assert len(data["steps"]) == 5

    # 进度:done+skipped = 3/5 = 60%
    assert data["progress"] == {"total": 5, "done": 3, "pct": 60}
    # 脏标计数(step5 stale)
    assert data["taints"] == 1
    # 当前步 = 第一个 running
    assert data["current"]["step"] == 3
    # 五阶段单字母映射
    letters = {s["step"]: s["stage_letter"] for s in data["steps"]}
    assert letters[1] == "V" and letters[3] == "R" and letters[4] == "P"
    # 无 commands 行时耗时/尝试次数为空默认(真实耗时来自台账,不是 trace 跨度)
    for s in data["steps"]:
        assert s["duration"] is None and s["attempts"] == 0
        assert {"duration", "attempts"} <= set(s)
    # 证据键存在(值可能为 None,取决于工作区)
    assert "evidence" in data
    # 默认模式 free(会话闸门,不是接入模式)
    assert data["mode"] == "free"
    # 5 步 core 且 2–6 未齐 skipped → 全链 A;无 qa.json → Basic + chips 缺席
    assert data["access_mode"] == "A"
    assert data["product"]["level"] == "basic"
    assert data["product"]["label"] == "LOS · 相对参考点"
    assert data["product"]["simulated"] is False
    assert data["qa_chips"] == {"crossval_r": None, "gnss_rmse_mm": None}


def test_monitor_empty_session(client):
    client.post("/api/sessions", json={"id": "sess-empty"})
    data = client.get("/api/monitor", params={"session": "sess-empty"}).json()
    assert data["run"] is None
    assert data["steps"] == []
    assert data["progress"]["pct"] == 0
    assert data["mode"] == "free"
    # 空壳也带新键,避免前端按有无 run 分支
    assert data["access_mode"] is None
    assert data["product"] is None
    assert data["qa_chips"] == {"crossval_r": None, "gnss_rmse_mm": None}


def test_mode_toggle_and_persist(client):
    client.post("/api/sessions", json={"id": "sess-mode"})
    # 默认 free
    assert client.get("/api/mode", params={"session": "sess-mode"}).json()["mode"] == "free"
    # 切 strict
    r = client.post("/api/mode", json={"session": "sess-mode", "mode": "strict"})
    assert r.status_code == 200 and r.json()["mode"] == "strict"
    assert client.get("/api/mode", params={"session": "sess-mode"}).json()["mode"] == "strict"
    # 非法值 400
    bad = client.post("/api/mode", json={"session": "sess-mode", "mode": "wild"})
    assert bad.status_code == 400
    # 切回 free
    client.post("/api/mode", json={"session": "sess-mode", "mode": "free"})
    assert client.get("/api/mode", params={"session": "sess-mode"}).json()["mode"] == "free"


def test_monitor_reflects_simulated_run(client):
    """端到端:规划 + 模拟执行后,/api/monitor 反映终态(无 LLM 密钥)。"""
    sess = "sess-e2e"
    client.post("/api/sessions", json={"id": sess})
    # 规划一回合(引擎缺失 → 模拟执行,规则路径,不需 LLM)
    client.post("/api/turn", json={"session": sess, "text": "Ridgecrest 地震同震形变"})
    runs = client.get("/api/runs", params={"session": sess}).json()["runs"]
    assert runs, "规划应产生一个 run"
    rid = runs[0]["run_id"]
    # 执行整条模拟流水线
    client.post("/api/pipeline", json={"session": sess, "run_id": rid})

    data = client.get("/api/monitor", params={"session": sess, "run_id": rid}).json()
    assert data["run"]["run_id"] == rid
    assert data["run"]["simulated"] is True
    assert data["run"]["status"] == "done"
    assert data["progress"]["pct"] == 100
    # 模拟执行证据级封顶 runnable(证据阶梯诚实性)
    assert data["evidence"] is not None
    assert data["evidence"]["level"] == "runnable"


def test_monitor_duration_from_last_settled_command(client):
    """耗时取 commands 最后一条已结算行;attempts 计全部尝试(RESET 不删旧行)。"""
    client.post("/api/sessions", json={"id": "sess-m"})
    rid = _seed_run(client._home)
    store = Store(Database(client._home / "insar.db"))
    store.record_command(rid, 1, ["echo", "fail"], exit_code=1, duration=0.5, attempt=1)
    store.record_command(rid, 1, ["echo", "ok"], exit_code=0, duration=1.9, attempt=2)

    data = client.get("/api/monitor", params={"session": "sess-m"}).json()
    by_step = {s["step"]: s for s in data["steps"]}
    assert by_step[1]["duration"] == pytest.approx(1.9)
    assert by_step[1]["attempts"] == 2
    assert by_step[2]["duration"] is None
    assert by_step[2]["attempts"] == 0


def test_monitor_analysis_run_is_access_mode_c(client):
    """analysis run 只排 20+,无 1–11 → C(侧栏不得把核心组当成 pending)。"""
    client.post("/api/sessions", json={"id": "sess-c"})
    store = Store(Database(client._home / "insar.db"))
    ws = client._home / "sessions" / "sess-c"
    rid = "run-analysis-c"
    store.create_run(rid, "sess-c", workspace=str(ws), scenario="quake")
    store.upsert_step(rid, 20, capability="register_sources", name="登记源",
                      method="register_sources", params={}, hashes=_HASHES)
    store.mark_step(rid, 20, state="done")

    data = client.get("/api/monitor", params={"session": "sess-c"}).json()
    assert data["access_mode"] == "C"
    assert all(s["step"] >= 20 for s in data["steps"])
    assert data["product"]["level"] == "basic"
    assert data["qa_chips"] == {"crossval_r": None, "gnss_rmse_mm": None}


def test_monitor_product_calibrated_from_qa_json(client):
    client.post("/api/sessions", json={"id": "sess-m"})
    rid = _seed_run(client._home)
    qa_path = (client._home / "sessions" / "sess-m" / "products" / "report" / "qa.json")
    qa_path.parent.mkdir(parents=True, exist_ok=True)
    qa_path.write_text(json.dumps({"gnss_rmse_mm": 4.25, "crossval_r": 0.87}),
                       encoding="utf-8")

    data = client.get("/api/monitor", params={"session": "sess-m"}).json()
    assert data["run"]["run_id"] == rid
    assert data["access_mode"] == "A"
    assert data["product"]["level"] == "calibrated"
    assert data["product"]["label"] == "LOS · GNSS 锚定"
    assert data["qa_chips"]["gnss_rmse_mm"] == pytest.approx(4.25)
    assert data["qa_chips"]["crossval_r"] == pytest.approx(0.87)
    assert data["qa_chips"]["gnss_rmse_mm"] != 0.92


def test_monitor_corrupt_qa_json_degrades_to_basic(client):
    client.post("/api/sessions", json={"id": "sess-m"})
    _seed_run(client._home)
    qa_path = (client._home / "sessions" / "sess-m" / "products" / "report" / "qa.json")
    qa_path.parent.mkdir(parents=True, exist_ok=True)
    qa_path.write_text("{not-json", encoding="utf-8")

    r = client.get("/api/monitor", params={"session": "sess-m"})
    assert r.status_code == 200
    data = r.json()
    assert data["product"]["level"] == "basic"
    assert data["product"]["simulated"] is False
    assert data["product"]["label"] == "LOS · 相对参考点"
    assert data["qa_chips"] == {"crossval_r": None, "gnss_rmse_mm": None}


def test_pi_journal_roundtrip(client):
    r = client.post("/api/pi-journal", json={
        "session": "sess-j", "tool": "bash", "mode": "free",
        "is_error": False, "input_digest": "git status", "ts": 1755229000.0})
    assert r.status_code == 200 and r.json() == {"accepted": True}

    data = client.get("/api/pi-journal").json()
    assert data["total"] == 1
    entry = data["entries"][0]
    assert entry["tool"] == "bash" and entry["mode"] == "free"
    assert entry["received_at"] > 0


def test_pi_journal_validates_and_tolerates_missing_file(client):
    assert client.get("/api/pi-journal").json() == {"entries": [], "total": 0}
    assert client.post("/api/pi-journal", json={"tool": "bash"}).status_code == 422
