"""环境检测决策树全矩阵(向导 /api/setup/status + runtime/probe 的判定语义钉死)。

监控矩阵(全部 monkeypatch 密封、tmp_path 造假环境目录,零真实探测):
  维度 A 引擎来源:PATH 有 / 显式 prefix / 隐式 prefix(假 conda 根)/ 全无;
  维度 B WSL:可达含 snaphu / 可达无 snaphu / 不可达 / 探测抛异常;
  维度 C 数据源:未配 / 配了好 / 配了坏路径 / 配了空目录。
断言每格的 ready、每个 check 的 ok/required、message 关键词("(wsl)"、
"present(insar)"、"可选" 等)。

回归锁(两轮用户实测反馈,各一条显式测试):
  一报 2026-08-12(749fa3e):WSL 里有 snaphu → engine_snaphu 按 "(wsl)" 通过;
  二报 2026-08-13(c834f93):启动 shell 无 conda PATH、引擎装在已知安装位
  → 隐式回退,ready=true。

另核对:隐式 prefix 优先级(显式 env 变量压过隐式;显式坏路径如实报错、不静默
兜底;名为 insar 的环境优先;扫描根顺序即优先级)与 WSL 探测 TTL 缓存
(_PROBE_CACHE:命中 / 过期 / 失败不缓存 / force / 按发行版隔离)。

真实 WSL 只有一条 skip 保护的秒级冒烟(单条 echo,不逐引擎探测)。

本轮矩阵核对发现并修复的决策树小缺陷(改动在 api/setup_router.py):
  1. data_source 配好时 required 翻回 True:可选是检查项属性,不随状态翻转;
  2. engine_prefix 通过消息断言引擎"来自 PATH":隐式 conda 回退后来源可能是
     已知安装位扫描或 WSL,措辞改为如实列举来源。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import insar_agent.runtime.probe as probe_mod
import insar_agent.runtime.wsl_probe as wp
from insar_agent.api.setup_router import create_setup_router

#: 模块导入时截留原函数:autouse 密封会替换 wp.probe_wsl_engines_cached 模块属性,
#: 缓存单测经此引用绕过替换、直接测真实现(其内部按调用时全局取 probe_wsl_engines)
_REAL_CACHED = wp.probe_wsl_engines_cached

ALL_KEYS = {"agent_python", "engine_prefix", "engine_mintpy", "engine_gdal",
            "engine_snaphu", "engine_pyaps", "data_source", "disk_space"}
REQUIRED_KEYS = {"agent_python", "engine_prefix", "engine_mintpy", "engine_gdal",
                 "disk_space"}
OPTIONAL_KEYS = ALL_KEYS - REQUIRED_KEYS

# probe.py _ENGINE_EXES 的引擎 → PATH 可执行名(密封 which 用)
_EXE_OF = {"isce2": "topsApp.py", "mintpy": "smallbaselineApp.py",
           "snaphu": "snaphu", "gdal": "gdalinfo", "snap": "gpt"}
# probe.py _ENGINE_MODULES 的 python 模块名 → 引擎名(密封 find_spec 用)
_ENGINE_OF_MODULE = {"pystamps": "pystamps", "pyaps3": "pyaps"}


# ---------------- 造假:conda 环境目录 / HyP3 数据源 / WSL 探测结果 ----------------

def _make_conda_env(root: Path, name: str,
                    engines=("mintpy", "gdal", "snaphu", "pyaps")) -> Path:
    """按 probe.py 显式 prefix 的判据造假环境:site-packages 模块目录 / Library\\bin。"""
    env = root / name
    win = sys.platform == "win32"
    site = env / ("Lib/site-packages" if win else "lib/python3.11/site-packages")
    lib_bin = env / ("Library/bin" if win else "bin")
    env.mkdir(parents=True, exist_ok=True)
    for eng in engines:
        if eng in ("mintpy", "pyaps", "pystamps"):
            (site / ("pyaps3" if eng == "pyaps" else eng)).mkdir(parents=True,
                                                                 exist_ok=True)
        else:  # gdal / snaphu:Library\bin 下的可执行
            lib_bin.mkdir(parents=True, exist_ok=True)
            (lib_bin / f"{'gdalinfo' if eng == 'gdal' else eng}.exe").write_bytes(b"")
    return env


def _make_hyp3_source(base: Path, pairs: int, nested: bool = False) -> Path:
    """HyP3 产品目录:每个干涉对一个子目录,内含 *unw_phase_clipped.tif。"""
    root = base / "hyp3" if nested else base
    root.mkdir(parents=True, exist_ok=True)
    for i in range(pairs):
        pair = root / f"S1AA_pair_{i}"
        pair.mkdir()
        (pair / f"p{i}_unw_phase_clipped.tif").write_bytes(b"\x00")
    return base


def _wsl_ok(snaphu: bool = True) -> dict:
    """可达的 WSL 探测结果(契约与 wsl_probe.probe_wsl_engines 返回一致)。"""
    def eng(present: bool, version: str) -> dict:
        return {"present": present, "path": "/opt/x" if present else None,
                "version": version if present else None, "error": None}
    return {"ok": True, "distro": "insar", "error": None,
            "engine_prefix": "/opt/miniforge3/envs/insar",
            "engines": {"isce2": eng(True, "2.6.3"), "mintpy": eng(True, "1.6.4"),
                        "snaphu": eng(snaphu, "2.0.6")}}


def _wsl_down() -> dict:
    return {"ok": False, "distro": "insar", "error": "wsl.exe 不存在(未安装 WSL)",
            "engine_prefix": None,
            "engines": {n: {"present": False, "path": None, "version": None,
                            "error": None} for n in ("isce2", "mintpy", "snaphu")}}


# ---------------- 密封与打洞 ----------------

def _set_path_engines(monkeypatch, engines: tuple[str, ...]) -> None:
    """密封 probe.py 的宿主探测面:which(PATH)/find_spec(模块)/disk_usage 全部注入。"""
    exes = {_EXE_OF[e] for e in engines if e in _EXE_OF}
    fake_shutil = types.SimpleNamespace(
        which=lambda exe: rf"C:\fake\bin\{exe}" if exe in exes else None,
        disk_usage=lambda p: types.SimpleNamespace(
            total=500 << 30, used=400 << 30, free=100 << 30))
    monkeypatch.setattr(probe_mod, "shutil", fake_shutil)
    fake_importlib = types.SimpleNamespace(util=types.SimpleNamespace(
        find_spec=lambda m: object() if _ENGINE_OF_MODULE.get(m) in engines else None))
    monkeypatch.setattr(probe_mod, "importlib", fake_importlib)


def _set_wsl(monkeypatch, result) -> None:
    """注入向导消费的 WSL 探测:dict 直接返回;"raise" 模拟探测本体抛异常。"""
    if result == "raise":
        def _boom(**kw):
            raise RuntimeError("WSL 探测本体炸了(模拟)")
        monkeypatch.setattr(wp, "probe_wsl_engines_cached", _boom)
    else:
        monkeypatch.setattr(wp, "probe_wsl_engines_cached", lambda **kw: result)


@pytest.fixture(autouse=True)
def sealed(monkeypatch, tmp_path):
    """默认密封基线:PATH 无引擎、无可导入模块、隐式扫描根不存在、env 变量清空、
    WSL 不可达。每个测试在此基线上按矩阵格显式开洞,宿主机器状态零泄漏。"""
    _set_path_engines(monkeypatch, ())
    monkeypatch.setattr(probe_mod, "_KNOWN_ENV_ROOTS", (tmp_path / "no-conda-root",))
    for env in ("INSAR_ENGINE_PREFIX", "INSAR_HYP3_SOURCE", "INSAR_HOME"):
        monkeypatch.delenv(env, raising=False)
    _set_wsl(monkeypatch, _wsl_down())


def _status(tmp_path) -> dict:
    app = FastAPI()
    app.include_router(create_setup_router(tmp_path / "home"))
    r = TestClient(app).get("/api/setup/status")
    assert r.status_code == 200
    return r.json()


def _by_key(body: dict) -> dict:
    return {c["key"]: c for c in body["checks"]}


# ================ 维度 A:引擎来源(PATH / 显式 / 隐式 / 全无) ================

def test_a_path_engines_make_ready(tmp_path, monkeypatch):
    _set_path_engines(monkeypatch, ("mintpy", "gdal"))
    body = _status(tmp_path)
    checks = _by_key(body)
    assert body["ready"] is True
    assert checks["engine_mintpy"]["ok"] is True
    assert "MintPy:present" in checks["engine_mintpy"]["message"]
    assert checks["engine_gdal"]["ok"] is True
    # 未配置 prefix 但引擎可用:engine_prefix 以说明性消息通过
    assert checks["engine_prefix"]["ok"] is True
    assert "未配置 INSAR_ENGINE_PREFIX" in checks["engine_prefix"]["message"]
    assert body["engine"]["prefix_configured"] is False


def test_a_explicit_prefix_marks_present_with_env_name(tmp_path, monkeypatch):
    env = _make_conda_env(tmp_path / "cenvs", "insar")
    monkeypatch.setenv("INSAR_ENGINE_PREFIX", str(env))
    body = _status(tmp_path)
    checks = _by_key(body)
    assert body["ready"] is True
    # 关键词:present(insar) 标注来源是 conda 环境目录而非 PATH
    for key in ("engine_mintpy", "engine_gdal", "engine_snaphu", "engine_pyaps"):
        assert checks[key]["ok"] is True, key
        assert "present(insar)" in checks[key]["message"], key
    assert checks["engine_prefix"]["ok"] is True
    assert str(env) in checks["engine_prefix"]["message"]
    assert body["engine"]["prefix_configured"] is True
    assert body["engine"]["prefix_exists"] is True
    assert body["engine"]["engines"]["mintpy"] == "present(insar)"


def test_a_implicit_conda_root_makes_ready_regression_round2(tmp_path, monkeypatch):
    """回归锁·用户实测二报(c834f93):裸 PATH(启动 shell 无 conda)+ conda 装在
    已知安装位 + 未配置 INSAR_ENGINE_PREFIX → 隐式回退,ready 必须为 True。"""
    root = tmp_path / "miniforge3" / "envs"
    _make_conda_env(root, "insar", engines=("mintpy", "gdal", "pyaps"))
    monkeypatch.setattr(probe_mod, "_KNOWN_ENV_ROOTS", (root,))
    body = _status(tmp_path)
    checks = _by_key(body)
    assert body["ready"] is True
    assert checks["engine_mintpy"]["ok"] is True
    assert "present(insar)" in checks["engine_mintpy"]["message"]
    assert checks["engine_gdal"]["ok"] is True
    assert checks["engine_prefix"]["ok"] is True
    # 隐式回退 ≠ 已配置:摘要如实报告未配置(向导仍可引导用户显式保存)
    assert body["engine"]["prefix_configured"] is False


def test_a_nothing_found_not_ready(tmp_path):
    body = _status(tmp_path)
    checks = _by_key(body)
    assert body["ready"] is False
    assert checks["engine_mintpy"]["ok"] is False
    assert checks["engine_mintpy"]["required"] is True
    assert "未探测到 MintPy" in checks["engine_mintpy"]["message"]
    assert checks["engine_prefix"]["ok"] is False
    assert "未配置引擎环境" in checks["engine_prefix"]["message"]
    assert "engine-env" in checks["engine_prefix"]["fix_hint"]


def test_a_explicit_prefix_dir_exists_but_empty(tmp_path, monkeypatch):
    """显式 prefix 目录存在但没装引擎:prefix 存在性检查通过,引擎检查如实红。"""
    env = _make_conda_env(tmp_path / "cenvs", "insar", engines=())
    monkeypatch.setenv("INSAR_ENGINE_PREFIX", str(env))
    body = _status(tmp_path)
    checks = _by_key(body)
    assert checks["engine_prefix"]["ok"] is True  # 目录确实存在
    assert checks["engine_mintpy"]["ok"] is False
    assert checks["engine_gdal"]["ok"] is False
    assert body["ready"] is False


# ================ 隐式 prefix 优先级(显式必须压过隐式) ================

def test_priority_explicit_env_beats_implicit_insar(tmp_path, monkeypatch):
    """显式 INSAR_ENGINE_PREFIX 指向别的环境时,绝不采用隐式扫到的 insar。"""
    root = tmp_path / "miniforge3" / "envs"
    _make_conda_env(root, "insar")
    monkeypatch.setattr(probe_mod, "_KNOWN_ENV_ROOTS", (root,))
    explicit = _make_conda_env(tmp_path / "elsewhere", "team_env")
    monkeypatch.setenv("INSAR_ENGINE_PREFIX", str(explicit))
    probe = probe_mod.probe_environment(tmp_path, check_wsl=False)
    assert probe.engines["mintpy"] == "present(team_env)"
    assert probe.engines["gdal"] == "present(team_env)"


def test_priority_explicit_bad_path_does_not_fall_back(tmp_path, monkeypatch):
    """决策树原则核对:显式 prefix 指向坏目录 → 如实探测失败,不静默兜底到隐式。"""
    root = tmp_path / "miniforge3" / "envs"
    _make_conda_env(root, "insar")  # 隐式候选明明可用
    monkeypatch.setattr(probe_mod, "_KNOWN_ENV_ROOTS", (root,))
    monkeypatch.setenv("INSAR_ENGINE_PREFIX", str(tmp_path / "no-such-env"))
    probe = probe_mod.probe_environment(tmp_path, check_wsl=False)
    assert probe.engines["mintpy"] is None
    assert probe.engines["gdal"] is None


def test_priority_explicit_bad_path_router_reports_honestly(tmp_path, monkeypatch):
    """同上场景走到向导:engine_prefix 必需项红 + 消息带路径,ready=False。"""
    root = tmp_path / "miniforge3" / "envs"
    _make_conda_env(root, "insar")
    monkeypatch.setattr(probe_mod, "_KNOWN_ENV_ROOTS", (root,))
    bad = tmp_path / "no-such-env"
    monkeypatch.setenv("INSAR_ENGINE_PREFIX", str(bad))
    body = _status(tmp_path)
    checks = _by_key(body)
    assert body["ready"] is False
    assert checks["engine_prefix"]["ok"] is False
    assert checks["engine_prefix"]["required"] is True
    assert "不存在" in checks["engine_prefix"]["message"]
    assert str(bad) in checks["engine_prefix"]["message"]
    assert checks["engine_mintpy"]["ok"] is False  # 不拿隐式候选顶包


def test_priority_insar_named_env_wins_within_root(tmp_path, monkeypatch):
    """同一扫描根里多个含 mintpy 的环境:名为 insar 的优先(压过字母序)。"""
    root = tmp_path / "envs"
    _make_conda_env(root, "aaa")    # 字母序在 insar 之前
    _make_conda_env(root, "insar")
    monkeypatch.setattr(probe_mod, "_KNOWN_ENV_ROOTS", (root,))
    assert probe_mod._implicit_engine_prefix() == str(root / "insar")


def test_priority_root_order_beats_env_name(tmp_path, monkeypatch):
    """扫描根顺序即优先级:第一个根命中就返回,后面根里的 insar 不回头。"""
    r1 = tmp_path / "root1"
    r2 = tmp_path / "root2"
    _make_conda_env(r1, "zzz")
    _make_conda_env(r2, "insar")
    monkeypatch.setattr(probe_mod, "_KNOWN_ENV_ROOTS", (r1, r2))
    assert probe_mod._implicit_engine_prefix() == str(r1 / "zzz")


def test_priority_implicit_requires_mintpy_marker(tmp_path, monkeypatch):
    """隐式判据 = 环境里有 mintpy:只装 gdal 的环境不算(也不半采信其 gdal);
    扫描根下的普通文件不干扰。"""
    root = tmp_path / "envs"
    _make_conda_env(root, "gdal_only", engines=("gdal",))
    (root / "environments.txt").write_text("干扰文件", encoding="utf-8")
    monkeypatch.setattr(probe_mod, "_KNOWN_ENV_ROOTS", (root,))
    assert probe_mod._implicit_engine_prefix() is None
    probe = probe_mod.probe_environment(tmp_path, check_wsl=False)
    assert probe.engines["gdal"] is None
    assert probe.engines["mintpy"] is None


def test_priority_empty_env_var_means_cleared_uses_implicit(tmp_path, monkeypatch):
    """INSAR_ENGINE_PREFIX="" 表示已清除(save 端点的清除语义)→ 走隐式回退。"""
    root = tmp_path / "envs"
    _make_conda_env(root, "insar")
    monkeypatch.setattr(probe_mod, "_KNOWN_ENV_ROOTS", (root,))
    monkeypatch.setenv("INSAR_ENGINE_PREFIX", "")
    probe = probe_mod.probe_environment(tmp_path, check_wsl=False)
    assert probe.engines["mintpy"] == "present(insar)"


def test_priority_no_roots_no_implicit(tmp_path):
    """密封基线(扫描根不存在)下隐式回退返回 None,行为与旧版完全一致。"""
    assert probe_mod._implicit_engine_prefix() is None
    probe = probe_mod.probe_environment(tmp_path, check_wsl=False)
    assert all(v is None for v in probe.engines.values())


# ================ 维度 B:WSL(可达含/无 snaphu、不可达、抛异常) ================

def test_b_wsl_snaphu_fallback_regression_round1(tmp_path, monkeypatch):
    """回归锁·用户实测一报(749fa3e):宿主 PATH 没有 snaphu 但 WSL 里有 →
    engine_snaphu 必须按 "(wsl)" 通过(isce2/snaphu 作业本就路由到 WSL 执行)。"""
    _set_path_engines(monkeypatch, ("mintpy", "gdal"))
    _set_wsl(monkeypatch, _wsl_ok(snaphu=True))
    body = _status(tmp_path)
    checks = _by_key(body)
    assert checks["engine_snaphu"]["ok"] is True
    assert "(wsl)" in checks["engine_snaphu"]["message"]
    assert "2.0.6" in checks["engine_snaphu"]["message"]
    assert body["ready"] is True


def test_b_wsl_reachable_without_snaphu_stays_optional_miss(tmp_path, monkeypatch):
    _set_path_engines(monkeypatch, ("mintpy", "gdal"))
    _set_wsl(monkeypatch, _wsl_ok(snaphu=False))
    body = _status(tmp_path)
    checks = _by_key(body)
    assert checks["engine_snaphu"]["ok"] is False
    assert checks["engine_snaphu"]["required"] is False
    assert "可选" in checks["engine_snaphu"]["message"]
    assert body["ready"] is True  # 可选缺失不拦 ready


def test_b_wsl_unreachable_adds_no_wsl_traces(tmp_path, monkeypatch):
    _set_path_engines(monkeypatch, ("mintpy", "gdal"))
    _set_wsl(monkeypatch, _wsl_down())
    body = _status(tmp_path)
    for c in body["checks"]:
        assert "(wsl)" not in c["message"], c["key"]
    for v in body["engine"]["engines"].values():
        assert "(wsl)" not in (v or "")
    assert body["ready"] is True


def test_b_wsl_probe_exception_swallowed(tmp_path, monkeypatch):
    """探测本体抛异常:status 不 5xx、行为与不可达一致(没装 WSL 的机器不受影响)。"""
    _set_path_engines(monkeypatch, ("mintpy", "gdal"))
    _set_wsl(monkeypatch, "raise")
    body = _status(tmp_path)
    checks = _by_key(body)
    assert body["ready"] is True
    assert checks["engine_snaphu"]["ok"] is False
    for c in body["checks"]:
        assert "(wsl)" not in c["message"]


def test_b_local_snaphu_beats_wsl(tmp_path, monkeypatch):
    """本地优先、WSL 兜底:宿主 PATH 有 snaphu 时消息不得标 (wsl)。"""
    _set_path_engines(monkeypatch, ("mintpy", "gdal", "snaphu"))
    _set_wsl(monkeypatch, _wsl_ok(snaphu=True))
    body = _status(tmp_path)
    snaphu = _by_key(body)["engine_snaphu"]
    assert snaphu["ok"] is True
    assert "(wsl)" not in snaphu["message"]


def test_b_wsl_mintpy_fallback_counts_for_required_check(tmp_path, monkeypatch):
    """现状锁定:本地无 mintpy 而 WSL 有 → engine_mintpy 按 "(wsl)" 通过。
    注意路由事实:mintpy 默认走本地后端(backend_select.WSL_ENGINES 只有
    isce2/snaphu),此格的绿针对工作区在 WSL 的条带链路线;749fa3e 用户反馈
    确立的行为,矩阵锁住以防无意回退。"""
    _set_path_engines(monkeypatch, ("gdal",))
    _set_wsl(monkeypatch, _wsl_ok())
    body = _status(tmp_path)
    checks = _by_key(body)
    assert checks["engine_mintpy"]["ok"] is True
    assert "(wsl)" in checks["engine_mintpy"]["message"]
    assert "1.6.4" in checks["engine_mintpy"]["message"]
    assert checks["engine_prefix"]["ok"] is True
    assert body["ready"] is True


# ================ 维度 C:数据源(未配 / 好 / 坏路径 / 空目录) ================

def test_c_unset_is_optional_neutral(tmp_path, monkeypatch):
    _set_path_engines(monkeypatch, ("mintpy", "gdal"))
    body = _status(tmp_path)
    ds = _by_key(body)["data_source"]
    assert ds["ok"] is False
    assert ds["required"] is False
    assert "可选" in ds["message"]
    assert body["ready"] is True  # 未配数据源不锁死"开始使用"
    assert body["data"]["configured"] is False


def test_c_good_source_counts_pairs(tmp_path, monkeypatch):
    _set_path_engines(monkeypatch, ("mintpy", "gdal"))
    src = _make_hyp3_source(tmp_path / "RidgecrestSenDT71", pairs=2)
    monkeypatch.setenv("INSAR_HYP3_SOURCE", str(src))
    body = _status(tmp_path)
    ds = _by_key(body)["data_source"]
    assert ds["ok"] is True
    assert "解缠相位栅格 2 个" in ds["message"]
    # 缺陷修复回归:可选属性不随状态翻转,配好了也不该变回 required=True
    assert ds["required"] is False
    assert body["data"] == {"source": str(src), "configured": True,
                            "exists": True, "pair_count": 2}
    assert body["ready"] is True


def test_c_bad_path_reports_honestly_without_blocking(tmp_path, monkeypatch):
    """配了坏路径不沉默(用户显然想用它)也不拦 ready(仍是可选项)。"""
    _set_path_engines(monkeypatch, ("mintpy", "gdal"))
    bad = tmp_path / "no-such-source"
    monkeypatch.setenv("INSAR_HYP3_SOURCE", str(bad))
    body = _status(tmp_path)
    ds = _by_key(body)["data_source"]
    assert ds["ok"] is False
    assert ds["required"] is False
    assert "不存在" in ds["message"]
    assert ds["fix_hint"]
    assert body["ready"] is True
    assert body["data"]["exists"] is False


def test_c_empty_dir_reports_missing_rasters(tmp_path, monkeypatch):
    _set_path_engines(monkeypatch, ("mintpy", "gdal"))
    src = _make_hyp3_source(tmp_path / "empty-source", pairs=0)
    monkeypatch.setenv("INSAR_HYP3_SOURCE", str(src))
    body = _status(tmp_path)
    ds = _by_key(body)["data_source"]
    assert ds["ok"] is False
    assert ds["required"] is False
    assert "unw_phase_clipped" in ds["message"]
    assert body["ready"] is True
    assert body["data"]["exists"] is True
    assert body["data"]["pair_count"] == 0


def test_c_nested_hyp3_layout_supported(tmp_path, monkeypatch):
    """根目录下带 hyp3/ 子目录的布局同样计数(与 engines/localdata 同判据)。"""
    _set_path_engines(monkeypatch, ("mintpy", "gdal"))
    src = _make_hyp3_source(tmp_path / "proj-root", pairs=1, nested=True)
    monkeypatch.setenv("INSAR_HYP3_SOURCE", str(src))
    body = _status(tmp_path)
    assert _by_key(body)["data_source"]["ok"] is True
    assert body["data"]["pair_count"] == 1


# ================ 全矩阵扫描:A × B 的 ready 恒等式与 required 恒定 ================

@pytest.mark.parametrize("wsl_state", ["ok_snaphu", "ok_no_snaphu", "down", "raise"])
@pytest.mark.parametrize("engine_source", ["path", "explicit", "implicit", "none"])
def test_matrix_ready_invariants(tmp_path, monkeypatch, engine_source, wsl_state):
    """16 格恒等式:检查项键集不变;required 标志不随格变;
    ready == 必需项全过 == 引擎有本地着落(WSL 不提供 gdal,救不了"全无")。"""
    if engine_source == "path":
        _set_path_engines(monkeypatch, ("mintpy", "gdal"))
    elif engine_source == "explicit":
        env = _make_conda_env(tmp_path / "cenvs", "insar")
        monkeypatch.setenv("INSAR_ENGINE_PREFIX", str(env))
    elif engine_source == "implicit":
        root = tmp_path / "miniforge3" / "envs"
        _make_conda_env(root, "insar")
        monkeypatch.setattr(probe_mod, "_KNOWN_ENV_ROOTS", (root,))
    _set_wsl(monkeypatch, {"ok_snaphu": _wsl_ok(True), "ok_no_snaphu": _wsl_ok(False),
                           "down": _wsl_down(), "raise": "raise"}[wsl_state])

    body = _status(tmp_path)
    checks = _by_key(body)
    assert set(checks) == ALL_KEYS
    assert body["ready"] == all(c["ok"] for c in body["checks"] if c["required"])
    for key in REQUIRED_KEYS:
        assert checks[key]["required"] is True, key
    for key in OPTIONAL_KEYS:
        assert checks[key]["required"] is False, key
    assert body["ready"] is (engine_source != "none")
    for c in body["checks"]:
        if not c["ok"]:
            assert c["fix_hint"], f"未通过项必须给中文修复建议:{c['key']}"


# ================ WSL 探测 TTL 缓存(wsl_probe._PROBE_CACHE) ================
# conftest 的 autouse fixture 已保证每个测试从空缓存出发。

def _counting_wsl_probe(monkeypatch, results: list[dict]):
    """按调用序返回预设结果(最后一个重复),记录调用供计数断言。"""
    calls: list[str] = []

    def fake(distro="insar", runner=None, timeout=60.0):
        calls.append(distro)
        return results[min(len(calls), len(results)) - 1]

    monkeypatch.setattr(wp, "probe_wsl_engines", fake)
    return calls


def test_cache_hit_within_ttl_returns_same_object(monkeypatch):
    ok = _wsl_ok()
    calls = _counting_wsl_probe(monkeypatch, [ok])
    assert _REAL_CACHED() is ok
    assert _REAL_CACHED() is ok  # 秒回:同一对象,不再探测
    assert calls == ["insar"]
    assert "insar" in wp._PROBE_CACHE


def test_cache_expired_entry_reprobes_and_refreshes(monkeypatch):
    first, second = _wsl_ok(), _wsl_ok()
    calls = _counting_wsl_probe(monkeypatch, [first, second])
    assert _REAL_CACHED() is first
    ts, res = wp._PROBE_CACHE["insar"]
    wp._PROBE_CACHE["insar"] = (ts - (wp._PROBE_CACHE_TTL + 1.0), res)  # 人为过期
    assert _REAL_CACHED() is second
    assert len(calls) == 2
    assert wp._PROBE_CACHE["insar"][1] is second  # 缓存已刷新为新结果
    assert wp._PROBE_CACHE["insar"][0] > ts - wp._PROBE_CACHE_TTL


def test_cache_failure_not_cached_retries_next_call(monkeypatch):
    """失败(未装 WSL/超时)不缓存:一次冷启动失败不得污染 5 分钟窗口。"""
    down = _wsl_down()
    calls = _counting_wsl_probe(monkeypatch, [down])
    assert _REAL_CACHED() is down
    assert "insar" not in wp._PROBE_CACHE
    assert _REAL_CACHED() is down
    assert len(calls) == 2  # 每次都重试


def test_cache_force_bypasses_fresh_hit(monkeypatch):
    first, second = _wsl_ok(), _wsl_ok()
    calls = _counting_wsl_probe(monkeypatch, [first, second])
    assert _REAL_CACHED() is first
    assert _REAL_CACHED(force=True) is second  # 窗口内也强制重探
    assert len(calls) == 2
    assert wp._PROBE_CACHE["insar"][1] is second


def test_cache_ttl_zero_always_reprobes(monkeypatch):
    first, second = _wsl_ok(), _wsl_ok()
    calls = _counting_wsl_probe(monkeypatch, [first, second])
    assert _REAL_CACHED(ttl=0.0) is first
    assert _REAL_CACHED(ttl=0.0) is second
    assert len(calls) == 2


def test_cache_keyed_by_distro(monkeypatch):
    calls: list[str] = []

    def fake(distro="insar", runner=None, timeout=60.0):
        calls.append(distro)
        return {**_wsl_ok(), "distro": distro}

    monkeypatch.setattr(wp, "probe_wsl_engines", fake)
    assert _REAL_CACHED(distro="a")["distro"] == "a"
    assert _REAL_CACHED(distro="b")["distro"] == "b"
    assert _REAL_CACHED(distro="a")["distro"] == "a"  # 命中 a 的缓存
    assert calls == ["a", "b"]
    assert set(wp._PROBE_CACHE) == {"a", "b"}


# ================ 真实 WSL 秒级冒烟(仅本机有 insar 发行版时) ================

def _wsl_distro_listed(distro: str = "insar") -> bool:
    """wsl.exe -l -q 只查注册表内清单,不会拉起发行版。"""
    exe = shutil.which("wsl.exe")
    if not exe:
        return False
    try:
        cp = subprocess.run([exe, "-l", "-q"], capture_output=True, timeout=5,
                            creationflags=wp._NO_WINDOW,
                            env={**os.environ, "WSL_UTF8": "1"})
    except Exception:
        return False
    names = wp._decode(cp.stdout).split()
    return any(n.casefold() == distro for n in names)


def test_real_wsl_reachability_echo_smoke():
    """真实 WSL 冒烟:单条 echo 验证可达性契约成立;不逐引擎探测(那要 ~20s)。"""
    if not _wsl_distro_listed():
        pytest.skip("无 WSL 或未注册 insar 发行版(秒级冒烟仅真机执行)")
    rc, out, err, error = wp._run(wp._default_runner,
                                  wp._argv("insar", "echo __setup_matrix_smoke__"), 20.0)
    assert error is None, error
    assert rc == 0
    assert "__setup_matrix_smoke__" in out
