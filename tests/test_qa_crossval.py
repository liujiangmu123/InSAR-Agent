"""QA crossval_ps_sbas:Pearson r / RMSE 来自重叠栅格,绝不编造。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from insar_agent.engines import qa
from insar_agent.registry.capabilities import REGISTRY

h5py = pytest.importorskip("h5py")


def _write_vel(path: Path, arr_m_yr) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        f.create_dataset("velocity", data=np.asarray(arr_m_yr, dtype=np.float32))


def test_compute_crossval_perfect_shift():
    x = np.arange(20, dtype=np.float64)
    y = x + 0.5
    out = qa.compute_crossval(x, y)
    assert out["crossval_status"] == "ok"
    assert out["n_overlap"] == 20
    assert out["crossval_r"] == pytest.approx(1.0, abs=1e-6)
    assert out["crossval_rmse_mm"] == pytest.approx(0.5, abs=1e-4)


def test_compute_crossval_noisy_grid_r_near_one():
    rng = np.random.default_rng(0)
    x = np.linspace(-8.0, 8.0, 40).reshape(5, 8)
    y = x + rng.normal(0.0, 0.02, size=x.shape)
    out = qa.compute_crossval(x, y)
    assert out["crossval_status"] == "ok"
    assert out["crossval_r"] > 0.99
    assert out["n_overlap"] == 40


def test_compute_crossval_overlap_too_small():
    x = np.array([1.0, 2.0, np.nan, np.nan])
    y = np.array([1.1, np.nan, 3.0, np.nan])
    out = qa.compute_crossval(x, y, min_overlap=10)
    assert out["crossval_r"] is None
    assert out["crossval_rmse_mm"] is None
    assert out["n_overlap"] == 1
    assert out["crossval_status"] == "overlap_too_small"


def test_compute_crossval_shape_mismatch():
    out = qa.compute_crossval(np.ones((2, 2)), np.ones((3, 3)))
    assert out["crossval_r"] is None
    assert out["crossval_status"] == "shape_mismatch"


def test_run_qa_crossval_writes_r(tmp_path):
    sbas = np.linspace(-0.02, 0.02, 24).reshape(4, 6)
    ps = sbas + 0.0001
    _write_vel(tmp_path / "mintpy/velocity.h5", sbas)
    _write_vel(tmp_path / "pystamps/velocity.h5", ps)
    result = qa.run_qa(tmp_path, "crossval_ps_sbas")
    assert result["method"] == "crossval_ps_sbas"
    assert result["crossval_status"] == "ok"
    assert result["crossval_r"] > 0.99
    assert result["n_overlap"] == 24
    data = json.loads((tmp_path / "products/report/qa.json").read_text(encoding="utf-8"))
    assert data["crossval_r"] == result["crossval_r"]
    assert data["crossval_r"] != 0.92


def test_run_qa_ps_missing_null_r(tmp_path):
    _write_vel(tmp_path / "mintpy/velocity.h5", np.ones((4, 4)) * 0.01)
    result = qa.run_qa(tmp_path, "crossval_ps_sbas")
    assert result["crossval_r"] is None
    assert result["crossval_status"] == "ps_velocity_missing"
    data = json.loads((tmp_path / "products/report/qa.json").read_text(encoding="utf-8"))
    assert data["crossval_r"] is None
    assert data["crossval_status"] == "ps_velocity_missing"
    assert "0.92" not in (tmp_path / "products/report/qa.json").read_text(encoding="utf-8")


def test_run_qa_overlap_too_small(tmp_path):
    sbas = np.ones((4, 4), dtype=np.float32) * 0.01
    ps = np.full((4, 4), np.nan, dtype=np.float32)
    ps[0, 0] = 0.01
    _write_vel(tmp_path / "mintpy/velocity.h5", sbas)
    _write_vel(tmp_path / "products/ps_velocity.h5", ps)
    result = qa.run_qa(tmp_path, "crossval_ps_sbas")
    assert result["crossval_r"] is None
    assert result["crossval_status"] == "overlap_too_small"
    assert result["n_overlap"] == 1


def test_coherence_mask_omits_crossval_r_even_if_ps_present(tmp_path):
    grid = np.ones((5, 5), dtype=np.float32) * 0.01
    _write_vel(tmp_path / "mintpy/velocity.h5", grid)
    _write_vel(tmp_path / "pystamps/velocity.h5", grid)
    result = qa.run_qa(tmp_path, "coherence_mask")
    assert result["method"] == "coherence_mask"
    assert "crossval_r" not in result
    assert "crossval_status" not in result


def test_loop_closure_reuses_stats_no_crash(tmp_path):
    _write_vel(tmp_path / "mintpy/velocity.h5", np.ones((3, 3)) * 0.002)
    result = qa.run_qa(tmp_path, "loop_closure")
    assert result["method"] == "loop_closure"
    assert "crossval_r" not in result
    assert "velocity_coverage" in result


def test_build_bakes_method_into_script(workspace):
    cross = qa.build(cap=REGISTRY[11], method="crossval_ps_sbas", params={},
                     run={"simulated": 0}, workspace=workspace)
    script = cross.files[".report/run_qa.py"]
    assert "METHOD = 'crossval_ps_sbas'" in script
    assert "METHOD = 'coherence_mask'" not in script
    mask = qa.build(cap=REGISTRY[11], method="coherence_mask", params={},
                    run={"simulated": 0}, workspace=workspace)
    assert "METHOD = 'coherence_mask'" in mask.files[".report/run_qa.py"]


def test_generated_script_subprocess_crossval(workspace):
    sbas = np.linspace(-0.01, 0.01, 30).reshape(5, 6)
    _write_vel(workspace / "mintpy/velocity.h5", sbas)
    _write_vel(workspace / "ps/velocity.h5", sbas + 1e-5)
    plan = qa.build(cap=REGISTRY[11], method="crossval_ps_sbas", params={},
                    run={"simulated": 0}, workspace=workspace)
    rel = ".report/run_qa.py"
    path = workspace / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plan.files[rel], encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, "-u", str(path)], cwd=str(workspace),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads((workspace / "products/report/qa.json").read_text(encoding="utf-8"))
    assert data["method"] == "crossval_ps_sbas"
    assert data["crossval_r"] > 0.99
    assert data["crossval_status"] == "ok"
