"""Phase 11:校正链参数穿透 smallbaselineApp cfg + 闭集校验。

不执行真实 MintPy:只渲染 cfg、校验注册表、核对 plate_motion 的 argv 拼装。
新增第 7/8/9 步参数会改变 default_params(),因此新计划 eval_hash 与旧 run
不同 —— fork 旧 run 时这些步骤不再零重算复用。这是正确行为(参数面变了,
复现语义就变了),不是回归。
"""

from __future__ import annotations

import pytest

from insar_agent.engines import mintpy as mintpy_engine
from insar_agent.engines import mintpy_post, resolve_builder
from insar_agent.engines.mintpy import render_cfg
from insar_agent.registry.capabilities import REGISTRY


def test_cfg_renders_correction_defaults():
    cfg = render_cfg({"chain": {}}, this_step=7, this_method="mintpy_sbas", this_params={})
    assert "mintpy.unwrapError.method           = no" in cfg
    assert "mintpy.ionosphericDelay.method      = no" in cfg
    assert "mintpy.timeFunc.uncertaintyQuantification = residue" in cfg
    assert "mintpy.timeFunc.bootstrapCount            = 400" in cfg
    # 既有默认不得被新键带偏(旧 run 语义)
    assert "mintpy.troposphericDelay.method     = pyaps" in cfg
    assert "mintpy.solidEarthTides              = no" in cfg
    assert "mintpy.network.perpBaseMax  = no" in cfg
    assert "mintpy.deramp                       = no" in cfg


def test_cfg_renders_bridging_and_bootstrap():
    run = {"chain": {7: {"method": "mintpy_sbas",
                         "params": {"unwrap_error_method": "bridging"}},
                     9: {"method": "linear",
                         "params": {"uncertainty": "bootstrap", "bootstrap_count": 800}}}}
    cfg = render_cfg(run, this_step=7, this_method="mintpy_sbas", this_params={})
    assert "= bridging" in cfg and "= bootstrap" in cfg and "= 800" in cfg
    assert "mintpy.unwrapError.method           = bridging" in cfg
    assert "mintpy.timeFunc.uncertaintyQuantification = bootstrap" in cfg
    assert "mintpy.timeFunc.bootstrapCount            = 800" in cfg


def test_cfg_renders_tropo_opera_and_iono_override():
    run = {"chain": {8: {"method": "tropo_opera",
                         "params": {"iono_method": "split_spectrum"}}}}
    cfg = render_cfg(run, this_step=8, this_method="tropo_opera", this_params={})
    assert "mintpy.troposphericDelay.method     = opera" in cfg
    assert "mintpy.ionosphericDelay.method      = split_spectrum" in cfg


def test_registry_rejects_hallucinated_correction_values():
    cap = REGISTRY[8]
    assert cap.validate_params({"iono_method": "magic"})  # 非闭集值必须被拒
    assert REGISTRY[7].validate_params({"unwrap_error_method": "magic"})
    assert REGISTRY[9].validate_params({"uncertainty": "magic"})
    assert not REGISTRY[8].validate_params({"iono_method": "split_spectrum"})
    assert not REGISTRY[7].validate_params({"unwrap_error_method": "bridging+phase_closure"})


def test_plate_motion_itrf_declared_and_routes_away_from_smallbaseline():
    cap = REGISTRY[22]
    assert cap.method("plate_motion_itrf") is not None
    assert cap.method("passthrough") is not None
    builder = resolve_builder(cap, "plate_motion_itrf", simulated=False)
    assert builder is mintpy_post.build
    assert builder is not mintpy_engine.build


def test_plate_motion_build_requires_plate_and_geometry(workspace):
    cap = REGISTRY[22]
    with pytest.raises(ValueError, match="plate"):
        mintpy_post.build(cap=cap, method="plate_motion_itrf", params={},
                          run={}, workspace=workspace)
    (workspace / "analysis").mkdir()
    (workspace / "analysis" / "masked.h5").write_bytes(b"")
    with pytest.raises(FileNotFoundError, match="几何文件"):
        mintpy_post.build(cap=cap, method="plate_motion_itrf",
                          params={"plate": "NorthAmerica"},
                          run={}, workspace=workspace)


def test_plate_motion_build_argv_contains_upstream_flags(workspace):
    cap = REGISTRY[22]
    geom = workspace / "mintpy" / "inputs" / "geometryRadar.h5"
    geom.parent.mkdir(parents=True)
    geom.write_bytes(b"")
    masked = workspace / "analysis" / "masked.h5"
    masked.parent.mkdir()
    masked.write_bytes(b"")
    plan = mintpy_post.build(cap=cap, method="plate_motion_itrf",
                             params={"plate": "NorthAmerica"},
                             run={}, workspace=workspace)
    script = plan.files[".analysis/run_post.py"]
    assert "mintpy.cli.plate_motion" in script
    assert "--plate" in script and "NorthAmerica" in script
    assert "-g" in script and "geometryRadar.h5" in script
    assert "-v" in script and "analysis/masked.h5" in script
    assert "-o" in script and "analysis/corrected.h5" in script
    assert "PYTHONUTF8" in plan.env
