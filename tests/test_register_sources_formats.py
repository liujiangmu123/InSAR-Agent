"""R2b 模式 C:register_sources 吃 GeoTIFF / CSV;不支持扩展名显式失败。"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from insar_agent.engines import passthrough
from insar_agent.registry.capabilities import REGISTRY

h5py = pytest.importorskip("h5py")
np = pytest.importorskip("numpy")


def _run_plan(plan, cwd):
    for rel, content in plan.files.items():
        target = cwd / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return subprocess.run(
        plan.argv, cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def test_png_rejected_at_build(workspace):
    png = workspace / "map.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    with pytest.raises(ValueError, match="不支持扩展名"):
        passthrough.build(
            cap=REGISTRY[20], method="register_sources",
            params={"primary": "map.png"}, run={"simulated": 0},
            workspace=workspace)


def test_h5_still_materializes_to_source_h5(workspace):
    src = workspace / "mintpy" / "velocity.h5"
    src.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(src, "w") as f:
        f.create_dataset("velocity", data=np.ones((2, 2), dtype="float32") * 0.01)
    plan = passthrough.build(
        cap=REGISTRY[20], method="register_sources",
        params={"primary": "mintpy/velocity.h5"}, run={"simulated": 0},
        workspace=workspace)
    cp = _run_plan(plan, workspace)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    dst = workspace / "analysis" / "source.h5"
    assert dst.is_file()
    with h5py.File(dst, "r") as f:
        assert "velocity" in f
        assert float(f["velocity"][0, 0]) == pytest.approx(0.01)
    man = json.loads((workspace / "analysis" / "source_manifest.json").read_text(
        encoding="utf-8"))
    assert man["format"] == "hdf5"


def test_csv_registers_original_columns(workspace):
    csv_path = workspace / "egms.csv"
    csv_path.write_text("longitude,latitude,vel\n10.1,50.2,-3.4\n10.2,50.3,-1.1\n",
                        encoding="utf-8")
    plan = passthrough.build(
        cap=REGISTRY[20], method="register_sources",
        params={"primary": "egms.csv"}, run={"simulated": 0},
        workspace=workspace)
    cp = _run_plan(plan, workspace)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    dst = workspace / "analysis" / "source.csv"
    assert dst.is_file()
    text = dst.read_text(encoding="utf-8")
    assert "longitude,latitude,vel" in text
    assert "-3.4" in text
    man = json.loads((workspace / "analysis" / "source_manifest.json").read_text(
        encoding="utf-8"))
    assert man["format"] == "csv"
    assert man["columns"] == ["longitude", "latitude", "vel"]
    assert man["column_map"]["lon"] == "longitude"
    assert man["column_map"]["lat"] == "latitude"
    assert man["column_map"]["velocity"] == "vel"
    assert "未发明" in man["note"]


def test_geotiff_hardlinks_without_inventing_values(workspace):
    tif = workspace / "disp.tif"
    tif.write_bytes(b"II*\x00" + b"\x00" * 32)
    plan = passthrough.build(
        cap=REGISTRY[20], method="register_sources",
        params={"primary": "disp.tif"}, run={"simulated": 0},
        workspace=workspace)
    cp = _run_plan(plan, workspace)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert (workspace / "analysis" / "source.tif").is_file()
    man = json.loads((workspace / "analysis" / "source_manifest.json").read_text(
        encoding="utf-8"))
    assert man["format"] == "geotiff"
    h5 = workspace / "analysis" / "source.h5"
    if h5.is_file():
        with h5py.File(h5, "r") as f:
            arr = np.asarray(f["velocity"][()])
        fake = np.random.default_rng(0).random(arr.shape)
        assert not np.allclose(arr, fake)
    else:
        assert man["h5_written"] is False
        assert "未写" in man["note"]


def test_geotiff_with_rasterio_writes_real_velocity(workspace):
    rasterio = pytest.importorskip("rasterio")
    from rasterio.transform import from_origin

    tif = workspace / "real.tif"
    data = np.array([[0.01, -0.02], [0.03, 0.04]], dtype="float32")
    with rasterio.open(
        tif, "w", driver="GTiff", height=2, width=2, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(10.0, 50.0, 0.1, 0.1),
    ) as dst:
        dst.write(data, 1)
    passthrough.register_one(workspace, tif, "source")
    man = json.loads((workspace / "analysis" / "source_manifest.json").read_text(
        encoding="utf-8"))
    assert man["h5_written"] is True
    with h5py.File(workspace / "analysis" / "source.h5", "r") as f:
        got = np.asarray(f["velocity"][()])
    assert np.allclose(got, data)
