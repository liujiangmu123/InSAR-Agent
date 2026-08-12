# -*- coding: utf-8 -*-
"""点位时序 API(GET /api/timeseries-point)契约与安全边界。

覆盖:
  - lat/lon → row/col 换算正确(MintPy 地理属性 X_FIRST/Y_STEP 等,
    含最近像元取整),值单位统一为 mm(UNIT=m → ×1000);
  - row/col 备选参数直给;无地理参考的产物 lat/lon → 400 并提示改用 row/col;
  - 变体优先:timeseries_demErr.h5(校正)优先于 timeseries.h5(原始);
  - 全 NaN 像元 → 404 结构化说明(携带 extent/shape 供前端探测);
    单历元 NaN 只丢该历元,dates/values 保持配对;
  - 占位文件(模拟运行,非 HDF5)/ 无时序产物 → 404「需要真实时序产物」;
  - 跨会话取他人 run → 404(与 resolve_run 口径一致);
  - 大 h5 不整块读:实现锁定为 h5py 单像元列切片 ds[:, r, c]
    (hyperslab 只命中该像元所在 chunk,不载入整个立方体)。

合成数据:KB 级 5 历元 × 6 行 × 8 列 float32 立方体,已知线性形变像元。
"""

from __future__ import annotations

import re
from pathlib import Path

import h5py
import numpy as np
import pytest
from fastapi.testclient import TestClient

from insar_agent.api.app import create_app
from insar_agent.core.db import Database
from insar_agent.core.store import Store

DATES = ["20190610", "20190622", "20190704", "20190716", "20190728"]
RUN_A = "20260812T000000-tsreal"       # sess-a:真实合成 h5(原始 + 校正变体)
RUN_B = "20260812T000001-tsfake"       # sess-b:模拟占位文件(非 HDF5)
RUN_C = "20260812T000002-tsnone"       # sess-c:没有任何时序产物
RUN_D = "20260812T000003-tsradar"      # sess-d:真实 h5 但无地理参考属性

# 目标像元 (row=2, col=3):已知线性形变 5 mm/历元(存 m,端点换算 mm)
TARGET_M = [0.0, 0.005, 0.010, 0.015, 0.020]


def _cube(scale: float = 1.0) -> np.ndarray:
    """合成立方体:全零背景 + 已知形变像元 + 全 NaN 像元 + 单历元 NaN 像元。"""
    cube = np.zeros((len(DATES), 6, 8), dtype="float32")
    cube[:, 2, 3] = np.array(TARGET_M, dtype="float32") * scale
    cube[:, 1, 1] = np.nan                                  # 全 NaN:无效像元
    cube[:, 0, 0] = np.array([0.0, np.nan, 0.002, 0.003, 0.004],
                             dtype="float32")               # 单历元 NaN
    return cube


