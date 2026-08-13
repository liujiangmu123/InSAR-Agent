"""环境面板前后端契约测试（防契约漂移）。

prototype/js/envlive.js 是「环境」面板的实时数据模块，消费两个端点：
  - GET /api/env          → probe.{engines,credentials,wsl.engine_probe,
                            disk_free_gb,disk_total_gb,cpu_count,mem_gb,
                            python,platform} + thresholds[{key,value,source,ref,status}]
  - GET /api/setup/status → ready + checks[{key,ok,message,fix_hint,required}]
                            + agent/engine/data/disk 摘要

本文件把 envlive.js 实际读取的字段清单写成断言：后端改动若删改任一字段，
这里先红，而不是等到面板在浏览器里静默显示成空白。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from insar_agent.api.app import create_app
from insar_agent.runtime.probe import ProbeResult

# ---------------- envlive.js 消费的字段清单（与前端一一对应） ----------------

# envlive.normalize() 读取 probe.* 的键
ENV_PROBE_FIELDS = {"engines", "credentials", "wsl", "disk_free_gb", "disk_total_gb",
                    "cpu_count", "mem_gb", "python", "platform"}
# envlive.normalize() 读取 thresholds[i].* 的键（thresholdSourceLabel 用 source/status）
THRESHOLD_FIELDS = {"key", "value", "source", "ref", "status"}
# envlive.normalize() 读取 probe.wsl.engine_probe.* 的键（WSL 发行版状态行）
ENGINE_PROBE_FIELDS = {"ok", "distro", "error", "engine_prefix"}
# 凭据状态行的固定键（runtime/probe.py 的 _CREDENTIALS）
CREDENTIAL_KEYS = {"earthdata", "cds", "gacos"}

# envlive.normalize() 读取 /api/setup/status 的顶层键
SETUP_TOP_FIELDS = {"ready", "agent", "engine", "data", "disk", "checks"}
# 就绪检查行读取的键
SETUP_CHECK_FIELDS = {"key", "ok", "message", "fix_hint", "required"}
# 摘要子对象里 envlive 读取的键
SETUP_AGENT_FIELDS = {"python", "venv", "executable"}
SETUP_ENGINE_FIELDS = {"prefix", "prefix_configured", "prefix_exists", "engines"}
SETUP_DATA_FIELDS = {"source", "configured", "exists", "pair_count"}
SETUP_DISK_FIELDS = {"free_gb", "total_gb"}
# setup_router.status() 的固定检查项（envView 逐行渲染）
SETUP_CHECK_KEYS = {"agent_python", "engine_prefix", "engine_mintpy", "engine_gdal",
                    "engine_snaphu", "engine_pyaps", "data_source", "disk_space"}


# ---------------- 密封 fixture：不受宿主 PATH / conda / WSL 影响 ----------------

def _empty_probe(*args, **kwargs):
    return ProbeResult(
        engines={k: None for k in ("isce2", "mintpy", "snaphu", "gdal", "snap",
                                   "pystamps", "pyaps")},
        credentials={"earthdata": False, "cds": False, "gacos": False},
        disk_free_gb=100.0, disk_total_gb=500.0, cpu_count=8)


def _wsl_reachable(*args, **kwargs):
    """伪造 WSL 可达：与 wsl_probe.probe_wsl_engines 的返回契约一致。"""
    return {
        "ok": True, "distro": "insar", "error": None,
        "engine_prefix": "/opt/miniforge3/envs/insar",
        "engines": {
            "isce2": {"present": True, "path": "/opt/isce2/bin/topsApp.py",
                      "version": "2.6.5", "error": None},
            "mintpy": {"present": True, "path": "/opt/env/bin/smallbaselineApp.py",
                       "version": "1.6.4", "error": None},
            "snaphu": {"present": False, "path": None, "version": None, "error": None},
        },
    }


def _wsl_unreachable(*args, **kwargs):
    return {
        "ok": False, "distro": "insar", "error": "wsl.exe 不存在（未安装 WSL）",
        "engine_prefix": None,
        "engines": {name: {"present": False, "path": None, "version": None, "error": None}
                    for name in ("isce2", "mintpy", "snaphu")},
    }


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # 本机探测密封为空引擎（同 test_api.py 做法）；setup 向导端点同样密封
    monkeypatch.setattr("insar_agent.loop.driver.probe_environment", _empty_probe)
    monkeypatch.setattr("insar_agent.api.setup_router.probe_environment", _empty_probe)
    for env in ("INSAR_ENGINE_PREFIX", "INSAR_HYP3_SOURCE"):
        monkeypatch.delenv(env, raising=False)
    app = create_app(home=tmp_path / "home")
    with TestClient(app) as c:
        yield c


# ---------------- GET /api/env ----------------

def test_env_contract_probe_fields(client, monkeypatch):
    """probe 的字段清单与类型：envlive.normalize() 读取的每个键都必须在。"""
    monkeypatch.setattr("insar_agent.runtime.wsl_probe.probe_wsl_engines",
                        _wsl_unreachable)
    body = client.get("/api/env", params={"session": "demo"}).json()

    assert set(body) >= {"probe", "thresholds"}
    probe = body["probe"]
    assert set(probe) >= ENV_PROBE_FIELDS

    # 引擎清单：dict[str, str|None]（None=缺失；字符串=版本号或 "present"）
    assert isinstance(probe["engines"], dict) and probe["engines"]
    assert all(v is None or isinstance(v, str) for v in probe["engines"].values())

    # 凭据状态：固定三键，布尔值
    assert set(probe["credentials"]) >= CREDENTIAL_KEYS
    assert all(isinstance(v, bool) for v in probe["credentials"].values())

    # 磁盘 / CPU / 内存 / 宿主信息
    assert isinstance(probe["disk_free_gb"], (int, float))
    assert isinstance(probe["disk_total_gb"], (int, float))
    assert isinstance(probe["cpu_count"], int)
    assert probe["mem_gb"] is None or isinstance(probe["mem_gb"], (int, float))
    assert isinstance(probe["python"], str) and isinstance(probe["platform"], str)

    # 阈值台账：每行五键；status/source 是 envlive.thresholdSourceLabel 的分支依据
    assert body["thresholds"], "阈值台账不应为空"
    for t in body["thresholds"]:
        assert set(t) >= THRESHOLD_FIELDS
        assert t["status"].upper() in ("OK", "PENDING")
        assert t["source"] in ("upstream_default", "literature", "local_calibration")


def test_env_contract_wsl_reachable_merges_suffixed_engines(client, monkeypatch):
    """WSL 可达：引擎并入且键带 " (wsl)" 后缀（宿主徽标的判据）；
    engine_probe 挂到 probe.wsl 下（WSL 发行版状态行的数据源）。"""
    monkeypatch.setattr("insar_agent.runtime.wsl_probe.probe_wsl_engines",
                        _wsl_reachable)
    probe = client.get("/api/env", params={"session": "demo"}).json()["probe"]

    # 合并规则：present 且有版本 → 版本串；present 无版本 → "present"；缺失 → None
    assert probe["engines"]["isce2 (wsl)"] == "2.6.5"
    assert probe["engines"]["mintpy (wsl)"] == "1.6.4"
    assert probe["engines"]["snaphu (wsl)"] is None

    ep = probe["wsl"]["engine_probe"]
    assert set(ep) >= ENGINE_PROBE_FIELDS
    assert ep["ok"] is True
    assert ep["distro"] == "insar"
    assert ep["error"] is None
    assert ep["engine_prefix"] == "/opt/miniforge3/envs/insar"


def test_env_contract_wsl_unreachable_keeps_engines_clean(client, monkeypatch):
    """WSL 不可达：不得添加任何 " (wsl)" 引擎键。

    现状锁定：api/app.py 仅在探测 ok 时调用 merge_wsl_probe，因此不可达时
    probe.wsl 里没有 engine_probe（不可达原因不透出，envlive.js 按
    probed=false 渲染「不可达或未安装」）。这是已知后端缺口（见任务报告）；
    后端若改为不可达也挂诊断元数据，本断言应同步收紧为校验 error 字段。"""
    monkeypatch.setattr("insar_agent.runtime.wsl_probe.probe_wsl_engines",
                        _wsl_unreachable)
    probe = client.get("/api/env", params={"session": "demo"}).json()["probe"]

    assert not [k for k in probe["engines"] if k.endswith("(wsl)")]
    assert "engine_probe" not in probe["wsl"]  # 现状：不可达不挂诊断元数据


# ---------------- GET /api/setup/status ----------------

def test_setup_status_contract(client, monkeypatch):
    """就绪检查的字段清单：envView「就绪检查」卡逐行渲染 checks，
    总评 tag 读 ready；摘要子对象是面板扩展信息的数据源。"""
    monkeypatch.setattr("insar_agent.runtime.wsl_probe.probe_wsl_engines",
                        _wsl_unreachable)
    body = client.get("/api/setup/status").json()

    assert set(body) >= SETUP_TOP_FIELDS
    assert isinstance(body["ready"], bool)
    assert body["ready"] is False  # 密封环境：引擎/数据源全缺 → 必选项不通过

    assert set(body["agent"]) >= SETUP_AGENT_FIELDS
    assert set(body["engine"]) >= SETUP_ENGINE_FIELDS
    assert set(body["data"]) >= SETUP_DATA_FIELDS
    assert set(body["disk"]) >= SETUP_DISK_FIELDS
    assert isinstance(body["data"]["pair_count"], int)
    assert isinstance(body["disk"]["free_gb"], (int, float))

    checks = body["checks"]
    assert {c["key"] for c in checks} >= SETUP_CHECK_KEYS
    for c in checks:
        assert set(c) >= SETUP_CHECK_FIELDS
        assert isinstance(c["ok"], bool) and isinstance(c["required"], bool)
        assert isinstance(c["message"], str) and c["message"]
        assert isinstance(c["fix_hint"], str)
        if not c["ok"]:
            assert c["fix_hint"], f"未通过项 {c['key']} 必须给修复建议（面板「处置」行）"
