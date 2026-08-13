"""环境安装助手(runtime/install_guide + api/install_router)契约测试。

覆盖四块:
  1. 知识表完整性:七引擎全覆盖、途径闭集、命令非空、requires 引用 REQUIREMENTS 闭集、
     关键命令口径对照实测(wsl_setup.ps1 / 清华镜像 / OpenBLAS 坑位);
  2. plan_installs:按探测状态正确筛选(本地优先、WSL 兜底、必需项排前、
     前置未满足时的推荐降级与解释);
  3. API 形状:GET /api/install/guide、POST /api/install/mark-done(未知引擎 400、
     mark-done 穿透 WSL 探测缓存 force=True、重探后 present 翻转);
  4. 安全边界:路由面闭集(只有 guide/mark-done 两个端点,绝无代跑安装的端点);
     前端/挂载的防漂移锚(installguide.js 引用两端点、index.html/app.py 接线)。

独立 FastAPI 实例挂 install_router,不经 create_app(同 test_setup_router 做法);
探测全部密封(mock probe),不受宿主 PATH/conda/WSL 影响。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from insar_agent.api.install_router import create_install_router
from insar_agent.runtime.install_guide import (
    ENGINE_ORDER,
    INSTALL_GUIDE,
    REQUIRED_ENGINES,
    REQUIREMENTS,
    install_hint_for,
    plan_installs,
    resolve_engines,
)
from insar_agent.runtime.probe import ProbeResult

ROOT = Path(__file__).resolve().parents[1]

_ALL_MISSING = {e: None for e in ENGINE_ORDER}
_WSL_READY = {"installed": True, "distros": ["insar"]}
_WSL_ABSENT = {"installed": False, "distros": []}


def _probe(engines: dict | None = None, wsl: dict | None = None) -> ProbeResult:
    base = dict(_ALL_MISSING)
    base.update(engines or {})
    return ProbeResult(engines=base, wsl=dict(wsl or {}))


# ---------------- 1. 知识表完整性 ----------------

def test_guide_covers_all_seven_engines():
    assert set(INSTALL_GUIDE) == set(ENGINE_ORDER)
    assert len(ENGINE_ORDER) == 7


def test_route_shapes_and_closed_sets():
    """每条途径:route 闭集、steps/notes 非空字符串、耗时磁盘为正、requires 引用闭集。"""
    for engine, spec in INSTALL_GUIDE.items():
        assert spec["label"] and spec["hint"], engine
        assert spec["routes"], f"{engine} 必须至少给一条安装途径"
        for route in spec["routes"]:
            rid = route["route"]
            assert rid in ("conda", "wsl", "manual"), f"{engine}: 未知途径 {rid}"
            assert route["title"], f"{engine}/{rid}: title 为空"
            assert route["steps"], f"{engine}/{rid}: steps 为空"
            assert all(isinstance(s, str) and s.strip() for s in route["steps"]), \
                f"{engine}/{rid}: 存在空步骤"
            assert route["est_minutes"] > 0, f"{engine}/{rid}: est_minutes 必须为正"
            assert route["disk_gb"] >= 0, f"{engine}/{rid}: disk_gb 不能为负"
            assert isinstance(route["notes"], list), f"{engine}/{rid}: notes 应为列表"
            unknown = set(route["requires"]) - set(REQUIREMENTS)
            assert not unknown, f"{engine}/{rid}: requires 引用了闭集外的键 {unknown}"
        # rationale 覆盖该引擎声明的全部途径(plan_installs 的 why 不缺词)
        assert {r["route"] for r in spec["routes"]} <= set(spec["rationale"]), engine


def test_route_availability_matrix():
    """途径矩阵:isce2 仅 WSL;snaphu WSL+conda;pystamps/snap 仅手动。"""
    of = lambda e: [r["route"] for r in INSTALL_GUIDE[e]["routes"]]  # noqa: E731
    assert of("isce2") == ["wsl"]
    assert of("snaphu") == ["wsl", "conda"]      # WSL 在前:apt 版才有 PATH 可执行
    assert of("mintpy") == ["conda", "wsl"]
    assert of("gdal") == ["conda", "wsl"]
    assert of("pyaps") == ["conda", "wsl"]
    assert of("pystamps") == ["manual"]
    assert of("snap") == ["manual"]


def test_commands_match_measured_sources():
    """命令口径对照实测:不编造包名/镜像,与 wsl_setup / setup_router / README 一致。"""
    joined = lambda e, rid: "\n".join(  # noqa: E731
        s for r in INSTALL_GUIDE[e]["routes"] if r["route"] == rid for s in r["steps"])
    # WSL 途径:一键脚本 + wsl_probe 验证(docs/WSL-SETUP.md §2-3 原样命令)
    for engine in ("isce2", "mintpy", "snaphu", "gdal", "pyaps"):
        wsl = joined(engine, "wsl")
        assert r"wsl_setup.ps1" in wsl, engine
        assert "insar_agent.runtime.wsl_probe" in wsl, engine
        assert "wsl --import insar E:\\wsl\\insar" in wsl, engine
    # conda 途径:清华 conda-forge 镜像 + --override-channels(setup_router 同口径)
    conda_mintpy = joined("mintpy", "conda")
    assert "mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge" in conda_mintpy
    assert "--override-channels" in conda_mintpy
    assert "python=3.11 mintpy" in conda_mintpy
    # README 坑位:Windows conda 必须切 OpenBLAS(MKL 2024 硬崩)
    assert 'libblas=*=*openblas' in conda_mintpy
    notes_mintpy = "\n".join(
        n for r in INSTALL_GUIDE["mintpy"]["routes"] if r["route"] == "conda"
        for n in r["notes"])
    assert "OpenBLAS" in notes_mintpy and "MKL" in notes_mintpy
    # snaphu 坑位:conda-forge 包是 snaphu-py 包装器(WSL-SETUP.md 实测)
    notes_snaphu = "\n".join(
        n for r in INSTALL_GUIDE["snaphu"]["routes"] for n in r["notes"])
    assert "snaphu-py" in notes_snaphu
    # pystamps 诚实缺席:不编造包名,占位符显式标注
    manual_pystamps = joined("pystamps", "manual")
    assert "pip install <PyStamps 源码目录>" in manual_pystamps
    assert "conda install" not in manual_pystamps


def test_install_hint_for_all_engines_and_unknown():
    for engine in ENGINE_ORDER:
        hint = install_hint_for(engine)
        assert hint.startswith(f"{engine}:") and len(hint) > 10
    unknown = install_hint_for("nosuch")
    assert "未收录" in unknown and "nosuch" in unknown


# ---------------- 2. plan_installs ----------------

def test_plan_empty_when_all_present():
    probe = _probe({e: "present" for e in ENGINE_ORDER})
    assert plan_installs(probe, _WSL_READY) == []


def test_plan_resolves_wsl_suffix_keys_as_present():
    """isce2 (wsl) 键在位 → isce2 不算缺失(本地优先、WSL 兜底,与向导同口径)。"""
    engines = {e: "present" for e in ENGINE_ORDER if e != "isce2"}
    engines["isce2"] = None
    engines["isce2 (wsl)"] = "2.6.5"
    probe = _probe(engines)
    assert plan_installs(probe, _WSL_READY) == []
    assert resolve_engines(probe)["isce2"] == "2.6.5 (wsl)"


def test_plan_orders_required_engines_first():
    plans = plan_installs(_probe(), _WSL_READY)
    assert [p["engine"] for p in plans] == list(
        sorted(ENGINE_ORDER, key=lambda e: (e not in REQUIRED_ENGINES,
                                            ENGINE_ORDER.index(e))))
    assert [p["engine"] for p in plans[:2]] == ["mintpy", "gdal"]
    assert all(p["required"] for p in plans[:2])
    assert not any(p["required"] for p in plans[2:])


def test_plan_recommendations_with_wsl_ready():
    plans = {p["engine"]: p for p in plan_installs(_probe(), _WSL_READY)}
    assert plans["isce2"]["recommend"] == "wsl"
    assert "唯一途径" in plans["isce2"]["why"]
    assert plans["isce2"]["unmet"] == []
    assert plans["snaphu"]["recommend"] == "wsl"          # apt 版才有 PATH 可执行
    assert plans["mintpy"]["recommend"] == "conda"        # 本机最快可验证
    assert plans["pystamps"]["recommend"] == "manual"
    assert plans["snap"]["recommend"] == "manual"
    # requires_detail:wsl2 已满足
    wsl_route = next(r for r in plans["isce2"]["routes"] if r["route"] == "wsl")
    assert wsl_route["requires_detail"] == [
        {"id": "wsl2", "label": REQUIREMENTS["wsl2"], "met": True}]


def test_plan_recommendations_when_wsl_absent():
    """WSL 未装:snaphu 降级推荐 conda(并解释);isce2 仍唯一 WSL 且标注前置未就绪。"""
    plans = {p["engine"]: p for p in plan_installs(_probe(), _WSL_ABSENT)}
    assert plans["snaphu"]["recommend"] == "conda"
    assert "前置未满足" in plans["snaphu"]["why"]          # 首选 WSL 被降级的解释
    assert plans["isce2"]["recommend"] == "wsl"            # 无备选:仍推荐唯一途径
    assert plans["isce2"]["unmet"] == ["wsl2"]
    assert "前置未就绪" in plans["isce2"]["why"]
    wsl_route = plans["isce2"]["routes"][0]
    assert wsl_route["requires_detail"][0]["met"] is False


def test_plan_wsl_unknown_when_not_probed():
    """本次没探测 WSL(wsl_status 空):wsl2 前置 met=None,不臆断也不降级。"""
    plans = {p["engine"]: p for p in plan_installs(_probe(), {})}
    assert plans["snaphu"]["recommend"] == "wsl"
    wsl_route = plans["isce2"]["routes"][0]
    assert wsl_route["requires_detail"][0]["met"] is None


def test_plan_does_not_mutate_knowledge_table():
    """plan_installs 返回的是深拷贝:调用方改 requires_detail/steps 不脏知识表。"""
    plans = plan_installs(_probe(), _WSL_ABSENT)
    plans[0]["routes"][0]["steps"].append("rm -rf /")
    plans[0]["routes"][0]["requires_detail"] = "脏数据"
    for spec in INSTALL_GUIDE.values():
        for route in spec["routes"]:
            assert "requires_detail" not in route
            assert "rm -rf /" not in route["steps"]


# ---------------- 3. API ----------------

@pytest.fixture()
def api(monkeypatch):
    """密封环境:本地探测与 WSL 探测都走假实现,记录调用参数。"""
    state = {"engines": dict(_ALL_MISSING), "probe_calls": 0, "wsl_forces": []}

    def fake_probe(*args, **kwargs):
        state["probe_calls"] += 1
        return _probe(state["engines"], wsl=_WSL_READY)

    def fake_wsl_cached(**kwargs):
        state["wsl_forces"].append(bool(kwargs.get("force")))
        return {"ok": False, "error": "sealed", "engines": {}}

    monkeypatch.setattr("insar_agent.api.install_router.probe_environment", fake_probe)
    monkeypatch.setattr(
        "insar_agent.runtime.wsl_probe.probe_wsl_engines_cached", fake_wsl_cached)
    app = FastAPI()
    app.include_router(create_install_router())
    with TestClient(app) as client:
        yield client, state


def test_guide_shape(api):
    client, state = api
    r = client.get("/api/install/guide")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"missing", "plans", "engines", "wsl"}
    assert body["missing"] == [p["engine"] for p in body["plans"]]
    assert set(body["missing"]) == set(ENGINE_ORDER)      # 密封探测:全缺
    assert set(body["engines"]) == set(ENGINE_ORDER)
    assert body["wsl"]["installed"] is True and body["wsl"]["distros"] == ["insar"]
    for plan in body["plans"]:
        assert set(plan) >= {"engine", "label", "required", "recommend", "why",
                             "unmet", "routes"}
        for route in plan["routes"]:
            assert set(route) >= {"route", "title", "steps", "est_minutes",
                                  "disk_gb", "notes", "requires", "requires_detail"}
    # guide 走缓存探测(force=False),不穿透
    assert state["wsl_forces"] == [False]


def test_mark_done_unknown_engine_400(api):
    client, _ = api
    r = client.post("/api/install/mark-done", json={"engine": "hyp3"})
    assert r.status_code == 400
    assert "未知引擎" in r.json()["detail"]
    for engine in ENGINE_ORDER:
        assert engine in r.json()["detail"]


def test_mark_done_forces_reprobe_and_reports_flip(api):
    """mark-done 缓存失效路径:WSL 探测 force=True 穿透 + 本地重探;装好后 present 翻转。"""
    client, state = api
    r = client.post("/api/install/mark-done", json={"engine": "mintpy"})
    assert r.status_code == 200
    body = r.json()
    assert body["engine"] == "mintpy" and body["present"] is False
    assert body["version"] is None
    assert "mintpy" in body["missing"]
    assert state["wsl_forces"] == [True]                  # 穿透 TTL 缓存强制重探
    probes_before = state["probe_calls"]

    state["engines"] = {**_ALL_MISSING, "mintpy": "1.6.4"}  # 用户装完了
    body = client.post("/api/install/mark-done", json={"engine": "mintpy"}).json()
    assert body["present"] is True and body["version"] == "1.6.4"
    assert "mintpy" not in body["missing"]
    assert all(p["engine"] != "mintpy" for p in body["plans"])
    assert state["probe_calls"] == probes_before + 1      # 每次确认都全新本地探测
    assert state["wsl_forces"] == [True, True]


def test_router_surface_is_guide_and_confirm_only():
    """安全边界:只有查询与确认两个端点,绝无「服务端代跑安装」的端点。"""
    router = create_install_router()
    surface = {(r.path, tuple(sorted(r.methods))) for r in router.routes}
    assert surface == {("/api/install/guide", ("GET",)),
                       ("/api/install/mark-done", ("POST",))}


# ---------------- 4. 防漂移锚(前端/挂载) ----------------

def test_frontend_references_both_endpoints():
    js = (ROOT / "prototype" / "js" / "installguide.js").read_text(encoding="utf-8")
    assert "/api/install/guide" in js
    assert "/api/install/mark-done" in js
    # 前端七引擎闭集与后端一致(防两边漂移)
    for engine in ENGINE_ORDER:
        assert f"'{engine}'" in js


def test_index_and_app_wiring():
    html = (ROOT / "prototype" / "index.html").read_text(encoding="utf-8")
    assert 'src="js/installguide.js"' in html
    assert 'href="css/installguide.css"' in html
    app_src = (ROOT / "src" / "insar_agent" / "api" / "app.py").read_text(encoding="utf-8")
    assert "create_install_router" in app_src