def _write_ts_h5(path: Path, *, scale: float = 1.0, with_geo: bool = True) -> None:
    """按 MintPy 布局写合成时序 h5(属性一律字符串,与 MintPy 落盘一致)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    cube = _cube(scale)
    with h5py.File(path, "w") as f:
        # chunks 取小块:端点的单像元列读取只应命中目标像元所在 chunk
        f.create_dataset("timeseries", data=cube, chunks=(len(DATES), 2, 2))
        f.create_dataset("date", data=np.array([d.encode() for d in DATES]))
        f.create_dataset("bperp", data=np.zeros(len(DATES), dtype="float32"))
        f.attrs["LENGTH"] = str(cube.shape[1])
        f.attrs["WIDTH"] = str(cube.shape[2])
        f.attrs["UNIT"] = "m"
        if with_geo:
            f.attrs["X_FIRST"] = "-118.0"
            f.attrs["X_STEP"] = "0.1"
            f.attrs["Y_FIRST"] = "36.0"
            f.attrs["Y_STEP"] = "-0.1"
            f.attrs["REF_LAT"] = "35.9"
            f.attrs["REF_LON"] = "-117.9"


@pytest.fixture()
def env(tmp_path):
    """四个会话各一个 run:真实 h5 / 占位文件 / 无产物 / 无地理参考。"""
    home = tmp_path / "home"
    app = create_app(home=home)
    with TestClient(app) as client:
        for sid in ("sess-a", "sess-b", "sess-c", "sess-d"):
            client.post("/api/sessions", json={"id": sid})
        store = Store(Database(home / "insar.db"))

        ws_a = home / "sessions" / "sess-a"
        store.create_run(RUN_A, "sess-a", workspace=str(ws_a))
        _write_ts_h5(ws_a / "mintpy" / "timeseries.h5", scale=1.0)
        _write_ts_h5(ws_a / "mintpy" / "timeseries_demErr.h5", scale=2.0)

        ws_b = home / "sessions" / "sess-b"
        store.create_run(RUN_B, "sess-b", workspace=str(ws_b))
        fake = ws_b / "mintpy" / "timeseries.h5"
        fake.parent.mkdir(parents=True, exist_ok=True)
        fake.write_bytes(b"SIMULATED placeholder, not an HDF5 file")

        ws_c = home / "sessions" / "sess-c"
        store.create_run(RUN_C, "sess-c", workspace=str(ws_c))

        ws_d = home / "sessions" / "sess-d"
        store.create_run(RUN_D, "sess-d", workspace=str(ws_d))
        _write_ts_h5(ws_d / "mintpy" / "timeseries.h5", with_geo=False)

        yield {"client": client, "home": home}
        store.close()


def _get(env, session, **params):
    return env["client"].get("/api/timeseries-point",
                             params={"session": session, **params})


# ---------------- 正常路径:换算 / 单位 / 元数据 ----------------

def test_latlon_selects_expected_pixel_mm_and_metadata(env):
    """像元中心坐标 (35.8, -117.7) → (row=2, col=3);值 = m × 1000。"""
    r = _get(env, "sess-a", run_id=RUN_A, lat=35.8, lon=-117.7)
    assert r.status_code == 200
    data = r.json()
    assert data["dates"] == DATES
    # 优先变体 scale=2.0:0/10/20/30/40 mm(float32 舍入容差)
    assert data["values_mm"] == pytest.approx([0, 10, 20, 30, 40], abs=1e-2)
    assert data["source"] == "timeseries_demErr.h5"
    assert data["point"]["row"] == 2 and data["point"]["col"] == 3
    assert data["point"]["lat"] == pytest.approx(35.8)
    assert data["point"]["lon"] == pytest.approx(-117.7)
    assert data["shape"] == {"rows": 6, "cols": 8}
    # extent 为像素边缘范围:中心 ±半像素(X_FIRST 按第 0 列像元中心解释)
    assert data["extent"] == pytest.approx(
        {"lon_min": -118.05, "lon_max": -117.25, "lat_min": 35.45, "lat_max": 36.05})
    assert data["n_dropped"] == 0


def test_nearest_pixel_rounding(env):
    """偏离像元中心的坐标按最近像元取整,与像元中心请求同像元同值。"""
    r = _get(env, "sess-a", lat=35.83, lon=-117.74)   # run_id 缺省 → 最近 run
    assert r.status_code == 200
    data = r.json()
    assert data["point"]["row"] == 2 and data["point"]["col"] == 3
    assert data["values_mm"] == pytest.approx([0, 10, 20, 30, 40], abs=1e-2)


def test_rowcol_direct_access(env):
    """row/col 备选参数直给同一像元:与 lat/lon 路径结果一致。"""
    r = _get(env, "sess-a", run_id=RUN_A, row=2, col=3)
    assert r.status_code == 200
    data = r.json()
    assert data["values_mm"] == pytest.approx([0, 10, 20, 30, 40], abs=1e-2)
    assert data["point"]["lat"] == pytest.approx(35.8)


def test_ref_point_from_attrs(env):
    r = _get(env, "sess-a", lat=35.8, lon=-117.7)
    assert r.json()["ref_point"] == pytest.approx({"lat": 35.9, "lon": -117.9})


def test_prefers_corrected_variant(env):
    """timeseries_demErr.h5(scale=2)优先于 timeseries.h5(scale=1):
    值翻倍且 source 指向校正变体,证明不是读的原始文件。"""
    data = _get(env, "sess-a", row=2, col=3).json()
    assert data["source"] == "timeseries_demErr.h5"
    assert data["values_mm"][1] == pytest.approx(10, abs=1e-2)   # 原始文件是 5


# ---------------- NaN 语义 ----------------

def test_nan_pixel_404_structured_with_grid_meta(env):
    """全 NaN 像元 → 404 结构化 detail,且携带 extent/shape
    (前端探测模式打在无效像元上也能学到坐标换算参数)。"""
    r = _get(env, "sess-a", lat=35.9, lon=-117.9)   # (row=1, col=1) 全 NaN
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail["error"] == "pixel_invalid"
    assert "无有效数据" in detail["message"]
    assert detail["shape"] == {"rows": 6, "cols": 8}
    assert detail["extent"]["lon_min"] == pytest.approx(-118.05)
    assert detail["point"]["row"] == 1 and detail["point"]["col"] == 1


def test_partial_nan_epochs_dropped(env):
    """单历元 NaN 只丢该历元:dates 与 values_mm 保持配对。"""
    data = _get(env, "sess-a", row=0, col=0).json()
    assert data["dates"] == ["20190610", "20190704", "20190716", "20190728"]
    # (0,0) 不乘 scale,两个变体同值:0/2/3/4 mm
    assert data["values_mm"] == pytest.approx([0, 2, 3, 4], abs=1e-2)
    assert data["n_dropped"] == 1


# ---------------- 无真实产物 / 占位文件 ----------------

def test_placeholder_file_404(env):
    """模拟运行的占位文件(非 HDF5)→ 404 且说明需要真实时序产物。"""
    r = _get(env, "sess-b", row=0, col=0)
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail["error"] == "placeholder"
    assert "需要真实时序产物" in detail["message"]
    assert detail["files"] == ["timeseries.h5"]


def test_missing_h5_404(env):
    """工作区没有任何 timeseries*.h5 → 404 且说明需要真实时序产物。"""
    r = _get(env, "sess-c", lat=35.8, lon=-117.7)
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail["error"] == "no_timeseries"
    assert "需要真实时序产物" in detail["message"]


# ---------------- 安全边界 ----------------

def test_cross_session_404(env):
    """sess-b 借 run_id 取 sess-a 的时序:按「不存在」处理,不泄露归属。"""
    r = _get(env, "sess-b", run_id=RUN_A, lat=35.8, lon=-117.7)
    assert r.status_code == 404


def test_no_run_404(env):
    r = _get(env, "sess-nothing", lat=35.8, lon=-117.7)
    assert r.status_code == 404


def test_out_of_coverage_404(env):
    r = _get(env, "sess-a", lat=40.0, lon=-117.7)
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail["error"] == "out_of_coverage"
    assert detail["extent"]["lat_max"] == pytest.approx(36.05)


def test_edge_click_no_dead_zone(env):
    """extent 边缘点击(边缘像元的外半格)命中边缘像元,而非假越界 404
    (回归:边缘语义与最近像元取整必须配对,否则地图最右/最下
    半像素宽的条带永远点不中)。"""
    r = _get(env, "sess-a", lat=35.45, lon=-118.05)
    assert r.status_code == 200
    p = r.json()["point"]
    assert (p["row"], p["col"]) == (5, 0)


def test_missing_params_400(env):
    """lat/lon 与 row/col 都不给 → 400(参数缺失,而非 404)。"""
    r = _get(env, "sess-a")
    assert r.status_code == 400
    assert "lat/lon" in r.json()["detail"]


def test_nonfinite_latlon_not_500(env):
    """lat=NaN/inf 之类的非有限数值必须被边界挡下(400 或 422),
    不得在 round(nan) 处逃逸为 500(fuzz 口径)。"""
    for bad in ("nan", "inf", "-inf"):
        r = _get(env, "sess-a", lat=bad, lon="-117.7")
        assert r.status_code in (400, 422), f"lat={bad} 逃逸为 {r.status_code}"


def test_no_geo_fallback_rowcol(env):
    """无地理参考属性(radar 坐标):lat/lon → 400 提示改用 row/col;
    row/col 直给正常返回(extent/point.lat 为 None)。"""
    r = _get(env, "sess-d", lat=35.8, lon=-117.7)
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "no_geo"

    r2 = _get(env, "sess-d", row=2, col=3)
    assert r2.status_code == 200
    data = r2.json()
    assert data["values_mm"] == pytest.approx([0, 5, 10, 15, 20], abs=1e-2)
    assert data["extent"] is None
    assert data["point"]["lat"] is None


# ---------------- 惰性读取实现锁定 ----------------

def test_single_pixel_column_read_locked(env):
    """大 h5 不整块读:实现必须是 h5py 单像元列切片 ds[:, r, c]
    (hyperslab 选择只读命中 chunk;此断言锁定实现方式,防止回退成
    f["timeseries"][()] 整块载入后再索引)。"""
    import insar_agent.api.data_router as dr
    src = Path(dr.__file__).read_text(encoding="utf-8")
    assert re.search(r"ds\[\s*:\s*,\s*r\s*,\s*c\s*\]", src), \
        "端点必须用单像元列切片 ds[:, r, c] 惰性读取"
    assert "[()]" not in src.replace('f["date"][()]', ""), \
        "除 date 小数组外不得整块物化数据集"
