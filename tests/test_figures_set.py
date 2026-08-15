"""figure_set 图集:默认行为回归、缺数据跳过、成功图三档+sidecar。

不跑 real_ridgecrest,不跑重型出图。渲染只用 8×8 真实 h5(测试基建),
由引擎 Python(有 matplotlib)执行生成脚本。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from insar_agent.engines import figures as figures_engine
from insar_agent.engines.mintpy import engine_python
from insar_agent.registry.capabilities import REGISTRY

try:
    import h5py
    import numpy as np
    _HAS_H5PY = True
except ImportError:
    _HAS_H5PY = False


def _script(workspace, params) -> str:
    plan = figures_engine.build(cap=REGISTRY[10], method="figure_journal", params=params,
                                run={"simulated": 0}, workspace=workspace)
    return plan.files[".report/make_figures.py"]


def test_figure_set_default_matches_explicit_velocity(workspace):
    """figure_set=['velocity'] 与默认参数注入同一清单(回归保护)。"""
    default = _script(workspace, REGISTRY[10].default_params())
    explicit = _script(workspace, dict(REGISTRY[10].default_params(), figure_set=["velocity"]))
    assert "FIGURE_SET = ['velocity']" in default
    assert "FIGURE_SET = ['velocity']" in explicit
    assert "velocity.png" in default and "velocity_hist.png" in default


def test_figure_set_missing_kinds_skip_in_script(workspace):
    script = _script(workspace, dict(REGISTRY[10].default_params(),
                                     figure_set=["velocity", "network", "coherence"]))
    assert "SKIP" in script
    assert "绝不画占位图" in script or "skip(" in script
    assert "if \"network\" in FIGURE_SET" in script
    assert "if \"coherence\" in FIGURE_SET" in script


def test_cmap_route_table_still_present(workspace):
    script = _script(workspace, REGISTRY[10].default_params())
    for pair in ('"velocity": "vik"', '"coherence": "batlow"',
                 '"temporalCoherence": "batlow"', '"wrapPhase": "romaO"'):
        assert pair in script
    assert "if True else 'vik'" in script
    assert "'cmap': 'vik'" in script


def _write_velocity_h5(ws: Path) -> None:
    mintpy = ws / "mintpy"
    mintpy.mkdir(parents=True, exist_ok=True)
    rng = np.linspace(-0.02, 0.03, 64, dtype=np.float32).reshape(8, 8)
    with h5py.File(mintpy / "velocity.h5", "w") as f:
        f.create_dataset("velocity", data=rng)
        f.attrs["FILE_TYPE"] = "velocity"
        f.attrs["X_FIRST"] = -117.7
        f.attrs["Y_FIRST"] = 35.8
        f.attrs["X_STEP"] = 0.01
        f.attrs["Y_STEP"] = -0.01
        f.attrs["START_DATE"] = "20190610"
        f.attrs["END_DATE"] = "20190815"
        f.attrs["PLATFORM"] = "Sen"
        f.attrs["ORBIT_DIRECTION"] = "DESCENDING"


def _engine_has_mpl() -> str | None:
    py = engine_python()
    probe = subprocess.run(
        [py, "-c", "import h5py,matplotlib,numpy"], capture_output=True, text=True)
    return py if probe.returncode == 0 else None


@pytest.mark.skipif(not _HAS_H5PY, reason="宿主 pytest 环境无 h5py,无法写测试夹具")
def test_velocity_only_writes_tiers_and_sidecar(workspace, tmp_path):
    py = _engine_has_mpl()
    if py is None:
        pytest.skip("引擎 Python 无 matplotlib,跳过轻量出图")
    _write_velocity_h5(workspace)
    params = dict(REGISTRY[10].default_params(), figure_set=["velocity"], dpi=72)
    plan = figures_engine.build(cap=REGISTRY[10], method="figure_journal", params=params,
                                run={"simulated": 0}, workspace=workspace)
    script = workspace / ".report" / "make_figures.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(plan.files[".report/make_figures.py"], encoding="utf-8")
    proc = subprocess.run([py, "-u", str(script)], cwd=workspace, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    figdir = workspace / "products" / "figures"
    for name in ("velocity.png", "velocity.pdf", "velocity_browse.png",
                 "velocity_thumb.png", "velocity.json",
                 "velocity_hist.png", "velocity_hist_browse.png",
                 "velocity_hist_thumb.png", "velocity_hist.json"):
        assert (figdir / name).exists(), name
    meta = json.loads((figdir / "velocity.json").read_text(encoding="utf-8"))
    assert meta["units"] == "mm/yr"
    assert "skipped" not in meta


@pytest.mark.skipif(not _HAS_H5PY, reason="宿主 pytest 环境无 h5py,无法写测试夹具")
def test_missing_kinds_skip_sidecar_no_placeholder(workspace):
    py = _engine_has_mpl()
    if py is None:
        pytest.skip("引擎 Python 无 matplotlib,跳过轻量出图")
    _write_velocity_h5(workspace)
    params = dict(REGISTRY[10].default_params(),
                  figure_set=["velocity", "network", "coherence", "points_timeseries"],
                  points_lalo=[[35.75, -117.60]], dpi=72)
    plan = figures_engine.build(cap=REGISTRY[10], method="figure_journal", params=params,
                                run={"simulated": 0}, workspace=workspace)
    script = workspace / ".report" / "make_figures.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(plan.files[".report/make_figures.py"], encoding="utf-8")
    proc = subprocess.run([py, "-u", str(script)], cwd=workspace, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    figdir = workspace / "products" / "figures"
    assert (figdir / "velocity.png").exists()
    for skipped in ("network", "coherence", "points_timeseries"):
        assert not (figdir / f"{skipped}.png").exists(), f"不得画占位图 {skipped}.png"
        side = json.loads((figdir / f"{skipped}.json").read_text(encoding="utf-8"))
        assert side["skipped"] == skipped
        assert side["reason"]
