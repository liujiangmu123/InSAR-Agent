"""MintPy 后处理 spatial_average:命令快照 + 宿主引擎真跑(速度场).

mintpy.cli.spatial_average 对 FILE_TYPE=velocity 必崩(标量写 txt);
本文件验证包装脚本改为 ut.spatial_average(saveList=False) 且均值与 numpy 一致。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from insar_agent.engines import mintpy_post
from insar_agent.registry.capabilities import REGISTRY
from insar_agent.runtime.render import render_plan_files

_DEFAULT_PREFIX = r"E:\miniforge3\envs\insar"


def _engine_python() -> Path:
    prefix = os.environ.get("INSAR_ENGINE_PREFIX") or _DEFAULT_PREFIX
    p = Path(prefix)
    if p.is_file():
        return p
    return p / ("python.exe" if sys.platform == "win32" else "bin/python")


_ENGINE_PY = _engine_python()


def _write_velocity_h5(path: Path, data: np.ndarray) -> None:
    h5py = pytest.importorskip("h5py")
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        f.create_dataset("velocity", data=data)
        f.attrs["FILE_TYPE"] = np.bytes_("velocity")
        f.attrs["UNIT"] = np.bytes_("m/year")
        f.attrs["WIDTH"] = np.bytes_(str(int(data.shape[1])))
        f.attrs["LENGTH"] = np.bytes_(str(int(data.shape[0])))


def test_spatial_average_command_snapshot(workspace):
    src = workspace / "analysis" / "decomposed.h5"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"")
    plan = mintpy_post.build(
        cap=REGISTRY[24], method="spatial_average", params={},
        run={"simulated": 0}, workspace=workspace)
    assert plan.argv[1:] == ["-u", ".analysis/run_post.py"]
    assert set(plan.files) == {".analysis/run_post.py", "analysis/.keep"}
    script = plan.files[".analysis/run_post.py"]
    assert "ut.spatial_average" in script
    assert "saveList=False" in script
    assert "atleast_1d" in script
    assert "analysis/measure.json" in script
    assert "mintpy.cli.spatial_average" not in script


@pytest.mark.skipif(
    not _ENGINE_PY.is_file(),
    reason=f"无 conda insar 引擎 Python({_ENGINE_PY}),跳过宿主 spatial_average 回归",
)
def test_spatial_average_velocity_mean_matches_numpy(workspace):
    rng = np.random.default_rng(0)
    data = rng.normal(0.01, 0.002, size=(8, 10)).astype(np.float32)
    _write_velocity_h5(workspace / "analysis" / "decomposed.h5", data)

    plan = mintpy_post.build(
        cap=REGISTRY[24], method="spatial_average", params={},
        run={"simulated": 0}, workspace=workspace)
    script = plan.files[".analysis/run_post.py"]
    assert "ut.spatial_average" in script
    assert "saveList=False" in script
    assert "atleast_1d" in script
    assert "analysis/measure.json" in script
    assert "mintpy.cli.spatial_average" not in script

    render_plan_files(workspace, plan)
    env = os.environ.copy()
    env.update(plan.env)
    cp = subprocess.run(
        plan.argv, cwd=plan.cwd, env=env,
        capture_output=True, text=True, timeout=60)
    assert cp.returncode == 0, f"stdout={cp.stdout!r}\nstderr={cp.stderr!r}"
    out = workspace / "analysis" / "measure.json"
    assert out.is_file()
    report = json.loads(out.read_text(encoding="utf-8"))
    expect = float(np.nanmean(data))
    assert report["method"] == "spatial_average"
    assert report["file"] == "analysis/decomposed.h5"
    assert report["dataset"] is None
    assert "mean" in report
    assert abs(float(report["mean"]) - expect) < 1e-6
    assert report.get("unit") == "m/year"
