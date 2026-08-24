"""环境向导判定语义回归锁(2026-08-12 用户实测反馈修复)。

1. data_source 是可选项:未配置不得锁死 ready(模拟演示/条带链不需要 HyP3 目录);
2. WSL 引擎兜底:宿主 PATH 探测不到 snaphu 但 WSL 里有 → 检查项按 "(wsl)" 通过
   (isce2/snaphu 作业本就由 backend_select 路由到 WSL 执行)。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from insar_agent.api.setup_router import create_setup_router


def _client(tmp_path, monkeypatch, wsl_result: dict | None = None):
    # 密封:清掉宿主环境变量影响,WSL 探测按注入结果返回
    for env in ("INSAR_ENGINE_PREFIX", "INSAR_HYP3_SOURCE"):
        monkeypatch.delenv(env, raising=False)
    if wsl_result is not None:
        import insar_agent.runtime.wsl_probe as wp
        monkeypatch.setattr(wp, "probe_wsl_engines_cached",
                            lambda **kw: wsl_result)
    app = FastAPI()
    app.include_router(create_setup_router(tmp_path))
    return TestClient(app)


def _by_key(status: dict) -> dict:
    return {c["key"]: c for c in status["checks"]}


def test_data_source_is_optional_and_does_not_block_ready(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch,
                wsl_result={"ok": False, "error": "未安装", "engines": {}})
    status = c.get("/api/setup/status").json()
    checks = _by_key(status)
    ds = checks["data_source"]
    assert ds["required"] is False and ds["ok"] is False
    assert "可选" in ds["message"]
    # ready 只看必需项:数据源缺席不参与(宿主引擎状态因机器而异,这里只锁
    # "ready 的取值 = 必需项全过",与 data_source 无关)
    required_ok = all(x["ok"] for x in status["checks"] if x["required"])
    assert status["ready"] == required_ok


def test_wsl_engine_fallback_marks_snaphu_ok(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, wsl_result={
        "ok": True, "distro": "insar", "error": None,
        "engine_prefix": "/opt/miniforge3/envs/insar",
        "engines": {"snaphu": {"present": True, "version": "2.0.6"},
                    "isce2": {"present": True, "version": "2.6.5"},
                    "mintpy": {"present": True, "version": "1.6.4"}},
    })
    status = c.get("/api/setup/status").json()
    checks = _by_key(status)
    snaphu = checks["engine_snaphu"]
    assert snaphu["ok"] is True
    assert "(wsl)" in snaphu["message"]


def test_configured_but_broken_data_source_still_reports(tmp_path, monkeypatch):
    """配置了坏路径不沉默:仍如实 ok=False(用户显然想用它),但保持可选不锁 ready。"""
    c = _client(tmp_path, monkeypatch,
                wsl_result={"ok": False, "error": "未安装", "engines": {}})
    # 必须在 _client 的环境清扫之后再设置(端点按请求时的 os.environ 读取)
    monkeypatch.setenv("INSAR_HYP3_SOURCE", str(tmp_path / "不存在的目录"))
    status = c.get("/api/setup/status").json()
    ds = _by_key(status)["data_source"]
    assert ds["ok"] is False and ds["required"] is False
    assert "不存在" in ds["message"]
