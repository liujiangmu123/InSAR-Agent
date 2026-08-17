"""GET /api/monitor 的 session=@latest / 省略语义。

沿用 tests/test_bridge_router.py 的 client 形态:密封 probe + INSAR_ALLOW_SIMULATED,
TestClient 走临时 INSAR_HOME,不打生产 8873。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from insar_agent.api.app import create_app
from insar_agent.core.db import Database
from insar_agent.core.store import Store

_HASHES = {"task_hash": "t0", "args_hash": "a0",
           "local_hash": "l0", "eval_hash": "e0" * 6}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # 与 test_bridge_router 同款密封:本文件不得触发宿主 MintPy
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


def _seed_run(home, session: str, rid: str, *, created_at: float, n_steps: int = 2):
    """显式 created_at:多会话排序不能依赖 create_run 的挂钟间隔。"""
    store = Store(Database(home / "insar.db"))
    ws = home / "sessions" / session
    store.create_run(rid, session, workspace=str(ws), scenario="quake")
    with store.db.tx() as cur:
        cur.execute("UPDATE runs SET created_at=? WHERE run_id=?", (created_at, rid))
    for sid in range(1, n_steps + 1):
        store.upsert_step(rid, sid, capability=f"cap{sid}", name=f"步骤{sid}",
                          method="m_x", params={}, hashes=_HASHES)
        store.mark_step(rid, sid, state="done")
    return rid


def _empty_shape(data: dict) -> None:
    assert data["run"] is None
    assert data["steps"] == []
    assert data["current"] is None
    assert data["progress"] == {"total": 0, "done": 0, "pct": 0}
    assert data["taints"] == 0
    assert "mode" in data


def test_monitor_latest_no_sessions_empty_shell(client):
    for params in ({}, {"session": "@latest"}):
        r = client.get("/api/monitor", params=params)
        assert r.status_code == 200
        data = r.json()
        assert data["session"] == "@latest"
        _empty_shape(data)
        assert data["mode"] == "free"


def test_monitor_latest_idle_session_keeps_mode(client):
    client.post("/api/sessions", json={"id": "lonely"})
    client.post("/api/mode", json={"session": "lonely", "mode": "strict"})
    data = client.get("/api/monitor", params={"session": "@latest"}).json()
    assert data["session"] == "lonely"
    _empty_shape(data)
    assert data["mode"] == "strict"


def test_monitor_latest_picks_newest_run_session(client):
    client.post("/api/sessions", json={"id": "real"})
    client.post("/api/sessions", json={"id": "other"})
    _seed_run(client._home, "real", "run-real", created_at=1000.0)
    _seed_run(client._home, "other", "run-other", created_at=2000.0)

    latest = client.get("/api/monitor", params={"session": "@latest"}).json()
    omitted = client.get("/api/monitor").json()
    assert latest["session"] == "other"
    assert latest["run"]["run_id"] == "run-other"
    assert len(latest["steps"]) == 2
    assert omitted == latest


def test_monitor_explicit_session_not_overridden(client):
    """显式 session=real 不得被更新的 other 会话抢走。"""
    client.post("/api/sessions", json={"id": "real"})
    client.post("/api/sessions", json={"id": "other"})
    _seed_run(client._home, "real", "run-real", created_at=1000.0)
    _seed_run(client._home, "other", "run-other", created_at=2000.0)

    data = client.get("/api/monitor", params={"session": "real"}).json()
    assert data["session"] == "real"
    assert data["run"]["run_id"] == "run-real"


def test_monitor_latest_prefers_run_activity_over_newer_idle_session(client):
    client.post("/api/sessions", json={"id": "with-run"})
    _seed_run(client._home, "with-run", "run-old", created_at=1000.0)
    client.post("/api/sessions", json={"id": "idle-newer"})
    data = client.get("/api/monitor", params={"session": "@latest"}).json()
    assert data["session"] == "with-run"
    assert data["run"]["run_id"] == "run-old"


def test_monitor_latest_run_id_cross_session_404(client):
    client.post("/api/sessions", json={"id": "real"})
    client.post("/api/sessions", json={"id": "other"})
    _seed_run(client._home, "real", "run-real", created_at=1000.0)
    _seed_run(client._home, "other", "run-other", created_at=2000.0)

    # @latest 解析到 other 后仍走归属校验,不把 real 的 run 透出去
    r = client.get("/api/monitor", params={"session": "@latest", "run_id": "run-real"})
    assert r.status_code == 404
    r2 = client.get("/api/monitor", params={"session": "real", "run_id": "run-other"})
    assert r2.status_code == 404


def test_monitor_latest_skips_archived_session(client):
    client.post("/api/sessions", json={"id": "old-active"})
    client.post("/api/sessions", json={"id": "new-archived"})
    _seed_run(client._home, "old-active", "run-old", created_at=1000.0)
    _seed_run(client._home, "new-archived", "run-new", created_at=2000.0)
    ar = client.patch("/api/sessions/new-archived", json={"archived": True})
    assert ar.status_code == 200

    data = client.get("/api/monitor", params={"session": "@latest"}).json()
    assert data["session"] == "old-active"
    assert data["run"]["run_id"] == "run-old"
    # 显式点名归档会话仍可读(软删不丢 run),只是 @latest 不选它
    named = client.get("/api/monitor", params={"session": "new-archived"}).json()
    assert named["run"]["run_id"] == "run-new"
