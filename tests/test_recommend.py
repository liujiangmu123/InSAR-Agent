# -*- coding: utf-8 -*-
"""处理路线推荐(planner/recommend.py + api/recommend_router.py)。

覆盖:
  - 知识映射表闭集:kind 覆盖 catalog.KINDS 全集、suitable_scenarios 是
    场景闭集子集(对照 registry.scenarios 实际加载的技能包)、pros/cons
    各 2-4 条、steps_involved 落在 1-11 注册表内;
  - 四类数据的路线正确性(hyp3 直通/重取、slc 全链/PS/云端、alos 条带链、
    dem 配主数据、unknown 先识别引导);
  - probe 缺失引擎时 requirements 逐项标注 满足/缺失,可选项不计入 missing,
    WSL 合并键('isce2 (wsl)')等同原生引擎;
  - 排序确定且稳定:同输入两次同输出;环境残缺时「当下能跑」的路线上浮,
    同缺失数保持知识表声明序;
  - est_note 诚实纪律:引用数据规模与实测参照,不编时长;
  - 路由:GET /api/recommend 的 200 形状与未知 dataset_id 404;
    create_app 两行挂载的集成冒烟(探测打桩,不触发真实引擎/WSL 探测)。

全部测试注入固定 ProbeResult(纯规则引擎的确定性正是本期卖点),
不起网络端口、不跑真实计算。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from insar_agent.api.recommend_router import create_recommend_router
from insar_agent.data.catalog import KINDS, dataset_id
from insar_agent.planner.recommend import _ROUTES, SCENARIO_KEYS, recommend_routes
from insar_agent.registry.capabilities import REGISTRY
from insar_agent.registry.scenarios import SCENARIOS
from insar_agent.runtime.probe import ProbeResult

# ---------------- probe 桩(固定输入 → 固定输出) ----------------

FULL_ENGINES = {"isce2": "present", "snaphu": "present", "mintpy": "1.5.3",
                "gdal": "present", "pyaps": "present", "pystamps": "present"}


def probe_full() -> ProbeResult:
    """全引擎 + 全凭据:所有路线 ready。"""
    return ProbeResult(engines=dict(FULL_ENGINES),
                       credentials={"earthdata": True, "cds": True, "gacos": False})


def probe_bare() -> ProbeResult:
    """裸机:无引擎无凭据(engines 键存在但值 None = 已探测、缺失)。"""
    return ProbeResult(engines={k: None for k in FULL_ENGINES},
                       credentials={"earthdata": False, "cds": False, "gacos": False})


def probe_wsl_only() -> ProbeResult:
    """isce2 只在 WSL(merge_wsl_probe 的 'xxx (wsl)' 键口径);mintpy 原生在。"""
    return ProbeResult(
        engines={"isce2": None, "isce2 (wsl)": "present(insar)", "mintpy": "1.5.3",
                 "snaphu": None, "gdal": None, "pyaps": None, "pystamps": None},
        credentials={"earthdata": False, "cds": False, "gacos": False})


# ---------------- 数据集条目桩(catalog.identify_dataset 的输出形状) ----------------

def ds(kind: str, detail: dict | None = None, **extra) -> dict:
    base = {"id": "x" * 12, "path": r"E:\data\demo", "name": "demo", "kind": kind,
            "size_bytes": 400 << 20, "file_count": 5,
            "date_range": {"start": "2019-07-04", "end": "2019-07-28"},
            "detail": detail or {}}
    base.update(extra)
    return base


DS_HYP3 = ds("hyp3", {"pairs": 11, "unw": 11, "corr": 11, "truncated": False})
DS_ALOS = ds("alos_raw", {"scenes": 2, "img": 2, "led": 2, "truncated": False})
DS_SLC = ds("slc_stack", {"slc": 25, "safe": 0, "truncated": False})
DS_DEM = ds("dem", {"dem_files": ["dem.wgs84"], "truncated": False})
DS_UNKNOWN = ds("unknown", {"truncated": False})


# ---------------- 知识映射表闭集 ----------------

def test_knowledge_covers_all_kinds():
    """四类数据 + unknown 各有 1-3 条路线,与识别器类型闭集逐一对应。"""
    assert set(_ROUTES) == set(KINDS)
    for kind, routes in _ROUTES.items():
        assert 1 <= len(routes) <= 3, kind


def test_scenarios_are_closed_subset():
    """suitable_scenarios ⊆ 场景闭集;闭集常量与实际加载的技能包一致。"""
    assert set(SCENARIO_KEYS) == {sc.key for sc in SCENARIOS}
    for routes in _ROUTES.values():
        for r in routes:
            assert set(r["scenarios"]) <= set(SCENARIO_KEYS), r["route_id"]


def test_pros_cons_counts_and_steps_in_registry():
    """pros/cons 各 2-4 条;steps_involved 全部落在 11 步注册表内。"""
    for routes in _ROUTES.values():
        for r in routes:
            assert 2 <= len(r["pros"]) <= 4, r["route_id"]
            assert 2 <= len(r["cons"]) <= 4, r["route_id"]
            assert set(r["steps"]) <= set(REGISTRY), r["route_id"]


# ---------------- 四类数据的路线正确性 ----------------

def test_hyp3_routes():
    routes = recommend_routes(DS_HYP3, probe_full())
    assert [r.route_id for r in routes] == ["hyp3_direct", "slc_reacquire"]
    direct = routes[0]
    # 直通路线跳过 2-6(云端已完成),从产品直接进第 7 步
    assert direct.steps_involved == (1, 7, 8, 9, 10, 11)
    assert direct.ready is True and direct.missing == ()
    assert "11 对" in direct.est_note            # 规模取自 detail.pairs
    assert "402 MB" in direct.est_note           # 实测参照(skills/01)
    assert any("免 2-6 步" in p for p in direct.pros)
    assert any("VV" in c for c in direct.cons)   # 极化限制如实告知


def test_alos_routes_wsl_engine_counts():
    routes = recommend_routes(DS_ALOS, probe_wsl_only())
    assert [r.route_id for r in routes] == ["stripmap_alos"]
    alos = routes[0]
    assert alos.suitable_scenarios == ("stripmap_coseismic",)
    # isce2 只装在 WSL('isce2 (wsl)' 合并键)也算满足 —— 条带链本就在 WSL 实测
    assert alos.ready is True
    isce2_req = next(r for r in alos.requirements if r["key"] == "isce2")
    assert isce2_req["ok"] is True
    assert any("pickle" in c for c in alos.cons)   # 条带链分段约束如实告知
    assert "33 min" in alos.est_note and "勿线性外推" in alos.est_note


def test_slc_routes():
    routes = recommend_routes(DS_SLC, probe_full())
    assert [r.route_id for r in routes] == ["full_chain_s1", "ps_chain_s1", "hyp3_cloud"]
    full = routes[0]
    assert full.steps_involved == tuple(range(1, 12))
    assert "25 景" in full.est_note
    # 25 景满足 PS 下界(≥20-25),est_note 如实判定
    ps = routes[1]
    assert "满足 PS 景数下界" in ps.est_note


def test_ps_scene_floor_honest_note():
    few = ds("slc_stack", {"slc": 12, "safe": 0, "truncated": False})
    ps = next(r for r in recommend_routes(few, probe_full())
              if r.route_id == "ps_chain_s1")
    assert "低于 PS 下界" in ps.est_note


def test_dem_route():
    routes = recommend_routes(DS_DEM, probe_full())
    assert [r.route_id for r in routes] == ["dem_only"]
    dem = routes[0]
    assert dem.steps_involved == (2,)
    assert any("配主数据" in c or "搭配主数据" in c for c in dem.cons)
    # DEM 是辅助数据:无必需引擎,永远 ready
    assert dem.ready is True and all(r["optional"] for r in dem.requirements)


def test_nisar_gunw_route():
    nisar = ds("nisar", {"gunw": 3, "h5": 3, "truncated": False})
    routes = recommend_routes(nisar, probe_full())
    assert [r.route_id for r in routes] == ["nisar_gunw"]
    r0 = routes[0]
    assert r0.steps_involved == (1, 7, 8, 9, 10, 11)
    assert r0.ready is True
    assert "GUNW 3" in r0.est_note


def test_gamma_lt1_route():
    gamma = ds("gamma", {"par": 4, "diff": 4, "mli": 0, "truncated": False})
    routes = recommend_routes(gamma, probe_full())
    assert [r.route_id for r in routes] == ["lt1_gamma_layout"]
    r0 = routes[0]
    assert r0.steps_involved == tuple(range(1, 12))
    assert "lt1_gamma" in r0.suitable_scenarios
    assert "4 个 .par" in r0.est_note


def test_displacement_mode_c_route():
    disp = ds("displacement", {"tif": 2, "csv": 1, "truncated": False})
    routes = recommend_routes(disp, probe_bare())
    assert [r.route_id for r in routes] == ["mode_c_analysis"]
    r0 = routes[0]
    assert r0.steps_involved == (20, 21, 22, 23, 24, 25)
    assert r0.ready is True  # GDAL 可选,裸机也可规划模式 C
    assert "2 个 GeoTIFF" in r0.est_note


def test_unknown_gives_identify_guidance():
    routes = recommend_routes(DS_UNKNOWN, probe_bare())
    assert [r.route_id for r in routes] == ["identify_first"]
    guide = routes[0]
    assert guide.suitable_scenarios == () and guide.steps_involved == ()
    assert guide.ready is True                       # 无需求,不误挂黄牌
    assert "重新扫描" in guide.est_note              # 引导:整理目录后重扫
    assert "IMG-" in guide.est_note                  # 四类判型特征写给用户


def test_kind_missing_falls_back_to_unknown():
    """kind 字段缺失/超出闭集的脏输入按 unknown 引导处理,不抛错。"""
    assert recommend_routes({"detail": {}}, probe_bare())[0].route_id == "identify_first"
    weird = ds("hologram_cube")
    assert recommend_routes(weird, probe_bare())[0].route_id == "identify_first"


# ---------------- requirements 对照 probe 标注 ----------------

def test_requirements_marked_missing_on_bare_probe():
    direct = recommend_routes(DS_HYP3, probe_bare())[0]
    assert direct.route_id == "hyp3_direct"          # 缺引擎不改变知识表主推序
    assert direct.ready is False
    assert direct.missing == ("mintpy",)             # 必需项缺失才计入
    by_key = {r["key"]: r for r in direct.requirements}
    assert by_key["mintpy"]["ok"] is False
    assert by_key["pyaps"]["ok"] is False and by_key["pyaps"]["optional"] is True
    # 可选项缺失不进 missing(前端黄徽章只列硬阻塞)
    assert "pyaps" not in direct.missing


def test_credential_requirement_checked():
    routes = recommend_routes(DS_SLC, probe_full())
    cloud = next(r for r in routes if r.route_id == "hyp3_cloud")
    cred = next(r for r in cloud.requirements if r["type"] == "credential")
    assert cred["key"] == "earthdata" and cred["ok"] is True
    # 凭据拿掉后同一路线立即标缺
    no_cred = probe_full()
    no_cred.credentials["earthdata"] = False
    cloud2 = next(r for r in recommend_routes(DS_SLC, no_cred)
                  if r.route_id == "hyp3_cloud")
    assert cloud2.ready is False and "earthdata" in cloud2.missing


# ---------------- 排序:确定、稳定、缺失少者上浮 ----------------

def test_sort_deterministic_same_input_same_output():
    a = [r.to_dict() for r in recommend_routes(DS_SLC, probe_bare())]
    b = [r.to_dict() for r in recommend_routes(DS_SLC, probe_bare())]
    assert a == b


def test_sort_ready_route_floats_up():
    """只有 mintpy+earthdata 的机器:hyp3_cloud(0 缺)排到全链(缺 2)之前;
    同缺失数的 full_chain 与 ps_chain 保持知识表声明序。"""
    p = ProbeResult(engines={"mintpy": "1.5.3", "isce2": None, "snaphu": None,
                             "gdal": None, "pyaps": None, "pystamps": None},
                    credentials={"earthdata": True, "cds": False, "gacos": False})
    order = [r.route_id for r in recommend_routes(DS_SLC, p)]
    assert order == ["hyp3_cloud", "full_chain_s1", "ps_chain_s1"]


def test_full_probe_keeps_declared_order():
    """全就绪时缺失数全 0,排序退化为知识表声明序(主推在前)。"""
    order = [r.route_id for r in recommend_routes(DS_SLC, probe_full())]
    assert order == ["full_chain_s1", "ps_chain_s1", "hyp3_cloud"]


def test_est_note_never_promises_duration():
    """诚实纪律:est_note 不许出现「预计 X 分钟/小时」式的编造时长;
    唯一允许的时间数字是实测参照(33 min,Baja 全链)且必须带外推警示。"""
    import re
    for dataset in (DS_HYP3, DS_ALOS, DS_SLC, DS_DEM, DS_UNKNOWN):
        for r in recommend_routes(dataset, probe_full()):
            claims = re.findall(r"预计[^;,。]*[分小]时?", r.est_note)
            assert not claims, (r.route_id, r.est_note)
            if "min" in r.est_note:
                assert "实测" in r.est_note and "勿线性外推" in r.est_note


# ---------------- 路由(/api/recommend) ----------------

def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


GRANULE_A = "S1AA_20190704T135158_20190716T135159_VVP012_INT80_G_ueF_355F"


def make_home(tmp_path):
    """home/datasets 下造一个 HyP3 数据集与一个 unknown 目录。"""
    home = tmp_path / "home"
    d = home / "datasets" / "ridgecrest_hyp3"
    _touch(d / f"{GRANULE_A}_unw_phase.tif")
    _touch(d / f"{GRANULE_A}_corr.tif")
    _touch(home / "datasets" / "notes" / "readme.txt")
    return home


@pytest.fixture()
def api(tmp_path, monkeypatch):
    """挂推荐路由的最小 app:probe 注入固定桩,绝不触发真实探测。"""
    monkeypatch.delenv("INSAR_DATA_DIR", raising=False)
    home = make_home(tmp_path)
    app = FastAPI()
    app.include_router(create_recommend_router(home, probe=probe_full()))
    return TestClient(app), home


def test_api_recommend_shape(api):
    client, home = api
    hyp3_id = dataset_id(home / "datasets" / "ridgecrest_hyp3")
    r = client.get(f"/api/recommend?dataset_id={hyp3_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["dataset"]["id"] == hyp3_id and body["dataset"]["kind"] == "hyp3"
    assert [x["route_id"] for x in body["routes"]] == ["hyp3_direct", "slc_reacquire"]
    first = body["routes"][0]
    # RouteOption.to_dict 的字段闭集(前端消费契约)
    assert set(first) == {"route_id", "name", "suitable_scenarios", "pros", "cons",
                          "requirements", "steps_involved", "est_note", "ready",
                          "missing"}
    assert first["ready"] is True
    assert all({"key", "label", "type", "optional", "ok"} <= set(req)
               for req in first["requirements"])


def test_api_unknown_kind_and_404(api):
    client, home = api
    notes_id = dataset_id(home / "datasets" / "notes")
    body = client.get(f"/api/recommend?dataset_id={notes_id}").json()
    assert [x["route_id"] for x in body["routes"]] == ["identify_first"]
    # 未知 id:强制重扫一次仍未命中 → 404
    assert client.get("/api/recommend?dataset_id=ffffffffffff").status_code == 404
    assert client.get("/api/recommend").status_code == 422  # 缺参数(FastAPI 校验)


def test_api_sees_late_dataset_within_ttl(tmp_path, monkeypatch):
    """缓存窗口内刚落盘的数据集:404 前强制重扫一次,不误报不存在。"""
    monkeypatch.delenv("INSAR_DATA_DIR", raising=False)
    home = make_home(tmp_path)
    app = FastAPI()
    app.include_router(create_recommend_router(home, probe=probe_bare()))
    client = TestClient(app)
    client.get("/api/recommend?dataset_id=000000000000")  # 预热清单缓存
    late = home / "datasets" / "late_dem"
    _touch(late / "dem.wgs84")
    r = client.get(f"/api/recommend?dataset_id={dataset_id(late)}")
    assert r.status_code == 200
    assert r.json()["routes"][0]["route_id"] == "dem_only"


def test_create_app_mounts_recommend(tmp_path, monkeypatch):
    """app.py 的两行挂载接通主应用(集成冒烟;探测打桩,不碰真实环境)。"""
    monkeypatch.delenv("INSAR_DATA_DIR", raising=False)
    import insar_agent.api.recommend_router as rr
    monkeypatch.setattr(rr, "_live_probe", lambda home: probe_full())
    from insar_agent.api.app import create_app

    home = tmp_path / "apphome"
    d = home / "datasets" / "dem_tiles"
    _touch(d / "srtm.dem")
    app = create_app(home=home)
    client = TestClient(app)
    r = client.get(f"/api/recommend?dataset_id={dataset_id(d)}")
    assert r.status_code == 200
    assert r.json()["routes"][0]["route_id"] == "dem_only"
    assert client.get("/api/recommend?dataset_id=ffffffffffff").status_code == 404
