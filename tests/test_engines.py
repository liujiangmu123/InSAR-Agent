"""路线 0.5:命令快照测试 —— 不执行引擎,验证命令构建正确性。

对拍基准:实测配置 InSAR-Pro RidgecrestSenDT71.txt(AGENT-DESIGN §0.5.4 唯一可信真值)。
"""

from __future__ import annotations

import pytest

from insar_agent.engines import ToolMissing, resolve_builder
from insar_agent.engines import mintpy as mintpy_engine
from insar_agent.registry.capabilities import REGISTRY

CHAIN = {
    7: {"method": "mintpy_sbas",
        "params": {"network": "small_baseline", "max_temporal_baseline": 120,
                   "parallel_workers": 4}},
    8: {"method": "tropo_era5_pyaps",
        "params": {"ramp": "linear", "dem_error": True, "solid_earth_tides": True}},
    9: {"method": "step", "params": {"step_date": "20190706T0320", "poly_order": 1,
                                     "periods": [1, 0.5]}},
}
RUN = {"simulated": 0, "chain": CHAIN}


def test_mintpy_cfg_matches_measured_config(workspace):
    """cfg 关键行与实测 RidgecrestSenDT71.txt 语义一致。"""
    cfg = mintpy_engine.render_cfg(RUN, this_step=7, this_method="mintpy_sbas",
                                   this_params=CHAIN[7]["params"])
    assert "mintpy.load.processor       = hyp3" in cfg
    assert "mintpy.load.unwFile         = ../hyp3/*/*unw_phase_clipped.tif" in cfg
    assert "mintpy.load.corFile         = ../hyp3/*/*corr_clipped.tif" in cfg
    assert "mintpy.networkInversion.weightFunc  = no" in cfg      # 实测配置的有意覆盖
    assert "mintpy.troposphericDelay.method     = pyaps" in cfg
    assert "mintpy.timeFunc.stepDate    = 20190706T0320" in cfg   # 同震阶跃日期
    assert "mintpy.subset.lalo          = 391e4:400e4,39e4:51e4" in cfg
    assert "mintpy.reference.lalo       = 391.5e4,45e4" in cfg
    assert "mintpy.plot = no" in cfg


def test_mintpy_ranges_single_argv(workspace):
    """三个 capability 的 --start/--end 区间正确,单 argv 免 bash(Windows 可跑)。"""
    expect = {7: ("load_data", "invert_network"),
              8: ("correct_LOD", "reference_date"),
              9: ("velocity", "velocity")}
    for sid, (start, end) in expect.items():
        plan = mintpy_engine.build(cap=REGISTRY[sid], method=CHAIN[sid]["method"],
                                   params=CHAIN[sid]["params"], run=RUN, workspace=workspace)
        assert plan.argv[1:] == ["-u", "-m", "mintpy.cli.smallbaselineApp", "insar_agent.cfg",
                                 "--start", start, "--end", end]
        assert plan.cwd.endswith("mintpy")
        assert plan.env["HDF5_USE_FILE_LOCKING"] == "FALSE"       # DESIGN.md:472
        assert int(plan.env["OMP_NUM_THREADS"]) <= 8              # 重型计算管控
        assert "mintpy/insar_agent.cfg" in plan.files


def test_cfg_shared_across_steps(workspace):
    """cfg 是全链共享的:第 7 步渲染的 cfg 已含第 9 步的阶跃日期(MintPy 语义)。"""
    p7 = mintpy_engine.build(cap=REGISTRY[7], method="mintpy_sbas",
                             params=CHAIN[7]["params"], run=RUN, workspace=workspace)
    p9 = mintpy_engine.build(cap=REGISTRY[9], method="step",
                             params=CHAIN[9]["params"], run=RUN, workspace=workspace)
    assert p7.files["mintpy/insar_agent.cfg"] == p9.files["mintpy/insar_agent.cfg"]
    assert "stepDate    = 20190706T0320" in p7.files["mintpy/insar_agent.cfg"]


