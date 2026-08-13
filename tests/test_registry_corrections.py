"""注册表修正 C3-C8 验收(reference/RESEARCH-insar-step-knowledge-2026-08.md §12)。

C1(ramp 默认 no)已另行落地、C2(alpha/filter_strength 合并)涉及参数合并设计暂缓,
本文件只锁 C3-C8 的落地事实:

  C3  stripmap 多视比倒置的生效路径:场景覆写 → make_plan → 步骤参数(az>rg 约 2:1);
      S1(tops)默认 10×2 不动 —— 非 stripmap 计划的指纹不受影响
  C4  第 1 步磁盘估算 ≈8 GB/景(双极化 IW SLC 未压缩;下载+解压峰值由乘子另计)
  C5  scenes hint 场景分层(同震单对=2;SBAS ≥15-20;PS ≥20-25)
  C6  max_perp_baseline 参数存在 + mintpy cfg 渲染(0/未设 → no,对齐上游 auto)
  C7  network 枚举 star/sequential 护栏 hint(Ansari 2021 fading 偏差)
  C8  cmap hint 分色带 + figures 按产物类型(h5 FILE_TYPE)路由默认色带,显式指定直通

不执行任何真实引擎:只做声明检查、计划组装与脚本/cfg 渲染。
"""

from __future__ import annotations

from insar_agent.engines import figures as figures_engine
from insar_agent.engines import mintpy as mintpy_engine
from insar_agent.planner.plan import make_plan
from insar_agent.registry.capabilities import REGISTRY
from insar_agent.registry.scenarios import scenario_of
from insar_agent.runtime.probe import ProbeResult


def _probe_isce2() -> ProbeResult:
    """假 probe(与 test_stripmap_scenario 同款):isce2/mintpy/pyaps 可用,组链够用。"""
    return ProbeResult(
        engines={"isce2": "2.6.5", "mintpy": "1.6.4", "snaphu": None, "gdal": None,
                 "snap": None, "pystamps": None, "pyaps": "present"},
        credentials={"earthdata": False, "cds": False, "gacos": False})


# ---------------- C3:stripmap 多视比倒置(场景覆写生效路径) ----------------

def test_c3_pack_overrides_looks_inverted():
    """场景包覆写声明:az:rg = 2:1 倒置(ALOS FBS 方位 ~3.2 m < 地面距离 ~7-8 m)。"""
    p4 = scenario_of("stripmap_coseismic").step_overrides[4]["params"]
    assert (p4["range_looks"], p4["azimuth_looks"]) == (2, 4)
    assert p4["azimuth_looks"] == 2 * p4["range_looks"]


def test_c3_effective_path_make_plan(store):
    """生效路径:场景覆写 → make_plan → 落库步骤参数(进新计划指纹的就是这份)。"""
    store.create_session("s-c3", "t")
    plan = make_plan(store, "s-c3", registry=REGISTRY, probe=_probe_isce2(),
                     scenario=scenario_of("stripmap_coseismic"), workspace="ws")
    assert plan.runnable(), plan.problems
    step4 = next(p for p in plan.steps if p.step_id == 4)
    assert step4.method == "isce2_stripmap_ifg"
    assert step4.params["azimuth_looks"] > step4.params["range_looks"]  # 比例倒置
    assert (step4.params["range_looks"], step4.params["azimuth_looks"]) == (2, 4)
    assert store.load_step(plan.run_id, 4).params["azimuth_looks"] == 4


def test_c3_s1_default_and_hints_untouched():
    """S1(tops)默认保持 10×2:既有非 stripmap 计划的指纹语义不变;hint 注明比例依据。"""
    p = REGISTRY[4].default_params()
    assert (p["range_looks"], p["azimuth_looks"]) == (10, 2)
    assert "S1 IW" in REGISTRY[4].params["range_looks"].hint
    assert "倒置" in REGISTRY[4].params["range_looks"].hint
    assert "2:1" in REGISTRY[4].params["azimuth_looks"].hint


# ---------------- C4:第 1 步磁盘估算 ≈8 GB/景 ----------------

def test_c4_disk_estimate_8gb_per_scene():
    cap = REGISTRY[1]
    assert cap.disk.estimate_gb(scenes=1) == 8    # 双极化 IW SLC 未压缩 ≈7-8 GB/景
    assert cap.disk.estimate_gb(scenes=7) == 56   # 旧值 2.4 低估 2-3×
    # 下载+解压峰值另计:zip(~4-4.5 GB)与解压产物并存 ≈12.5 GB/景,乘子须覆盖
    assert cap.disk.estimate_gb(scenes=1) * cap.disk.peak_multiplier >= 12.5


