"""向导引擎发现透明化(/api/setup/status)回归锁(2026-08-13)。

背景:probe_environment 支持隐式 conda prefix 回退后,向导 UI 不知道
"引擎是从哪找到的",用户也无法把隐式发现固化为显式配置。本文件锁定:
1. engine.discovered_prefix 三态与来源标注:explicit(显式配置,坏路径也如实报)
   / implicit(隐式发现,未固化)/ None;
2. engine_prefix 检查的 message 在隐式命中时写「自动发现引擎环境:…(未固化,建议保存)」;
3. 一键保存链路(向导「使用自动发现的环境」按钮 → POST /save 现有端点)后来源转 explicit;
4. GET /status?force=1 透传 probe_wsl_engines_cached(force=…) 穿透 WSL 探测缓存。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import insar_agent.api.setup_router as sr
import insar_agent.runtime.wsl_probe as wp
from insar_agent.api.setup_router import setup_router
from insar_agent.runtime.probe import ProbeResult

_ENVS = ("INSAR_ENGINE_PREFIX", "INSAR_HYP3_SOURCE")


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """密封环境:INSAR_HOME 指向临时目录;引擎/数据源变量从干净状态出发。"""
    h = tmp_path / "home"
    monkeypatch.setenv("INSAR_HOME", str(h))
    for k in _ENVS:
        monkeypatch.setenv(k, "_discovery_test_")
        monkeypatch.delenv(k)
    return h


@pytest.fixture()
def client(home, monkeypatch):
    # WSL 底层探测默认密封为不可达(不付真实 20s 探测);缓存清零保证测试间独立。
    # force 语义测试会按需重打这两个补丁。
    monkeypatch.setattr(wp, "probe_wsl_engines",
                        lambda **kw: {"ok": False, "error": "sealed", "engines": {}})
    monkeypatch.setattr(wp, "_PROBE_CACHE", {})
    app = FastAPI()
    app.include_router(setup_router)
    with TestClient(app) as c:
        yield c


def _fake_probe(engines: dict | None = None, disk: float = 100.0):
    """密封 probe_environment:不受宿主 PATH/conda 环境影响(同 test_setup_router)。"""
    base = {"isce2": None, "mintpy": None, "snaphu": None, "gdal": None,
            "snap": None, "pystamps": None, "pyaps": None}
    base.update(engines or {})

    def probe(*args, **kwargs):
        return ProbeResult(
            engines=dict(base),
            credentials={"earthdata": False, "cds": False, "gacos": False},
            disk_free_gb=disk, disk_total_gb=500.0, cpu_count=8)

    return probe


def _seal(monkeypatch, *, engines: dict | None = None, implicit: str | None = None):
    """密封发现链路:假引擎表 + 隐式发现固定返回值(宿主真装了 insar 环境也不干扰)。"""
    monkeypatch.setattr(sr, "probe_environment", _fake_probe(engines))
    monkeypatch.setattr(sr, "_implicit_engine_prefix", lambda: implicit)


_CONDA_ENGINES = {"mintpy": "present(insar)", "gdal": "present(insar)"}


def _prefix_check(body: dict) -> dict:
    return next(c for c in body["checks"] if c["key"] == "engine_prefix")


# ---------------- discovered_prefix 三态 ----------------

def test_engine_section_contract_shape(client, monkeypatch):
    """engine 段键集合钉死(前端 renderDiscovered/engineValueOf 逐字段读取)。"""
    _seal(monkeypatch)
    eng = client.get("/api/setup/status").json()["engine"]
    assert set(eng) == {"prefix", "prefix_configured", "prefix_exists",
                        "discovered_prefix", "prefix_source", "engines"}
    assert eng["prefix_source"] in (None, "explicit", "implicit")


def test_discovered_prefix_none_when_nothing_found(client, monkeypatch):
    _seal(monkeypatch, implicit=None)
    body = client.get("/api/setup/status").json()
    eng = body["engine"]
    assert eng["discovered_prefix"] is None
    assert eng["prefix_source"] is None
    assert eng["prefix_configured"] is False
    ck = _prefix_check(body)
    assert ck["ok"] is False and "未配置引擎环境" in ck["message"]


def test_discovered_prefix_explicit_when_env_configured(client, monkeypatch, tmp_path):
    env_dir = tmp_path / "conda" / "envs" / "insar"
    env_dir.mkdir(parents=True)
    # 显式配置在场时不得采用隐式扫描结果(probe 的取值顺序:显式 env 优先)
    _seal(monkeypatch, engines=_CONDA_ENGINES, implicit=str(tmp_path / "决不该出现"))
    monkeypatch.setenv("INSAR_ENGINE_PREFIX", str(env_dir))
    body = client.get("/api/setup/status").json()
    eng = body["engine"]
    assert eng["discovered_prefix"] == str(env_dir)
    assert eng["prefix_source"] == "explicit"
    assert eng["prefix_exists"] is True
    ck = _prefix_check(body)
    assert ck["ok"] is True and "自动发现" not in ck["message"]


def test_discovered_prefix_implicit_when_env_missing(client, monkeypatch, tmp_path):
    found = tmp_path / "miniforge3" / "envs" / "insar"
    found.mkdir(parents=True)
    _seal(monkeypatch, engines=_CONDA_ENGINES, implicit=str(found))
    eng = client.get("/api/setup/status").json()["engine"]
    assert eng["discovered_prefix"] == str(found)
    assert eng["prefix_source"] == "implicit"
    # 隐式发现 ≠ 已配置:prefix 仍为空,提示用户固化
    assert eng["prefix"] is None and eng["prefix_configured"] is False


def test_explicit_broken_prefix_keeps_explicit_source(client, monkeypatch, tmp_path):
    """显式配置坏路径:probe 只在未配置时才隐式回退,来源必须如实标 explicit
    (标成 implicit 会诱导用户点「使用自动发现的环境」把坏路径再存一遍)。"""
    _seal(monkeypatch, implicit=str(tmp_path))
    monkeypatch.setenv("INSAR_ENGINE_PREFIX", r"E:\no\such\env")
    body = client.get("/api/setup/status").json()
    eng = body["engine"]
    assert eng["prefix_source"] == "explicit"
    assert eng["discovered_prefix"] == r"E:\no\such\env"
    assert eng["prefix_exists"] is False
    assert _prefix_check(body)["ok"] is False


# ---------------- 隐式命中的 message 关键词 ----------------

def test_implicit_hit_message_keywords(client, monkeypatch, tmp_path):
    found = tmp_path / "envs" / "insar"
    found.mkdir(parents=True)
    _seal(monkeypatch, engines=_CONDA_ENGINES, implicit=str(found))
    ck = _prefix_check(client.get("/api/setup/status").json())
    assert ck["ok"] is True
    assert "自动发现引擎环境" in ck["message"]
    assert str(found) in ck["message"]
    assert "未固化" in ck["message"] and "建议保存" in ck["message"]


# ---------------- 一键保存后来源转 explicit ----------------

def test_one_click_save_turns_explicit(client, monkeypatch, tmp_path):
    """向导「使用自动发现的环境」按钮的后端链路:POST /save(现有端点)→ 固化。"""
    found = tmp_path / "miniforge3" / "envs" / "insar"
    found.mkdir(parents=True)
    _seal(monkeypatch, engines=_CONDA_ENGINES, implicit=str(found))
    before = client.get("/api/setup/status").json()["engine"]
    assert before["prefix_source"] == "implicit"

    r = client.post("/api/setup/save",
                    json={"engine_prefix": before["discovered_prefix"]})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["exists"]["engine_prefix"] is True

    after = client.get("/api/setup/status").json()
    eng = after["engine"]
    assert eng["prefix_source"] == "explicit"
    assert eng["prefix"] == str(found)
    assert eng["prefix_configured"] is True and eng["prefix_exists"] is True
    ck = _prefix_check(after)
    assert ck["ok"] is True and "未固化" not in ck["message"]
    # 落盘核对:settings.json 已固化,重启进程也能恢复
    settings = json.loads(Path(after["settings_file"]).read_text(encoding="utf-8"))
    assert settings["engine_prefix"] == str(found)


# ---------------- force 穿透 WSL 探测缓存 ----------------

def test_status_passes_force_through_to_wsl_probe(client, monkeypatch):
    """接线契约:?force=1 → probe_wsl_engines_cached(force=True),不带/为 0 → False。"""
    _seal(monkeypatch)
    seen: list[bool] = []

    def fake_cached(**kw):
        seen.append(bool(kw.get("force")))
        return {"ok": False, "error": "sealed", "engines": {}}

    monkeypatch.setattr(wp, "probe_wsl_engines_cached", fake_cached)
    client.get("/api/setup/status")
    client.get("/api/setup/status?force=1")
    client.get("/api/setup/status?force=0")
    assert seen == [False, True, False]


def test_force_penetrates_wsl_probe_cache(client, monkeypatch):
    """mock 计数走真缓存包装器:命中缓存不重探,force=1 强制重探且新结果回填缓存。"""
    _seal(monkeypatch)
    calls = {"n": 0}

    def fake_probe_wsl(**kw):
        calls["n"] += 1
        return {"ok": True, "distro": "insar", "error": None,
                "engine_prefix": "/opt/miniforge3/envs/insar",
                "engines": {"snaphu": {"present": True, "version": "2.0.6"}}}

    monkeypatch.setattr(wp, "probe_wsl_engines", fake_probe_wsl)
    client.get("/api/setup/status")          # 冷缓存:底层真探测 1 次
    client.get("/api/setup/status")          # TTL 内命中缓存:不再探测
    assert calls["n"] == 1
    client.get("/api/setup/status?force=1")  # force:穿透缓存重探
    assert calls["n"] == 2
    client.get("/api/setup/status")          # force 的新结果已回填缓存
    assert calls["n"] == 2
