"""数据产品导出契约(0814B §2:/api/export/options + /api/export)。

覆盖(离线密封:引擎探测与子进程全部打桩,微型 h5 夹具自造):
  ① options 能力矩阵形状:产品×格式全集、不可用必给原因、source 相对路径;
  ② csv 内容正确性:像元中心坐标计算、NaN 行剔除、列名按 EPSG 诚实分派
     (lon,lat / UTM x,y)、velocityStd 数据集、时序宽表、未地理编码 409、
     点数防线 400;
  ③ h5 直传与路径防御:字节等同源文件、product/fmt 闭集 400、跨会话 404
     不泄磁盘路径;
  ④ 模拟 run(runs.simulated=1)导出一律 409;
  ⑤ 引擎缺失 gtiff → 501(engine_status 真函数的安装指引单测另列);
  ⑥ 引擎子进程:monkeypatch _run_engine,断言 save_gdal/save_kmz/save_qgis
     命令组装正确、失败 stderr 原样透传 502、成功但无产物 502、超时 504、
     shp 打包 zip;
  ⑦ 同参幂等复用(X-Export-Reused,源文件更新后重算)。

路由挂接:本单元不改 api/app.py,测试用独立 FastAPI 挂载
(create_export_router(store, home),artifacts_router B10 先例)。
密封纪律:env 夹具默认把 engine_status 钉为「引擎缺席」、_run_engine 钉为
禁止真跑 —— 本机装有真实 conda 引擎环境,不打桩会让用例在开发机/CI 上
表现分叉;需要引擎在场的用例自行重打桩。
"""

from __future__ import annotations

import os
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import h5py
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from insar_agent.api.export_router import create_export_router
from insar_agent.core.db import Database
from insar_agent.core.store import Store
from insar_agent.report import export as export_mod
from insar_agent.report.export import FORMATS

RUN_ID = "20260814T000000-exptest"
SIM_RUN_ID = "20260814T000001-simexp"
EMPTY_RUN_ID = "20260814T000002-empty"
UTM_RUN_ID = "20260814T000003-utm"
RADAR_RUN_ID = "20260814T000004-radar"

#: MintPy 风格地理编码属性(字符串落盘,与真实 h5 一致)
_GEO_ATTRS = {"X_FIRST": "100.0", "Y_FIRST": "35.0",
              "X_STEP": "0.05", "Y_STEP": "-0.05"}

#: 密封桩:引擎缺席的诚实原因(断言 501 detail 原样透传用)
_ENGINE_OFF = "MintPy 引擎不可用(测试密封桩)。安装指引 —— mintpy:conda 途径见文档"


def _forbid_subprocess(argv, cwd, timeout):
    raise AssertionError("测试密封:不得真的拉起引擎子进程")


def _write_velocity_h5(path: Path, *, attrs: dict | None = None) -> None:
    """10×10 velocity + velocityStd,(0,0)/(3,4) 两个 NaN 像元。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    vel = (np.arange(100, dtype=np.float32) * 0.001).reshape(10, 10)
    vel[0, 0] = np.nan
    vel[3, 4] = np.nan
    std = vel * 0.1 + 0.0001  # NaN 传染:同两个像元无效
    with h5py.File(path, "w") as f:
        f.create_dataset("velocity", data=vel)
        f.create_dataset("velocityStd", data=std)
        base = {"FILE_TYPE": "velocity", "UNIT": "m/year"}
        for k, v in {**base, **(_GEO_ATTRS if attrs is None else attrs)}.items():
            f.attrs[k] = v


TS_DATES = ("20200101", "20200113", "20200125")


def _write_timeseries_h5(path: Path) -> None:
    """3 期 × 4×4:像元(0,0) 全 NaN(须整行剔除),(1,1) 第 0 期 NaN(留空串)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    cube = np.zeros((3, 4, 4), dtype=np.float32)
    for i in range(3):
        for r in range(4):
            for c in range(4):
                cube[i, r, c] = i * 100 + r * 10 + c
    cube[:, 0, 0] = np.nan
    cube[0, 1, 1] = np.nan
    with h5py.File(path, "w") as f:
        f.create_dataset("timeseries", data=cube)
        f.create_dataset("date", data=np.array([d.encode() for d in TS_DATES]))
        for k, v in {"FILE_TYPE": "timeseries", "UNIT": "m", **_GEO_ATTRS}.items():
            f.attrs[k] = v