def test_tropo_method_switches_cfg(workspace):
    run2 = {"simulated": 0, "chain": {**CHAIN, 8: {"method": "tropo_height_corr",
                                                   "params": {"ramp": "no"}}}}
    cfg = mintpy_engine.render_cfg(run2, this_step=8, this_method="tropo_height_corr",
                                   this_params={"ramp": "no"})
    assert "mintpy.troposphericDelay.method     = height_correlation" in cfg
    assert "mintpy.deramp                       = no" in cfg


def test_mintpy_cfg_fallback_defaults_align_registry(workspace):
    """绕过 planner 的空链渲染(桥接/手工 build):solidEarthTides 回退 no,
    与 registry 第 8 步声明的默认 False 对齐(REVIEW-r2 P2-10 —— conda-forge
    pysolid 的 Fortran DLL 在 Windows 上加载失败,SET 绝不能默认开启);
    stepFuncDate 不再读第 8 步未声明的 step_func_date 参数,回退 auto。"""
    cfg = mintpy_engine.render_cfg({"simulated": 0, "chain": {}},
                                   this_step=7, this_method="mintpy_sbas",
                                   this_params={})
    assert "mintpy.solidEarthTides              = no" in cfg
    assert "mintpy.topographicResidual.stepFuncDate      = auto" in cfg


def test_mintpy_cfg_explicit_set_and_step_date_respected(workspace):
    """显式声明照常生效:SET=yes 走参数,stepFuncDate 跟随第 9 步阶跃日期。"""
    cfg = mintpy_engine.render_cfg(RUN, this_step=8, this_method="tropo_era5_pyaps",
                                   this_params=CHAIN[8]["params"])
    assert "mintpy.solidEarthTides              = yes" in cfg
    assert "mintpy.topographicResidual.stepFuncDate      = 20190706T0320" in cfg


def test_real_mode_routing():
    """真实模式:自建方法路由到真实实现;未实现的显式 ToolMissing,绝不静默造假。"""
    from insar_agent.engines import figures, localdata, qa

    assert resolve_builder(REGISTRY[1], "local_import", simulated=False) is localdata.build
    assert resolve_builder(REGISTRY[1], "nisar_import", simulated=False) is localdata.build
    assert resolve_builder(REGISTRY[10], "figure_journal", simulated=False) is figures.build
    assert resolve_builder(REGISTRY[11], "coherence_mask", simulated=False) is qa.build
    assert resolve_builder(REGISTRY[11], "crossval_ps_sbas", simulated=False) is qa.build
    from insar_agent.engines import dolphin, gnss, mintpy_post, passthrough, predict
    assert resolve_builder(REGISTRY[11], "gnss_compare", simulated=False) is gnss.build
    assert resolve_builder(REGISTRY[7], "dolphin_ps_ds", simulated=False) is dolphin.build
    assert resolve_builder(REGISTRY[21], "passthrough", simulated=False) is passthrough.build
    assert resolve_builder(REGISTRY[23], "asc_desc_horz_vert", simulated=False) is mintpy_post.build
    assert resolve_builder(REGISTRY[27], "extrapolate_fitted", simulated=False) is predict.build
    with pytest.raises(ToolMissing):
        resolve_builder(REGISTRY[5], "none", simulated=False)  # 未实现 → 显式拒绝


def test_simulated_mode_routing():
    from insar_agent.engines import simulate

    for sid, method in [(1, "local_import"), (7, "mintpy_sbas"), (11, "coherence_mask")]:
        assert resolve_builder(REGISTRY[sid], method, simulated=True) is simulate.build


def test_localdata_script_contains_junction_and_era5(workspace):
    from insar_agent.engines import localdata

    plan = localdata.build(cap=REGISTRY[1], method="local_import",
                           params={"source": r"E:\data\Ridgecrest"}, run=RUN,
                           workspace=workspace)
    script = plan.files[".import/import_local.py"]
    assert "mklink" in script and "ERA5.h5" in script
    assert "unw_phase_clipped.tif" in script  # 导入后自检
    assert repr(r"E:\data\Ridgecrest") in script  # source 以 repr 形式渲染进脚本


