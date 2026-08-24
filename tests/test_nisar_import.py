"""R2a nisar_import:登记 GUNW,不下载;缺 source 显式失败。"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from insar_agent.engines import localdata, resolve_builder
from insar_agent.registry.capabilities import REGISTRY

h5py = pytest.importorskip("h5py")


def _run_plan(plan, cwd):
    for rel, content in plan.files.items():
        target = cwd / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return subprocess.run(
        plan.argv, cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def test_nisar_import_declared_not_default():
    cap = REGISTRY[1]
    m = cap.method("nisar_import")
    assert m is not None
    assert m.engine == "-"
    assert "GUNW" in m.why and "跳过 2-6" in m.why
    assert cap.default_method == "local_import"
    assert "data/nisar" in cap.artifacts[0].candidates


def test_resolve_builder_real_mode_nisar_import():
    assert resolve_builder(REGISTRY[1], "nisar_import", simulated=False) is localdata.build


def test_nisar_script_contains_registration_logic(workspace):
    plan = localdata.build(
        cap=REGISTRY[1], method="nisar_import",
        params={"source": r"E:\data\NISAR_GUNW"}, run={"simulated": 0},
        workspace=workspace)
    script = plan.files[".import/import_nisar.py"]
    assert "data" in script and "nisar" in script
    assert "manifest.json" in script
    assert "GUNW" in script
    assert "mklink" in script
    assert repr(r"E:\data\NISAR_GUNW") in script
    assert "download" not in script.lower() or "不下载" in script


def test_nisar_import_missing_source_fails(workspace):
    plan = localdata.build(
        cap=REGISTRY[1], method="nisar_import", params={"source": ""},
        run={"simulated": 0}, workspace=workspace)
    cp = _run_plan(plan, workspace)
    assert cp.returncode == 2
    assert "未指定数据源" in cp.stdout
    assert not (workspace / "data" / "nisar" / "manifest.json").exists()


def test_nisar_import_missing_dir_fails(workspace):
    plan = localdata.build(
        cap=REGISTRY[1], method="nisar_import",
        params={"source": str(workspace / "no_such_gunw")},
        run={"simulated": 0}, workspace=workspace)
    cp = _run_plan(plan, workspace)
    assert cp.returncode == 2
    assert "不存在" in cp.stdout


def test_nisar_import_registers_gunw_h5(tmp_path, workspace):
    src = tmp_path / "gunw_src"
    src.mkdir()
    product = src / "NISAR_L2_GUNW_sample.h5"
    with h5py.File(product, "w") as f:
        f.create_dataset("unw", data=[1, 2, 3])
    plan = localdata.build(
        cap=REGISTRY[1], method="nisar_import",
        params={"source": str(src)}, run={"simulated": 0},
        workspace=workspace)
    cp = _run_plan(plan, workspace)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    manifest_path = workspace / "data" / "nisar" / "manifest.json"
    assert manifest_path.is_file()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["mode"] == "nisar_gunw"
    assert data["dest"] == "data/nisar"
    assert any("GUNW" in p for p in data["products"])
    assert (workspace / "data" / "nisar" / "products").exists()


def test_nisar_import_empty_dir_fails(tmp_path, workspace):
    src = tmp_path / "empty"
    src.mkdir()
    (src / "readme.txt").write_text("not a gunw", encoding="utf-8")
    plan = localdata.build(
        cap=REGISTRY[1], method="nisar_import",
        params={"source": str(src)}, run={"simulated": 0},
        workspace=workspace)
    cp = _run_plan(plan, workspace)
    assert cp.returncode == 3
    assert "没有 GUNW" in cp.stdout
    assert not (workspace / "data" / "nisar" / "manifest.json").exists()