def _mk_run(store: Store, root: Path, session: str, run_id: str,
            *, simulated: bool = False) -> Path:
    ws = root / "sessions" / session
    ws.mkdir(parents=True, exist_ok=True)
    store.create_session(session, session)  # INSERT OR IGNORE,幂等
    store.create_run(run_id, session, workspace=str(ws), simulated=simulated)
    return ws


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """会话×run 矩阵 + 微型 h5 夹具;app 只挂导出路由(独立 FastAPI)。

    默认密封:引擎缺席 + 禁真子进程(见模块头);需要引擎的用例自行重打桩。
    """
    store = Store(Database(tmp_path / "insar.db"))
    ws = _mk_run(store, tmp_path, "sess-a", RUN_ID)
    _write_velocity_h5(ws / "mintpy" / "velocity.h5")
    _write_timeseries_h5(ws / "mintpy" / "timeseries.h5")
    # 几何文件(shp 可用性判据只看在场):微型有效 h5
    geom = ws / "mintpy" / "inputs" / "geometryGeo.h5"
    geom.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(geom, "w") as f:
        f.create_dataset("height", data=np.zeros((4, 4), dtype=np.float32))
    # 同会话的模拟 run(共享工作区:文件在场也必须 409)
    store.create_run(SIM_RUN_ID, "sess-a", workspace=str(ws), simulated=True)
    # 空工作区 run(产物缺失路径)/ UTM run / 未地理编码 run / 无 run 会话
    _mk_run(store, tmp_path, "sess-empty", EMPTY_RUN_ID)
    utm_ws = _mk_run(store, tmp_path, "sess-utm", UTM_RUN_ID)
    _write_velocity_h5(utm_ws / "mintpy" / "velocity.h5",
                       attrs={**_GEO_ATTRS, "X_FIRST": "500000.0", "Y_FIRST": "3900000.0",
                              "X_STEP": "80.0", "Y_STEP": "-80.0",
                              "EPSG": "32611", "UTM_ZONE": "11N"})
    radar_ws = _mk_run(store, tmp_path, "sess-radar", RADAR_RUN_ID)
    _write_velocity_h5(radar_ws / "mintpy" / "velocity.h5",
                       attrs={"FILE_TYPE": "velocity"})  # 无 X_FIRST 系属性
    store.create_session("sess-b", "sess-b")

    monkeypatch.setattr(export_mod, "engine_status", lambda: (False, _ENGINE_OFF))
    monkeypatch.setattr(export_mod, "_run_engine", _forbid_subprocess)

    app = FastAPI()
    app.include_router(create_export_router(store, tmp_path))
    with TestClient(app) as client:
        yield {"client": client, "store": store, "ws": ws, "tmp": tmp_path}
    store.close()


def _options(env, **params):
    return env["client"].get("/api/export/options", params=params)


def _export(env, session="sess-a", **params):
    return env["client"].get("/api/export", params={"session": session, **params})


def _product(data: dict, key: str) -> dict:
    return next(p for p in data["products"] if p["product"] == key)


# ---------------- ① options 能力矩阵 ----------------