def test_hyp3_plan_skips_cloud_steps(store):
    """HyP3 路线:2-6 步标 skipped 且不做可行性检查;有 MintPy 即为真实计划。"""
    from insar_agent.planner.plan import make_plan
    from insar_agent.registry.scenarios import scenario_of
    from insar_agent.runtime.probe import ProbeResult

    probe = ProbeResult(
        engines={"mintpy": "1.6.1", "gdal": "present", "isce2": None, "snaphu": None,
                 "snap": None, "pystamps": None, "pyaps": "present"},
        credentials={"earthdata": False, "cds": False, "gacos": False})
    store.create_session("s1", "t")
    plan = make_plan(store, "s1", registry=REGISTRY, probe=probe,
                     scenario=scenario_of("quake"), workspace="ws")
    assert plan.runnable(), plan.problems      # ISCE2/SNAPHU 缺失不阻塞
    assert not plan.simulated                  # 真实计划
    states = {p.step_id: p.state for p in plan.steps}
    assert all(states[s] == "skipped" for s in (2, 3, 4, 5, 6))
    assert states[1] == states[7] == states[11] == "pending"
    methods = {p.step_id: p.method for p in plan.steps}
    assert methods[9] == "step" and methods[11] == "coherence_mask"
    # 落库状态一致
    rows = {s.step_id: s.state for s in store.load_steps(plan.run_id)}
    assert rows[3] == "skipped" and rows[7] == "pending"


def _touch_decomposed(workspace):
    p = workspace / "analysis" / "decomposed.h5"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    return p


def test_spatial_average_wrapper_uses_ut_not_cli(workspace):
    """速度场不能走 mintpy.cli.spatial_average(上游写 txt 必崩);包装脚本内存计算."""
    from insar_agent.engines import mintpy_post

    _touch_decomposed(workspace)
    plan = mintpy_post.build(
        cap=REGISTRY[24], method="spatial_average", params={},
        run={"simulated": 0}, workspace=workspace)
    assert plan.argv[1:] == ["-u", ".analysis/run_post.py"]
    assert plan.cwd == str(workspace)
    assert set(plan.files) == {".analysis/run_post.py", "analysis/.keep"}
    script = plan.files[".analysis/run_post.py"]
    assert "ut.spatial_average" in script
    assert "saveList=False" in script
    assert "atleast_1d" in script
    assert "analysis/measure.json" in script
    assert "mintpy.cli.spatial_average" not in script
    assert "analysis/decomposed.h5" in script
    assert "DATASET = None" in script


def test_spatial_average_bakes_dataset_param(workspace):
    from insar_agent.engines import mintpy_post

    _touch_decomposed(workspace)
    plan = mintpy_post.build(
        cap=REGISTRY[24], method="spatial_average",
        params={"dataset": "vertical"},
        run={"simulated": 0}, workspace=workspace)
    script = plan.files[".analysis/run_post.py"]
    assert "DATASET = 'vertical'" in script
    assert "mintpy.cli.spatial_average" not in script


def test_temporal_average_and_rms_still_use_mintpy_cli(workspace):
    """本缺陷只动 spatial_average;时序类 CLI 包装保持原样."""
    from insar_agent.engines import mintpy_post

    _touch_decomposed(workspace)
    temporal = mintpy_post.build(
        cap=REGISTRY[24], method="temporal_average", params={},
        run={"simulated": 0}, workspace=workspace)
    rms = mintpy_post.build(
        cap=REGISTRY[24], method="timeseries_rms", params={},
        run={"simulated": 0}, workspace=workspace)
    t_script = temporal.files[".analysis/run_post.py"]
    r_script = rms.files[".analysis/run_post.py"]
    assert "mintpy.cli.temporal_average" in t_script
    assert "mintpy.cli.timeseries_rms" in r_script
    assert "mintpy.cli.spatial_average" not in t_script
    assert "ut.spatial_average" not in t_script
    assert temporal.argv[1:] == ["-u", ".analysis/run_post.py"]
