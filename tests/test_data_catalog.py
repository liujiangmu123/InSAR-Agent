# -*- coding: utf-8 -*-
"""数据集识别器与 /api/datasets* 路由契约(data/catalog.py + data_catalog_router.py)。

覆盖:
  - 识别器八种类型(tmp_path 造假数据集:识别只看文件名模式与 stat 元数据,
    空文件即可,绝不读内容):hyp3(产品对数/日期范围)、alos_raw(IMG/LED
    成对、优先级压过辅助 DEM、轨道号不误判为日期)、slc_stack(.slc 文件与
    .SAFE 目录)、dem、nisar(GUNW h5)、gamma(.par+.diff 不被 slc_stack 抢走)、
    displacement(EGMS tif/csv)、unknown;hyp3 的 *_unw_phase*.tif 仍是 hyp3
    不是 displacement;
  - 扫描上限截断(max_entries 注入小值,不真造 5000 个文件);
  - 根目录本身的浅判(INSAR_DATA_DIR 直接指向产品目录的形态);
  - 路由:60s TTL 缓存(命中不重扫 / rescan=1 穿透 / ttl=0 过期即重扫)、
    POST roots 的路径校验(相对路径 / .. 穿越 / 不存在一律 400)与持久化去重、
    GET {id} 详情的文件清单上限 200 与未知 id 404;
  - create_app 集成:app.py 的两行挂载真的把路由接进主应用。
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from insar_agent.api.data_catalog_router import create_data_catalog_router
from insar_agent.data.catalog import (
    KINDS,
    dataset_id,
    identify_dataset,
    list_files,
    scan_roots,
)

# 真实数据形态的文件名样本(完整InSAR开发测试数据说明.md / localdata.py 口径)
GRANULE_A = "S1AA_20190704T135158_20190716T135159_VVP012_INT80_G_ueF_355F"
GRANULE_B = "S1BB_20190716T135159_20190728T135200_VVP012_INT80_G_ueF_1C2D"
SAFE_NAME = "S1A_IW_SLC__1SDV_20200604T022252_20200604T022319_032861_03CE65_7C85.SAFE"


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def make_hyp3(root):
    """HyP3 产品栈:两个产品对目录,含 unw/corr 与 clipped 变体。"""
    d = root / "ridgecrest_hyp3"
    _touch(d / GRANULE_A / f"{GRANULE_A}_unw_phase.tif")
    _touch(d / GRANULE_A / f"{GRANULE_A}_unw_phase_clipped.tif")
    _touch(d / GRANULE_A / f"{GRANULE_A}_corr.tif")
    _touch(d / GRANULE_B / f"{GRANULE_B}_unw_phase.tif")
    _touch(d / GRANULE_B / f"{GRANULE_B}_corr.tif")
    return d


def make_alos(root):
    """GMTSAR ALOS Baja 形态:raw/IMG+LED 两景成对 + topo/dem.grd 辅助。"""
    d = root / "gmtsar_alos_baja_eq"
    for scene in ("ALPSRP207600640-H1.0__A", "ALPSRP227730640-H1.0__A"):
        _touch(d / "raw" / f"IMG-HH-{scene}")
        _touch(d / "raw" / f"LED-{scene}")
    _touch(d / "topo" / "dem.grd")
    return d


def make_slc(root):
    d = root / "slc_stack_demo"
    _touch(d / "20200604.slc")
    _touch(d / "20200616.slc")
    (d / SAFE_NAME).mkdir(parents=True)
    return d


def make_dem(root):
    d = root / "dem_tiles"
    _touch(d / "srtm_n35.dem")
    _touch(d / "dem.wgs84")
    _touch(d / "gebco.grd")
    return d


def make_unknown(root):
    d = root / "notes"
    _touch(d / "readme.txt")
    return d


@pytest.fixture()
def data_root(tmp_path):
    """五种假数据集共用一个根。"""
    root = tmp_path / "data"
    make_hyp3(root)
    make_alos(root)
    make_slc(root)
    make_dem(root)
    make_unknown(root)
    return root


# ---------------- 识别器 ----------------

def test_kinds_closed_set():
    assert KINDS == ("hyp3", "alos_raw", "slc_stack", "dem", "nisar", "gamma",
                     "displacement", "unknown")


def test_identify_hyp3(data_root):
    info = identify_dataset(data_root / "ridgecrest_hyp3")
    assert info["kind"] == "hyp3"
    # clipped 变体与 corr 同属一个 granule:两对,不是五对
    assert info["detail"]["pairs"] == 2
    assert info["detail"]["unw"] == 3 and info["detail"]["corr"] == 2
    assert info["date_range"] == {"start": "2019-07-04", "end": "2019-07-28"}
    assert info["file_count"] == 5
    assert info["detail"]["truncated"] is False


def test_identify_alos_raw_beats_dem(data_root):
    """IMG/LED 成对 → alos_raw;同目录 topo/dem.grd 是辅助,不改判型。"""
    info = identify_dataset(data_root / "gmtsar_alos_baja_eq")
    assert info["kind"] == "alos_raw"
    assert info["detail"] == {"scenes": 2, "img": 2, "led": 2, "truncated": False}
    # ALOS 场景名里的轨道号(…20760064…)不得误判为日期
    assert info["date_range"] is None


def test_identify_slc_stack(data_root):
    info = identify_dataset(data_root / "slc_stack_demo")
    assert info["kind"] == "slc_stack"
    assert info["detail"]["slc"] == 2 and info["detail"]["safe"] == 1
    # .slc 名与 SAFE 名的日期一起进范围
    assert info["date_range"] == {"start": "2020-06-04", "end": "2020-06-16"}


def test_identify_dem(data_root):
    info = identify_dataset(data_root / "dem_tiles")
    assert info["kind"] == "dem"
    assert info["detail"]["dem_files"] == ["dem.wgs84", "gebco.grd", "srtm_n35.dem"]
    assert info["date_range"] is None


def test_identify_unknown(data_root):
    info = identify_dataset(data_root / "notes")
    assert info["kind"] == "unknown"
    assert info["file_count"] == 1


def test_identify_nisar(tmp_path):
    d = tmp_path / "nisar_gunw"
    _touch(d / "NISAR_L_GUNW_20260720.h5")
    info = identify_dataset(d)
    assert info["kind"] == "nisar"
    assert info["detail"]["gunw"] == 1
    assert info["detail"]["h5"] == 1
    assert info["date_range"] == {"start": "2026-07-20", "end": "2026-07-20"}


def test_identify_gamma_not_slc_stack(tmp_path):
    """YYYYMMDD.slc.par + .diff → gamma;同目录再放 .slc 也不被 slc_stack 抢走。"""
    d = tmp_path / "lt1_gamma"
    _touch(d / "20200604.slc.par")
    _touch(d / "20200604.diff")
    info = identify_dataset(d)
    assert info["kind"] == "gamma"
    assert info["detail"]["par"] == 1 and info["detail"]["diff"] == 1
    _touch(d / "20200604.slc")
    assert identify_dataset(d)["kind"] == "gamma"


def test_identify_displacement_tif(tmp_path):
    d = tmp_path / "egms_tif"
    _touch(d / "EGMS_L2b_velocity.tif")
    info = identify_dataset(d)
    assert info["kind"] == "displacement"
    assert info["detail"]["tif"] == 1 and info["detail"]["csv"] == 0


def test_identify_displacement_csv(tmp_path):
    d = tmp_path / "egms_csv"
    _touch(d / "egms_points.csv")
    info = identify_dataset(d)
    assert info["kind"] == "displacement"
    assert info["detail"]["tif"] == 0 and info["detail"]["csv"] == 1


def test_hyp3_unw_phase_is_not_displacement(tmp_path):
    """hyp3 优先: *_unw_phase*.tif 仍是 hyp3,不被 displacement 的 tif 规则吃掉。"""
    d = tmp_path / "one_pair"
    _touch(d / f"{GRANULE_A}_unw_phase.tif")
    assert identify_dataset(d)["kind"] == "hyp3"


def test_result_shape_and_id(data_root):
    """结果字段闭集与 id 稳定性(路径哈希:同路径同 id,不同路径不同 id)。"""
    info = identify_dataset(data_root / "notes")
    assert set(info) == {"id", "path", "name", "kind", "size_bytes",
                         "file_count", "date_range", "detail"}
    assert info["id"] == dataset_id(data_root / "notes")
    assert len(info["id"]) == 12
    assert info["id"] != dataset_id(data_root / "dem_tiles")
    assert info["kind"] in KINDS


def test_scan_cap_truncates(tmp_path):
    """上限截断:max_entries=10 时 25 个文件只见 10 个,truncated 如实标注。"""
    d = tmp_path / "big"
    for i in range(25):
        _touch(d / f"f{i:03d}.bin")
    info = identify_dataset(d, max_entries=10)
    assert info["file_count"] == 10
    assert info["detail"]["truncated"] is True


def test_list_files_limit(tmp_path):
    d = tmp_path / "many"
    for i in range(12):
        _touch(d / f"f{i:02d}.txt")
    files, more = list_files(d, limit=5)
    assert [f["path"] for f in files] == [f"f{i:02d}.txt" for i in range(5)]
    assert more is True
    files_all, more_all = list_files(d, limit=200)
    assert len(files_all) == 12 and more_all is False
    assert set(files_all[0]) == {"path", "name", "size", "mtime"}


def test_scan_roots_lists_subdirs(data_root):
    out = scan_roots([data_root])
    kinds = {d["name"]: d["kind"] for d in out}
    assert kinds == {
        "ridgecrest_hyp3": "hyp3",
        "gmtsar_alos_baja_eq": "alos_raw",
        "slc_stack_demo": "slc_stack",
        "dem_tiles": "dem",
        "notes": "unknown",
    }


def test_scan_roots_self_when_root_is_product_dir(tmp_path):
    """INSAR_DATA_DIR 直接指向单个产品目录:根本身按第 1 层信号列出。"""
    d = tmp_path / "one_pair"
    _touch(d / f"{GRANULE_A}_unw_phase.tif")
    out = scan_roots([d])
    assert [x["kind"] for x in out] == ["hyp3"]
    assert out[0]["path"] == str(d)


def test_scan_roots_skips_missing_root(tmp_path, data_root):
    out = scan_roots([tmp_path / "不存在", data_root])
    assert len(out) == 5


# ---------------- 路由 ----------------

@pytest.fixture()
def api(tmp_path, data_root, monkeypatch):
    """挂数据集路由的最小 app:INSAR_DATA_DIR 指向假数据根,home 独立目录。"""
    monkeypatch.setenv("INSAR_DATA_DIR", str(data_root))
    home = tmp_path / "home"
    home.mkdir()
    app = FastAPI()
    app.include_router(create_data_catalog_router(home))
    return TestClient(app), home


def test_get_datasets(api):
    client, _home = api
    r = client.get("/api/datasets")
    assert r.status_code == 200
    body = r.json()
    assert body["cached"] is False
    assert len(body["roots"]) == 1
    found = {d["kind"] for d in body["datasets"]}
    assert found == {"hyp3", "alos_raw", "slc_stack", "dem", "unknown"}
    assert found <= set(KINDS)


def test_ttl_cache_and_rescan(api, data_root):
    client, _home = api
    assert client.get("/api/datasets").json()["cached"] is False
    # 缓存窗口内新落盘的数据集:普通 GET 看不到(cached),rescan=1 立即可见
    make_hyp3(data_root / "late")
    cached = client.get("/api/datasets").json()
    assert cached["cached"] is True
    assert all(d["name"] != "ridgecrest_hyp3" or "late" not in d["path"]
               for d in cached["datasets"])
    fresh = client.get("/api/datasets?rescan=1").json()
    assert fresh["cached"] is False
    assert any("late" in d["path"] for d in fresh["datasets"])


def test_ttl_zero_expires(tmp_path, data_root, monkeypatch):
    """ttl_seconds=0(测试缝):每次 GET 都重扫,不需要 rescan=1。"""
    monkeypatch.setenv("INSAR_DATA_DIR", str(data_root))
    home = tmp_path / "home0"
    home.mkdir()
    app = FastAPI()
    app.include_router(create_data_catalog_router(home, ttl_seconds=0))
    client = TestClient(app)
    assert client.get("/api/datasets").json()["cached"] is False
    make_dem(data_root / "late2")
    body = client.get("/api/datasets").json()
    assert body["cached"] is False
    assert any("late2" in d["path"] for d in body["datasets"])


def test_workspace_datasets_root(tmp_path, monkeypatch):
    """<home>/datasets 存在时自动成为扫描根(INSAR_DATA_DIR 缺席也能用)。"""
    monkeypatch.delenv("INSAR_DATA_DIR", raising=False)
    home = tmp_path / "homews"
    make_dem(home / "datasets")
    app = FastAPI()
    app.include_router(create_data_catalog_router(home))
    body = TestClient(app).get("/api/datasets").json()
    assert body["roots"] == [str(home / "datasets")]
    assert [d["kind"] for d in body["datasets"]] == ["dem"]


@pytest.mark.parametrize("bad,why", [
    ("datasets", "相对路径"),
    ("../escape", "相对路径"),
    ("", "空路径"),
])
def test_add_root_rejects_relative(api, bad, why):
    client, _home = api
    r = client.post("/api/datasets/roots", json={"path": bad})
    assert r.status_code == 400, why


def test_add_root_rejects_traversal_and_missing(api, tmp_path):
    client, _home = api
    # 绝对路径但带 .. 段:拒绝穿越
    sneaky = str(tmp_path) + ("\\..\\x" if "\\" in str(tmp_path) else "/../x")
    r = client.post("/api/datasets/roots", json={"path": sneaky})
    assert r.status_code == 400
    assert "穿越" in r.json()["detail"]
    # 存在性:不存在的绝对路径同样 400
    r2 = client.post("/api/datasets/roots", json={"path": str(tmp_path / "无此目录")})
    assert r2.status_code == 400


def test_add_root_persists_and_dedupes(api, tmp_path):
    client, home = api
    extra = tmp_path / "extra_root"
    make_slc(extra)
    r = client.post("/api/datasets/roots", json={"path": str(extra)})
    assert r.status_code == 200
    saved = json.loads((home / "datasets_roots.json").read_text(encoding="utf-8"))
    assert saved == [str(extra.resolve())]
    # 重复添加不产生重复项
    client.post("/api/datasets/roots", json={"path": str(extra)})
    saved2 = json.loads((home / "datasets_roots.json").read_text(encoding="utf-8"))
    assert saved2 == saved
    # 新根立即生效(POST 后缓存失效,不等 TTL)
    body = client.get("/api/datasets").json()
    assert str(extra.resolve()) in body["roots"]
    assert any(d["kind"] == "slc_stack" and "extra_root" in d["path"]
               for d in body["datasets"])


def test_dataset_detail_and_404(api, data_root):
    client, _home = api
    listing = client.get("/api/datasets").json()["datasets"]
    hyp3 = next(d for d in listing if d["kind"] == "hyp3")
    r = client.get(f"/api/datasets/{hyp3['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "hyp3" and body["id"] == hyp3["id"]
    assert body["files_truncated"] is False
    assert len(body["files"]) == 5
    assert all(set(f) == {"path", "name", "size", "mtime"} for f in body["files"])
    assert client.get("/api/datasets/ffffffffffff").status_code == 404


def test_dataset_detail_files_capped_at_200(tmp_path, monkeypatch):
    root = tmp_path / "cap_root"
    d = root / "many"
    for i in range(205):
        _touch(d / f"f{i:03d}.txt")
    monkeypatch.setenv("INSAR_DATA_DIR", str(root))
    home = tmp_path / "home_cap"
    home.mkdir()
    app = FastAPI()
    app.include_router(create_data_catalog_router(home))
    client = TestClient(app)
    ds = client.get("/api/datasets").json()["datasets"][0]
    body = client.get(f"/api/datasets/{ds['id']}").json()
    assert len(body["files"]) == 200
    assert body["files_truncated"] is True


def test_create_app_mounts_router(tmp_path, monkeypatch):
    """app.py 的两行挂载真的接通主应用(集成冒烟,不起网络端口)。"""
    monkeypatch.delenv("INSAR_DATA_DIR", raising=False)
    from insar_agent.api.app import create_app

    app = create_app(home=tmp_path / "apphome")
    r = TestClient(app).get("/api/datasets")
    assert r.status_code == 200
    assert r.json()["datasets"] == []
