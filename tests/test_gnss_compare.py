"""R3 gnss_compare:RMSE 从合成 velocity.h5 + CSV 算出,禁止写死 0.92。"""

from __future__ import annotations

import csv
import json
import math

import pytest

from insar_agent.engines import gnss, resolve_builder
from insar_agent.registry.capabilities import REGISTRY

h5py = pytest.importorskip("h5py")
np = pytest.importorskip("numpy")


def _write_vel(path, arr, *, x0=-118.0, y0=36.0, dx=0.1, dy=-0.1):
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        f.create_dataset("velocity", data=np.asarray(arr, dtype="float32"))
        f.attrs["X_FIRST"] = x0
        f.attrs["Y_FIRST"] = y0
        f.attrs["X_STEP"] = dx
        f.attrs["Y_STEP"] = dy


def test_gnss_declared_not_default():
    cap = REGISTRY[11]
    m = cap.method("gnss_compare")
    assert m is not None and m.engine == "-"
    assert cap.default_method == "crossval_ps_sbas"
    assert "gnss_csv" in cap.params


def test_resolve_builder_gnss_compare():
    assert resolve_builder(REGISTRY[11], "gnss_compare", simulated=False) is gnss.build


def test_missing_csv_fails_at_build(workspace):
    with pytest.raises(ValueError, match="gnss_csv"):
        gnss.build(cap=REGISTRY[11], method="gnss_compare", params={},
                   run={"simulated": 0}, workspace=workspace)
    with pytest.raises(FileNotFoundError, match="GNSS CSV"):
        gnss.build(cap=REGISTRY[11], method="gnss_compare",
                   params={"gnss_csv": "missing.csv"},
                   run={"simulated": 0}, workspace=workspace)


def test_rmse_computed_from_grid_and_csv(tmp_path):
    vel = np.array([[0.010, 0.020, 0.030],
                    [0.040, 0.050, 0.060],
                    [0.070, 0.080, 0.090]], dtype="float32")
    _write_vel(tmp_path / "mintpy" / "velocity.h5", vel)
    csv_path = tmp_path / "gnss.csv"
    # 像元中心:(-118,36)=10 mm/yr, (-117.9,36)=20, (-118,35.9)=40
    csv_path.write_text(
        "lon,lat,los_mm\n"
        "-118.0,36.0,10.0\n"
        "-117.9,36.0,20.0\n"
        "-118.0,35.9,37.0\n",
        encoding="utf-8")
    out = gnss.run_gnss_compare(tmp_path, "gnss.csv")
    assert out["n_stations"] == 3
    assert out["status"] == "ok"
    expected = math.sqrt((0.0 ** 2 + 0.0 ** 2 + 3.0 ** 2) / 3.0)
    assert out["gnss_rmse_mm"] == pytest.approx(expected, abs=1e-3)
    assert out["gnss_rmse_mm"] != 0.92
    data = json.loads((tmp_path / "products/report/gnss.json").read_text(encoding="utf-8"))
    assert data["gnss_rmse_mm"] == out["gnss_rmse_mm"]
    qa = json.loads((tmp_path / "products/report/qa.json").read_text(encoding="utf-8"))
    assert qa["gnss_rmse_mm"] == out["gnss_rmse_mm"]
    assert "0.92" not in (tmp_path / "products/report/gnss.json").read_text(encoding="utf-8")
    assert out["residuals_csv"] == "products/report/gnss_residuals.csv"
    assert data["residuals_csv"] == "products/report/gnss_residuals.csv"
    res_path = tmp_path / "products/report/gnss_residuals.csv"
    assert res_path.is_file()
    assert res_path.read_bytes().startswith(b"\xef\xbb\xbf")
    table = list(csv.reader(res_path.read_text(encoding="utf-8-sig").splitlines()))
    assert table[0] == ["name", "lat", "lon", "los_mm", "insar_mm", "residual_mm"]
    assert len(table) == 4
    for row in table[1:]:
        los_mm, insar_mm, residual_mm = map(float, row[3:6])
        assert residual_mm == pytest.approx(insar_mm - los_mm, abs=1e-6)
        assert residual_mm != 0.92


def test_n_lt_3_computes_but_not_hard_gate(tmp_path):
    vel = np.ones((2, 2), dtype="float32") * 0.005
    _write_vel(tmp_path / "mintpy" / "velocity.h5", vel)
    (tmp_path / "gnss.csv").write_text(
        "lon,lat,los_mm\n-118.0,36.0,4.0\n-117.9,36.0,6.0\n", encoding="utf-8")
    out = gnss.run_gnss_compare(tmp_path, "gnss.csv")
    assert out["n_stations"] == 2
    assert out["status"] == "n_lt_3"
    assert out["gnss_rmse_mm"] is not None
    assert out["gnss_rmse_mm"] != 0.92
    table = list(csv.reader(
        (tmp_path / "products/report/gnss_residuals.csv")
        .read_text(encoding="utf-8-sig").splitlines()))
    assert table[0] == ["name", "lat", "lon", "los_mm", "insar_mm", "residual_mm"]
    assert len(table) == 3


def test_missing_geo_fails_honestly(tmp_path):
    path = tmp_path / "mintpy" / "velocity.h5"
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        f.create_dataset("velocity", data=np.ones((2, 2), dtype="float32"))
    (tmp_path / "gnss.csv").write_text("lon,lat,los_mm\n-118,36,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="地理编码"):
        gnss.run_gnss_compare(tmp_path, "gnss.csv")


def test_enu_without_geometry_fails(tmp_path):
    _write_vel(tmp_path / "mintpy" / "velocity.h5", np.ones((2, 2)) * 0.01)
    (tmp_path / "gnss.csv").write_text(
        "lon,lat,ve,vn,vu\n-118.0,36.0,1,2,3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="入射角"):
        gnss.run_gnss_compare(tmp_path, "gnss.csv")


def test_build_bakes_csv_path(workspace):
    (workspace / "st.csv").write_text("lon,lat,los_mm\n0,0,1\n", encoding="utf-8")
    plan = gnss.build(cap=REGISTRY[11], method="gnss_compare",
                      params={"gnss_csv": "st.csv"},
                      run={"simulated": 0}, workspace=workspace)
    script = plan.files[".report/run_gnss.py"]
    assert "st.csv" in script
    assert "run_gnss_compare" in script