# ---------------- C5:scenes hint 场景分层 ----------------

def test_c5_scenes_hint_layered():
    hint = REGISTRY[1].params["scenes"].hint
    for kw in ("同震", "SBAS", "PS", "15-20", "20-25", "Crosetto", "Berardino"):
        assert kw in hint, f"scenes hint 缺关键词 {kw!r}:{hint}"
    assert REGISTRY[1].params["scenes"].min == 2  # 同震单对下限不动


# ---------------- C6:max_perp_baseline 参数 + cfg 渲染 ----------------

def test_c6_param_declared():
    p = REGISTRY[7].params["max_perp_baseline"]
    assert p.kind == "science" and p.type == "int"
    assert p.default == 0 and p.min == 0          # 0=不限,对齐 MintPy 上游 auto(no)
    for kw in ("130", "1800"):                    # ERS 级 / L 波段 ALOS 场景参考
        assert kw in p.hint, f"max_perp_baseline hint 缺场景参考 {kw}:{p.hint}"


def test_c6_cfg_renders_no_when_unset_or_zero():
    """未设(旧 run 的 chain 无此参数)与默认 0 都渲染 no —— cfg 语义与上游一致。"""
    cfg = mintpy_engine.render_cfg({"simulated": 0, "chain": {}},
                                   this_step=7, this_method="mintpy_sbas", this_params={})
    assert "mintpy.network.perpBaseMax  = no" in cfg
    cfg = mintpy_engine.render_cfg({"simulated": 0}, this_step=7, this_method="mintpy_sbas",
                                   this_params=REGISTRY[7].default_params())
    assert "mintpy.network.perpBaseMax  = no" in cfg


def test_c6_cfg_renders_value_when_set():
    params = dict(REGISTRY[7].default_params(), max_perp_baseline=130)
    assert not REGISTRY[7].validate_params({"max_perp_baseline": 130})  # 声明校验通过
    cfg = mintpy_engine.render_cfg({"simulated": 0}, this_step=7, this_method="mintpy_sbas",
                                   this_params=params)
    assert "mintpy.network.perpBaseMax  = 130" in cfg
    # 全链共享 cfg:第 8 步渲染时从 run["chain"][7] 取同一份网络参数
    run = {"simulated": 0, "chain": {7: {"method": "mintpy_sbas",
                                         "params": {"max_perp_baseline": 1800}}}}
    cfg = mintpy_engine.render_cfg(run, this_step=8, this_method="tropo_era5_pyaps",
                                   this_params={})
    assert "mintpy.network.perpBaseMax  = 1800" in cfg


# ---------------- C7:network 枚举护栏 hint ----------------

def test_c7_network_guardrail_hint():
    p = REGISTRY[7].params["network"]
    assert p.enum == ("small_baseline", "star", "sequential")  # 枚举本身不动
    for kw in ("star", "PS", "fading", "长基线", "Ansari"):
        assert kw in p.hint, f"network hint 缺护栏关键词 {kw!r}:{p.hint}"


# ---------------- C8:cmap 分色带 hint + figures 路由 ----------------

def test_c8_cmap_hint_per_product_type():
    hint = REGISTRY[10].params["cmap"].hint
    for kw in ("vik", "batlow", "romaO"):
        assert kw in hint, f"cmap hint 缺分色带建议 {kw}:{hint}"


def _figure_script(workspace, params) -> str:
    plan = figures_engine.build(cap=REGISTRY[10], method="figure_journal", params=params,
                                run={"simulated": 0}, workspace=workspace)
    return plan.files[".report/make_figures.py"]


def test_c8_default_roma_routes_by_file_type(workspace):
    """默认 roma=未显式指定:脚本按 FILE_TYPE 路由(velocity 主图仍 vik,旧语义不变)。"""
    script = _figure_script(workspace, REGISTRY[10].default_params())
    for pair in ('"velocity": "vik"', '"coherence": "batlow"',
                 '"temporalCoherence": "batlow"', '"wrapPhase": "romaO"'):
        assert pair in script, f"路由表缺 {pair}"
    assert "if True else 'vik'" in script      # AUTO=True:走 FILE_TYPE 路由,兜底 vik
    assert "'cmap': 'vik'" in script           # sidecar params 摘要与 velocity 路由一致


def test_c8_explicit_cmap_respected(workspace):
    """显式指定 cmap 时直通用户值,不做路由。"""
    script = _figure_script(workspace, dict(REGISTRY[10].default_params(), cmap="batlow"))
    assert "if False else 'batlow'" in script  # AUTO=False:直通显式值
    assert "'cmap': 'batlow'" in script