def test_options_matrix_shape(env):
    """矩阵形状钉死:产品×格式全集,不可用必给原因,source 只回相对路径。"""
    r = _options(env, session="sess-a", run_id=RUN_ID)
    assert r.status_code == 200
    data = r.json()
    assert data["run"] == RUN_ID and data["simulated"] is False
    assert set(data) == {"run", "simulated", "engine", "products"}
    assert [p["product"] for p in data["products"]] == \
        ["velocity", "velocity_std", "timeseries"]
    assert set(FORMATS) == {"h5", "csv", "xlsx", "gtiff", "kmz", "shp"}
    for p in data["products"]:
        assert set(p) == {"product", "source", "formats"}
        assert set(p["formats"]) == set(FORMATS)
        for cell in p["formats"].values():
            assert set(cell) == {"available", "reason"}
            if not cell["available"]:
                assert cell["reason"]  # 不可用必给诚实原因
            else:
                assert cell["reason"] is None

    vel = _product(data, "velocity")
    assert vel["source"] == "mintpy/velocity.h5"
    assert vel["formats"]["h5"]["available"] is True
    assert vel["formats"]["csv"]["available"] is True  # venv 有 h5py
    # 引擎缺席(密封桩):三个引擎格式统一给探测结果的原因
    for fmt in ("gtiff", "kmz"):
        assert vel["formats"][fmt] == {"available": False, "reason": _ENGINE_OFF}
    # 结构性不适配:velocityStd 寄生文件、时序 3D 不能出单栅格
    std = _product(data, "velocity_std")
    assert std["formats"]["h5"]["available"] is False
    assert "velocity.h5" in std["formats"]["h5"]["reason"]
    ts = _product(data, "timeseries")
    assert ts["source"] == "mintpy/timeseries.h5"
    assert ts["formats"]["gtiff"]["available"] is False
    assert "3D" in ts["formats"]["gtiff"]["reason"]
    assert ts["formats"]["shp"]["available"] is False  # 引擎缺席
    # 响应全文不泄露磁盘布局
    leak = str(env["tmp"]).replace("\\", "/")
    assert leak not in r.text.replace("\\\\", "/").replace("\\", "/")


def test_options_missing_product_reason(env):
    """空工作区 run:全格式不可用,原因如实「没有产物 h5」。"""
    data = _options(env, session="sess-empty", run_id=EMPTY_RUN_ID).json()
    vel = _product(data, "velocity")
    assert vel["source"] is None
    for cell in vel["formats"].values():
        assert cell["available"] is False
    assert "没有 velocity 产物 h5" in vel["formats"]["csv"]["reason"]


def test_options_simulated_all_unavailable(env):
    """模拟 run 的 options 仍 200(信息端点),但矩阵全不可用并给统一原因。"""
    data = _options(env, session="sess-a", run_id=SIM_RUN_ID).json()
    assert data["simulated"] is True
    for p in data["products"]:
        for cell in p["formats"].values():
            assert cell["available"] is False
            assert "模拟产物不可导出" in cell["reason"]


# ---------------- ② csv 内容正确性 ----------------

