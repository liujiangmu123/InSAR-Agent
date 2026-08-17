"""界面诚实化: /api/runs 每条 run 附 simulated 布尔。

演示执行的产物绝不冒充真实运行。密封:probe 打空桩(引擎全缺 → run 必为
simulated),INSAR_HOME 指向 pytest tmp_path。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from insar_agent.api.app import create_app


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
    app = create_app(home=tmp_path / "home")
    with TestClient(app) as c:
        yield c


def _drain(client: TestClient, url: str, body: dict) -> None:
    """POST NDJSON 流式端点并消费到尾(test_api.py 的 _stream_events 同款)。"""
    with client.stream("POST", url, json=body) as resp:
        assert resp.status_code == 200
        for _ in resp.iter_lines():
            pass


# ---------------- /api/runs 的 simulated 标记 ----------------

def test_runs_carry_simulated_flag(client):
    """每条 run 附 simulated 布尔;空引擎密封环境下必为 True。"""
    client.post("/api/sessions", json={"id": "demo"})
    _drain(client, "/api/turn", {"session": "demo", "text": "Ridgecrest 地震同震"})
    _drain(client, "/api/pipeline", {"session": "demo"})

    data = client.get("/api/runs", params={"session": "demo"}).json()
    assert data["runs"], "执行过流水线后应至少有一条 run"
    for item in data["runs"]:
        assert isinstance(item["simulated"], bool)
        assert item["simulated"] is True  # 引擎全缺 → 只能是合成执行
        # 既有窄集字段不回归(前端切换器消费面)
        assert {"run_id", "parent_run_id", "created_at", "status", "steps"} <= set(item)

    # 分页/过滤分支(list_runs_page)走同一条目构造,同样带标记
    paged = client.get("/api/runs", params={"session": "demo", "limit": 1}).json()
    assert paged["runs"] and paged["runs"][0]["simulated"] is True


def test_runs_empty_session_unchanged(client):
    """无 run 会话仍是空清单(不 404),simulated 字段不影响空态口径。"""
    data = client.get("/api/runs", params={"session": "nobody"}).json()
    assert data == {"session": "nobody", "runs": []}
