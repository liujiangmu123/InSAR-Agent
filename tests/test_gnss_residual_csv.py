"""gnss_compare 写真实逐站残差表,禁止编造 0.92。"""

from __future__ import annotations

import csv
import json
import math

import pytest

from insar_agent.engines import gnss

h5py = pytest.importorskip("h5py")
np = pytest.importorskip("numpy")

_FIELDS = ["name", "lat", "lon", "los_mm", "insar_mm", "residual_mm"]


def _write_vel(path, arr, *, x0=-118.0, y0=36.0, dx=0.1, dy=-0.1):
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        f.create_dataset("velocity", data=np.asarray(arr, dtype="float64"))
        f.attrs["X_FIRST"] = x0
        f.attrs["Y_FIRST"] = y0
        f.attrs["X_STEP"] = dx
        f.attrs["Y_STEP"] = dy


def _read_table(path):
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    return list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines()))


def test_residual_csv_matches_insar_minus_los(tmp_path):
    vel = np.array([[0.010, 0.020, 0.030],
                    [0.040, 0.050, 0.060],
                    [0.070, 0.080, 0.090]], dtype="float64")
    _write_vel(tmp_path / "mintpy" / "velocity.h5", vel)
    (tmp_path / "gnss.csv").write_text(
        "name,lon,lat,los_mm\n"
        "A,-118.0,36.0,10.0\n"
        "B,-117.9,36.0,20.0\n"
        "C,-118.0,35.9,37.0\n",
        encoding="utf-8")
    out = gnss.run_gnss_compare(tmp_path, "gnss.csv")
    expected = math.sqrt((0.0 ** 2 + 0.0 ** 2 + 3.0 ** 2) / 3.0)
    assert out["gnss_rmse_mm"] == pytest.approx(expected, abs=1e-4)
    assert out["gnss_rmse_mm"] != 0.92
    assert out["residuals_csv"] == "products/report/gnss_residuals.csv"
    data = json.loads((tmp_path / "products/report/gnss.json").read_text(encoding="utf-8"))
    assert data["residuals_csv"] == "products/report/gnss_residuals.csv"

    table = _read_table(tmp_path / "products/report/gnss_residuals.csv")
    assert table[0] == _FIELDS
    assert [row[0] for row in table[1:]] == ["A", "B", "C"]
    got = [(float(r[3]), float(r[4]), float(r[5])) for r in table[1:]]
    assert got[0][0] == pytest.approx(10.0)
    assert got[0][1] == pytest.approx(10.0)
    assert got[1][0] == pytest.approx(20.0)
    assert got[1][1] == pytest.approx(20.0)
    assert got[2][0] == pytest.approx(37.0)
    assert got[2][1] == pytest.approx(40.0)
    for los_mm, insar_mm, residual_mm in got:
        assert residual_mm == pytest.approx(insar_mm - los_mm, abs=1e-9)
        assert residual_mm != 0.92
    assert got[2][2] == pytest.approx(3.0)


def test_n_zero_writes_header_only(tmp_path):
    vel = np.ones((2, 2), dtype="float64") * 0.005
    _write_vel(tmp_path / "mintpy" / "velocity.h5", vel)
    (tmp_path / "gnss.csv").write_text(
        "lon,lat,los_mm\n0.0,0.0,1.0\n1.0,1.0,2.0\n", encoding="utf-8")
    out = gnss.run_gnss_compare(tmp_path, "gnss.csv")
    assert out["n_stations"] == 0
    assert out["n_skipped"] == 2
    assert out["gnss_rmse_mm"] is None
    assert out["status"] == "no_overlap"
    assert out["residuals_csv"] == "products/report/gnss_residuals.csv"
    table = _read_table(tmp_path / "products/report/gnss_residuals.csv")
    assert table == [_FIELDS]


def test_skipped_stations_omitted_from_csv(tmp_path):
    vel = np.array([[0.010, np.nan], [0.040, 0.050]], dtype="float64")
    _write_vel(tmp_path / "mintpy" / "velocity.h5", vel)
    (tmp_path / "gnss.csv").write_text(
        "station,lon,lat,los_mm\n"
        "IN,-118.0,36.0,8.0\n"
        "NANPIX,-117.9,36.0,1.0\n"
        "OUT,0.0,0.0,1.0\n",
        encoding="utf-8")
    out = gnss.run_gnss_compare(tmp_path, "gnss.csv")
    assert out["n_stations"] == 1
    assert out["n_skipped"] == 2
    table = _read_table(tmp_path / "products/report/gnss_residuals.csv")
    assert table[0] == _FIELDS
    assert len(table) == 2
    assert table[1][0] == "IN"
    los_mm, insar_mm, residual_mm = map(float, table[1][3:6])
    assert los_mm == pytest.approx(8.0)
    assert insar_mm == pytest.approx(10.0)
    assert residual_mm == pytest.approx(2.0)
    assert residual_mm != 0.92
    names = {row[0] for row in table[1:]}
    assert "NANPIX" not in names
    assert "OUT" not in names