def test_csv_velocity_content(env):
    """坐标=像元中心(X_FIRST+col*X_STEP),NaN 行剔除,列名 lon,lat,value。"""
    r = _export(env, run_id=RUN_ID, product="velocity", fmt="csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert f"{RUN_ID}_velocity.csv" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0] == "lon,lat,value"
    assert len(lines) == 1 + 98  # 100 像元 - 2 个 NaN
    # (0,0) 是 NaN → 首行是像元 (0,1):lon=100.0+0.05, lat=35.0, value=0.001
    assert lines[1] == "100.05,35,0.001"
    # (3,4) 是 NaN → 该像元行缺席(lat=35-3*0.05=34.85, lon=100+4*0.05=100.2)
    assert not any(ln.startswith("100.2,34.85,") for ln in lines)
    assert "nan" not in r.text.lower()


def test_csv_utm_columns_honest(env):
    """EPSG=32611(UTM 米坐标)→ 列名 x,y,value,绝不冒充经纬度。"""
    r = _export(env, session="sess-utm", run_id=UTM_RUN_ID,
                product="velocity", fmt="csv")
    assert r.status_code == 200
    lines = r.text.strip().splitlines()
    assert lines[0] == "x,y,value"
    assert lines[1] == "500080,3900000,0.001"  # 坐标 .10g:米级精度不丢


def test_csv_velocity_std_dataset(env):
    """velocity_std 读同文件的 velocityStd 数据集(0.001*0.1+0.0001=0.0002)。"""
    r = _export(env, run_id=RUN_ID, product="velocity_std", fmt="csv")
    assert r.status_code == 200
    lines = r.text.strip().splitlines()
    assert lines[0] == "lon,lat,value"
    assert lines[1] == "100.05,35,0.0002"
    assert len(lines) == 1 + 98  # NaN 传染同两个像元


def test_csv_timeseries_wide(env):
    """时序宽表:列 d<date>;全 NaN 像元剔除;部分缺失历元留空串。"""
    r = _export(env, run_id=RUN_ID, product="timeseries", fmt="csv")
    assert r.status_code == 200
    lines = r.text.strip().splitlines()
    assert lines[0] == "lon,lat," + ",".join(f"d{d}" for d in TS_DATES)
    assert len(lines) == 1 + 15  # 16 像元 - 1 个全 NaN
    # 像元(1,1):第 0 期 NaN → 空串;第 1/2 期 = 111/211
    assert "100.05,34.95,,111,211" in lines
    # 全 NaN 像元 (0,0)(lon=100,lat=35)整行缺席
    assert not any(ln.startswith("100,35,") for ln in lines)


def test_csv_radar_coords_409(env):
    """未地理编码(缺 X_FIRST 系属性)→ 409:行列号不冒充坐标。"""
    r = _export(env, session="sess-radar", run_id=RADAR_RUN_ID,
                product="velocity", fmt="csv")
    assert r.status_code == 409
    assert "地理编码" in r.json()["detail"]


def test_csv_point_cap_400(env, monkeypatch):
    """点数防线:超上限 400 并提示降采样(上限压到 50 触发)。"""
    monkeypatch.setattr(export_mod, "MAX_CSV_POINTS", 50)
    r = _export(env, run_id=RUN_ID, product="velocity", fmt="csv")
    assert r.status_code == 400
    assert "降采样" in r.json()["detail"]
    assert not (env["ws"] / "export" / f"{RUN_ID}_velocity.csv").exists()


def test_xlsx_openpyxl_missing_501(env, monkeypatch):
    """openpyxl 缺失:xlsx 501,提示改用 csv,不落假文件。"""
    monkeypatch.setattr(export_mod, "openpyxl_available", lambda: False)
    r = _export(env, run_id=RUN_ID, product="velocity", fmt="xlsx")
    assert r.status_code == 501
    assert r.json()["detail"] == "未安装 openpyxl，可用 csv 导出表格"
    assert not (env["ws"] / "export" / f"{RUN_ID}_velocity.xlsx").exists()
    cell = _product(_options(env, session="sess-a", run_id=RUN_ID).json(),
                    "velocity")["formats"]["xlsx"]
    assert cell == {"available": False, "reason": "未安装 openpyxl，可用 csv 导出表格"}


def test_xlsx_same_points_as_csv(env):
    """xlsx 与 csv 同一批像元;工作表名 velocity / timeseries。"""
    pytest.importorskip("openpyxl")
    from openpyxl import load_workbook

    csv_r = _export(env, run_id=RUN_ID, product="velocity", fmt="csv")
    assert csv_r.status_code == 200
    xlsx_r = _export(env, run_id=RUN_ID, product="velocity", fmt="xlsx")
    assert xlsx_r.status_code == 200
    assert "spreadsheetml.sheet" in xlsx_r.headers["content-type"]
    assert f"{RUN_ID}_velocity.xlsx" in xlsx_r.headers["content-disposition"]
    path = env["ws"] / "export" / f"{RUN_ID}_velocity.xlsx"
    wb = load_workbook(path, read_only=True)
    assert wb.sheetnames == ["velocity"]
    rows = list(wb["velocity"].iter_rows(values_only=True))
    wb.close()
    csv_lines = csv_r.text.strip().splitlines()
    assert list(rows[0]) == csv_lines[0].split(",")
    assert len(rows) == len(csv_lines)
    csv_first = [float(x) for x in csv_lines[1].split(",")]
    assert [float(x) for x in rows[1]] == csv_first

    ts_r = _export(env, run_id=RUN_ID, product="timeseries", fmt="xlsx")
    assert ts_r.status_code == 200
    ts_path = env["ws"] / "export" / f"{RUN_ID}_timeseries.xlsx"
    ts_wb = load_workbook(ts_path, read_only=True)
    assert ts_wb.sheetnames == ["timeseries"]
    ts_rows = list(ts_wb["timeseries"].iter_rows(values_only=True))
    ts_wb.close()
    assert ts_rows[0] == ("lon", "lat", *(f"d{d}" for d in TS_DATES))
    # 像元(1,1):第 0 期 NaN → 空单元格;与 csv 空串同一像元
    match = [row for row in ts_rows[1:] if row[0] == 100.05 and row[1] == 34.95]
    assert len(match) == 1 and match[0][2] is None
    assert [float(x) for x in match[0][3:]] == [111.0, 211.0]


def test_xlsx_point_cap_400(env, monkeypatch):
    """xlsx 共用 CSV 点数防线:超上限 400,不落文件。"""
    monkeypatch.setattr(export_mod, "openpyxl_available", lambda: True)
    monkeypatch.setattr(export_mod, "MAX_CSV_POINTS", 50)
    r = _export(env, run_id=RUN_ID, product="velocity", fmt="xlsx")
    assert r.status_code == 400
    assert "降采样" in r.json()["detail"]
    assert not (env["ws"] / "export" / f"{RUN_ID}_velocity.xlsx").exists()


# ---------------- ③ h5 直传与路径防御 ----------------

def test_h5_passthrough_bytes(env):
    """h5 直传:响应字节与源文件逐字节相同,下载名符合规范。"""
    r = _export(env, run_id=RUN_ID, product="velocity", fmt="h5")
    assert r.status_code == 200
    assert r.content == (env["ws"] / "mintpy" / "velocity.h5").read_bytes()
    assert f"{RUN_ID}_velocity.h5" in r.headers["content-disposition"]
    assert r.headers["x-export-reused"] == "1"  # 直传:源文件即交付物


def test_h5_missing_404_no_leak(env):
    """产物缺失 → 404,错误信息不携带磁盘绝对路径。"""
    r = _export(env, session="sess-empty", run_id=EMPTY_RUN_ID,
                product="velocity", fmt="h5")
    assert r.status_code == 404
    leak = str(env["tmp"]).replace("\\", "/")
    assert leak not in r.text.replace("\\\\", "/").replace("\\", "/")


def test_product_fmt_closed_sets_400(env):
    """product/fmt 闭集校验即路径面防御:穿越串到不了文件系统。"""
    for bad in ("../evil", "..\\evil", "velocity/../../x"):
        assert _export(env, run_id=RUN_ID, product=bad, fmt="csv").status_code == 400
    assert _export(env, run_id=RUN_ID, product="velocity",
                   fmt="../h5").status_code == 400


def test_velocity_std_h5_structural_400(env):
    """velocityStd 寄生在 velocity.h5 内:h5 直传按结构性不适配 400。"""
    r = _export(env, run_id=RUN_ID, product="velocity_std", fmt="h5")
    assert r.status_code == 400
    assert "velocity.h5" in r.json()["detail"]


def test_cross_session_404_no_leak(env):
    """跨会话借 run_id 导出:按「不存在」处理,不泄露归属与磁盘路径。"""
    r = _export(env, session="sess-b", run_id=RUN_ID, product="velocity", fmt="h5")
    assert r.status_code == 404
    leak = str(env["tmp"]).replace("\\", "/")
    assert leak not in r.text.replace("\\\\", "/").replace("\\", "/")


# ---------------- ④ 模拟 run 一律 409 ----------------

def test_simulated_run_409_all_formats(env):
    """runs.simulated=1:任何产品×格式一律 409(工作区文件在场也不放行)。"""
    for fmt in FORMATS:
        r = _export(env, run_id=SIM_RUN_ID, product="velocity", fmt=fmt)
        assert r.status_code == 409, fmt
        assert "模拟" in r.json()["detail"]


# ---------------- ⑤ 引擎缺失 → 501 ----------------

def test_gtiff_engine_missing_501(env):
    """引擎缺席:gtiff/kmz/shp 一律 501,探测原因原样透传。"""
    for product, fmt in (("velocity", "gtiff"), ("velocity", "kmz"),
                         ("timeseries", "shp")):
        r = _export(env, run_id=RUN_ID, product=product, fmt=fmt)
        assert r.status_code == 501, (product, fmt)
        assert r.json()["detail"] == _ENGINE_OFF


def test_engine_status_real_fn_install_hint(monkeypatch):
    """engine_status 真函数:探测不到 mintpy → 不可用 + 安装指引文案。"""
    import insar_agent.runtime.probe as probe

    monkeypatch.setattr(probe, "probe_environment",
                        lambda **kw: SimpleNamespace(engines={}))
    ok, reason = export_mod.engine_status()
    assert ok is False
    assert "安装指引" in reason and "mintpy" in reason


def test_engine_status_real_fn_present(monkeypatch):
    import insar_agent.runtime.probe as probe

    monkeypatch.setattr(probe, "probe_environment",
                        lambda **kw: SimpleNamespace(engines={"mintpy": "present(insar)"}))
    assert export_mod.engine_status() == (True, "")


# ---------------- ⑥ 引擎子进程路径(打桩) ----------------

def _fake_engine(record: list, *, rc: int = 0, stderr: str = "",
                 write_out: bool = True, sidecars: bool = False):
    """替身子进程:记录 argv/cwd/timeout;按需在 -o 处落产物(或不落)。"""
    def run(argv, cwd, timeout):
        record.append({"argv": list(argv), "cwd": Path(cwd), "timeout": timeout})
        if rc == 0 and write_out:
            out = Path(argv[argv.index("-o") + 1])
            out.write_bytes(b"ENGINE-OUT")
            if sidecars:
                for ext in (".dbf", ".shx", ".prj"):
                    out.with_suffix(ext).write_bytes(b"SIDE")
        return subprocess.CompletedProcess(argv, rc, stdout="", stderr=stderr)
    return run


def test_gtiff_command_assembly(env, monkeypatch):
    """save_gdal 命令组装:引擎解释器 + -m mintpy.cli.save_gdal + -d/-o/--of。"""
    monkeypatch.setenv("INSAR_ENGINE_PYTHON", "X:/fake-conda/python.exe")
    monkeypatch.delenv("INSAR_EXPORT_TIMEOUT", raising=False)  # 默认超时判定要密封
    monkeypatch.setattr(export_mod, "engine_status", lambda: (True, ""))
    record: list = []
    monkeypatch.setattr(export_mod, "_run_engine", _fake_engine(record))
    r = _export(env, run_id=RUN_ID, product="velocity", fmt="gtiff")
    assert r.status_code == 200
    assert r.content == b"ENGINE-OUT"
    assert r.headers["content-type"].startswith("image/tiff")
    assert len(record) == 1
    argv = record[0]["argv"]
    assert argv[0] == "X:/fake-conda/python.exe"  # engine_python 机制(env 优先)
    assert argv[1:4] == ["-u", "-m", "mintpy.cli.save_gdal"]
    assert argv[4].endswith("velocity.h5")
    i = argv.index("-d")
    assert argv[i + 1] == "velocity"
    out = argv[argv.index("-o") + 1]
    assert out.endswith(f"{RUN_ID}_velocity.tif.part")  # .part 原子替换
    assert argv[-2:] == ["--of", "GTiff"]
    assert record[0]["timeout"] == pytest.approx(600.0)
    assert (env["ws"] / "export" / f"{RUN_ID}_velocity.tif").is_file()


def test_kmz_command_positional_dataset(env, monkeypatch):
    """save_kmz:数据集走位置参数(velocityStd 单独出 kmz)。"""
    monkeypatch.setattr(export_mod, "engine_status", lambda: (True, ""))
    record: list = []
    monkeypatch.setattr(export_mod, "_run_engine", _fake_engine(record))
    r = _export(env, run_id=RUN_ID, product="velocity_std", fmt="kmz")
    assert r.status_code == 200
    argv = record[0]["argv"]
    assert argv[1:4] == ["-u", "-m", "mintpy.cli.save_kmz"]
    assert argv[4].endswith("velocity.h5")
    assert argv[5] == "velocityStd"
    assert (env["ws"] / "export" / f"{RUN_ID}_velocity_std.kmz").is_file()


def test_shp_command_and_zip_delivery(env, monkeypatch):
    """save_qgis:-g 几何文件;交付物是打包 shp/dbf/shx/prj 的 zip。"""
    monkeypatch.setattr(export_mod, "engine_status", lambda: (True, ""))
    record: list = []
    monkeypatch.setattr(export_mod, "_run_engine",
                        _fake_engine(record, sidecars=True))
    r = _export(env, run_id=RUN_ID, product="timeseries", fmt="shp")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/zip")
    argv = record[0]["argv"]
    assert argv[1:4] == ["-u", "-m", "mintpy.cli.save_qgis"]
    assert argv[4].endswith("timeseries.h5")
    assert argv[argv.index("-g") + 1].endswith("geometryGeo.h5")
    target = env["ws"] / "export" / f"{RUN_ID}_timeseries.shp.zip"
    assert target.is_file()
    with zipfile.ZipFile(target) as zf:
        exts = {Path(n).suffix for n in zf.namelist()}
    assert exts == {".shp", ".dbf", ".shx", ".prj"}


def test_shp_missing_geometry_404(env, monkeypatch):
    """几何文件缺失:save_qgis 无法定位点坐标 → 404 如实说明。"""
    monkeypatch.setattr(export_mod, "engine_status", lambda: (True, ""))
    (env["ws"] / "mintpy" / "inputs" / "geometryGeo.h5").unlink()
    r = _export(env, run_id=RUN_ID, product="timeseries", fmt="shp")
    assert r.status_code == 404
    assert "geometryGeo.h5" in r.json()["detail"]


def test_engine_failure_stderr_passthrough_502(env, monkeypatch):
    """引擎非零退出:stderr 原样透传,绝不留下假产物。"""
    monkeypatch.setattr(export_mod, "engine_status", lambda: (True, ""))
    record: list = []
    monkeypatch.setattr(
        export_mod, "_run_engine",
        _fake_engine(record, rc=1, stderr="ValueError: Input file is not geocoded"))
    r = _export(env, run_id=RUN_ID, product="velocity", fmt="gtiff")
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert "Input file is not geocoded" in detail and "退出码 1" in detail
    assert not (env["ws"] / "export" / f"{RUN_ID}_velocity.tif").exists()


def test_engine_ok_but_no_output_502(env, monkeypatch):
    """退出码 0 但产物缺席:同样 502 —— 绝不伪造/交付不存在的文件。"""
    monkeypatch.setattr(export_mod, "engine_status", lambda: (True, ""))
    record: list = []
    monkeypatch.setattr(export_mod, "_run_engine",
                        _fake_engine(record, write_out=False))
    r = _export(env, run_id=RUN_ID, product="velocity", fmt="gtiff")
    assert r.status_code == 502
    assert "未产出文件" in r.json()["detail"]


def test_engine_timeout_504(env, monkeypatch):
    """子进程超时:504 + 调参提示(超时保护经 subprocess timeout)。"""
    monkeypatch.setattr(export_mod, "engine_status", lambda: (True, ""))

    def hang(argv, cwd, timeout):
        raise subprocess.TimeoutExpired(argv, timeout)

    monkeypatch.setattr(export_mod, "_run_engine", hang)
    r = _export(env, run_id=RUN_ID, product="velocity", fmt="gtiff")
    assert r.status_code == 504
    assert "超时" in r.json()["detail"]


# ---------------- ⑦ 同参幂等复用 ----------------

def test_csv_idempotent_reuse_then_stale_regen(env):
    """同参二次导出直接复用(mtime 不变);源文件更新后如实重算。"""
    r1 = _export(env, run_id=RUN_ID, product="velocity", fmt="csv")
    assert r1.status_code == 200 and r1.headers["x-export-reused"] == "0"
    target = env["ws"] / "export" / f"{RUN_ID}_velocity.csv"
    mtime1 = target.stat().st_mtime

    r2 = _export(env, run_id=RUN_ID, product="velocity", fmt="csv")
    assert r2.status_code == 200 and r2.headers["x-export-reused"] == "1"
    assert target.stat().st_mtime == mtime1  # 未重写
    assert r2.text == r1.text

    # 源比导出新(rerun 覆写产物)→ 幂等失效,重算
    src = env["ws"] / "mintpy" / "velocity.h5"
    os.utime(src, (mtime1 + 10, mtime1 + 10))
    r3 = _export(env, run_id=RUN_ID, product="velocity", fmt="csv")
    assert r3.status_code == 200 and r3.headers["x-export-reused"] == "0"
    assert target.stat().st_mtime > mtime1


def test_gtiff_idempotent_reuse_skips_engine(env, monkeypatch):
    """引擎格式的幂等复用:第二次请求不再拉起子进程。"""
    monkeypatch.setattr(export_mod, "engine_status", lambda: (True, ""))
    record: list = []
    monkeypatch.setattr(export_mod, "_run_engine", _fake_engine(record))
    assert _export(env, run_id=RUN_ID, product="velocity",
                   fmt="gtiff").status_code == 200
    r2 = _export(env, run_id=RUN_ID, product="velocity", fmt="gtiff")
    assert r2.status_code == 200 and r2.headers["x-export-reused"] == "1"
    assert len(record) == 1  # 子进程只跑了一次
