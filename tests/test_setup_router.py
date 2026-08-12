"""环境向导后端(/api/setup)契约测试。

独立 FastAPI 实例挂 setup_router,不经 create_app(app.py 由另一分支集成)。
覆盖:status 结构与 ready 判定、save 原子落盘 + 热更新 env + 重载、engine-env 命令清单。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from insar_agent.api.setup_router import setup_router
from insar_agent.runtime.probe import ProbeResult

_ENVS = ("INSAR_ENGINE_PREFIX", "INSAR_HYP3_SOURCE")


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """密封环境:INSAR_HOME 指向临时目录;引擎/数据源变量从干净状态出发。

    setenv+delenv 双登记:endpoint 直接改 os.environ 也能在 teardown 恢复宿主原值。
    """
    h = tmp_path / "home"
    monkeypatch.setenv("INSAR_HOME", str(h))
    for k in _ENVS:
        monkeypatch.setenv(k, "_wizard_test_")
        monkeypatch.delenv(k)
    return h


@pytest.fixture()
def client(home):
    app = FastAPI()
    app.include_router(setup_router)
    with TestClient(app) as c:
        yield c


def _fake_probe(engines: dict | None = None, disk: float = 100.0):
    """密封 probe:不受宿主 PATH/conda 环境影响(同 test_api.py 的做法)。"""
    base = {"isce2": None, "mintpy": None, "snaphu": None, "gdal": None,
            "snap": None, "pystamps": None, "pyaps": None}
    base.update(engines or {})

    def probe(*args, **kwargs):
        return ProbeResult(
            engines=dict(base),
            credentials={"earthdata": False, "cds": False, "gacos": False},
            disk_free_gb=disk, disk_total_gb=500.0, cpu_count=8)

    return probe


# ---------------- GET /api/setup/status ----------------

def test_status_shape_and_not_ready_when_empty(client, monkeypatch):
    monkeypatch.setattr("insar_agent.api.setup_router.probe_environment", _fake_probe())
    r = client.get("/api/setup/status")
    assert r.status_code == 200
    body = r.json()

    assert set(body) >= {"ready", "agent", "engine", "data", "disk",
                         "checks", "settings_file", "settings"}
    assert body["ready"] is False
    assert isinstance(body["agent"]["python"], str)
    assert isinstance(body["agent"]["venv"], bool)
    assert set(body["engine"]["engines"]) == {"mintpy", "gdal", "snaphu", "pyaps"}
    assert body["engine"]["prefix_configured"] is False
    assert body["data"]["configured"] is False and body["data"]["pair_count"] == 0
    assert body["disk"]["free_gb"] == 100.0

    keys = {c["key"] for c in body["checks"]}
    assert keys >= {"agent_python", "engine_prefix", "engine_mintpy", "engine_gdal",
                    "engine_snaphu", "engine_pyaps", "data_source", "disk_space"}
    for c in body["checks"]:
        assert set(c) >= {"key", "ok", "message", "fix_hint"}
        assert isinstance(c["ok"], bool) and c["message"]
        if not c["ok"]:
            assert c["fix_hint"], f"缺失项必须给中文修复建议: {c['key']}"
    bad = {c["key"] for c in body["checks"] if not c["ok"]}
    assert {"engine_prefix", "engine_mintpy", "engine_gdal", "data_source"} <= bad


def test_status_ready_when_all_present(client, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "insar_agent.api.setup_router.probe_environment",
        _fake_probe({"mintpy": "present(insar)", "gdal": "present(insar)"}))
    env_dir = tmp_path / "conda" / "envs" / "insar"
    env_dir.mkdir(parents=True)
    src = tmp_path / "RidgecrestSenDT71"
    for i in range(3):
        pair = src / f"S1_pair_{i}"
        pair.mkdir(parents=True)
        (pair / f"p{i}_unw_phase_clipped.tif").write_bytes(b"\x00")
    monkeypatch.setenv("INSAR_ENGINE_PREFIX", str(env_dir))
    monkeypatch.setenv("INSAR_HYP3_SOURCE", str(src))

    body = client.get("/api/setup/status").json()
    assert body["engine"]["prefix_exists"] is True
    assert body["data"]["exists"] is True and body["data"]["pair_count"] == 3
    assert body["ready"] is True
    # 可选项(snaphu/pyaps)缺失不拦 ready
    by_key = {c["key"]: c for c in body["checks"]}
    assert by_key["engine_snaphu"]["ok"] is False
    assert by_key["engine_snaphu"]["required"] is False
    assert by_key["engine_pyaps"]["required"] is False


# ---------------- POST /api/setup/save ----------------

def test_save_writes_settings_atomically_and_updates_env(client, home, tmp_path):
    env_dir = tmp_path / "envs" / "insar"
    env_dir.mkdir(parents=True)
    missing = tmp_path / "no-such-dir"

    r = client.post("/api/setup/save", json={
        "engine_prefix": str(env_dir), "hyp3_source": str(missing)})
    assert r.status_code == 200
    body = r.json()

    sf = Path(body["settings_file"])
    assert sf == home / "settings.json" and sf.exists()
    data = json.loads(sf.read_text(encoding="utf-8"))
    assert data == {"engine_prefix": str(env_dir), "hyp3_source": str(missing)}
    # 原子写:INSAR_HOME 下只有成品,不残留 *.tmp
    assert [p.name for p in home.iterdir()] == ["settings.json"]
    # 热更新进程环境
    assert os.environ["INSAR_ENGINE_PREFIX"] == str(env_dir)
    assert os.environ["INSAR_HYP3_SOURCE"] == str(missing)
    # exists 反馈:目录存在与否如实上报
    assert body["exists"] == {"engine_prefix": True, "hyp3_source": False}


def test_save_merges_partial_and_clears_with_empty(client, home):
    client.post("/api/setup/save", json={"engine_prefix": "E:/x", "hyp3_source": "E:/y"})
    # 只传一个字段:另一个保留
    client.post("/api/setup/save", json={"hyp3_source": "E:/z"})
    data = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    assert data == {"engine_prefix": "E:/x", "hyp3_source": "E:/z"}
    assert os.environ["INSAR_HYP3_SOURCE"] == "E:/z"
    # 空串 = 清除该项(settings 与进程 env 同步移除)
    client.post("/api/setup/save", json={"engine_prefix": ""})
    data = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    assert data == {"hyp3_source": "E:/z"}
    assert "INSAR_ENGINE_PREFIX" not in os.environ
    # 什么都不传 → 400
    assert client.post("/api/setup/save", json={}).status_code == 400


def test_status_loads_settings_from_disk(client, monkeypatch):
    """模拟重启:save 落盘后清掉进程 env,GET status 应从 settings.json 恢复。"""
    monkeypatch.setattr("insar_agent.api.setup_router.probe_environment", _fake_probe())
    client.post("/api/setup/save", json={"engine_prefix": "E:/no/such/env"})
    os.environ.pop("INSAR_ENGINE_PREFIX", None)  # 假装换了个进程

    body = client.get("/api/setup/status").json()
    assert body["engine"]["prefix_configured"] is True
    assert body["engine"]["prefix"] == "E:/no/such/env"
    assert body["engine"]["prefix_exists"] is False
    assert body["settings"]["engine_prefix"] == "E:/no/such/env"


# ---------------- POST /api/setup/engine-env ----------------

def test_engine_env_returns_command_list_without_executing(client):
    r = client.post("/api/setup/engine-env")
    assert r.status_code == 200
    body = r.json()
    assert "detected_conda" in body  # 路径字符串或 null,都合法

    cmds = body["commands"]
    assert isinstance(cmds, list) and cmds
    for c in cmds:
        assert set(c) == {"title", "command", "note"}
        assert c["title"] and c["command"]

    joined = " ".join(c["command"] for c in cmds)
    assert "Miniforge3-Windows-x86_64.exe" in joined        # miniforge 下载链接
    assert "python=3.11" in joined and "mintpy" in joined   # conda create
    assert "mirrors.tuna.tsinghua.edu.cn" in joined         # 清华镜像
    assert "openblas" in joined                             # BLAS 切 OpenBLAS(README 坑位)
