"""第 7 步数据面参数化:默认 cfg 与参数化前逐字节相同;processor/模式覆写穿透。

LEGACY_DEFAULT_CFG 是参数化前(空链 + 默认 this_params)的渲染快照,不得手改;
行为变了就该让本测试变红。
"""

from __future__ import annotations

from insar_agent.engines import mintpy as mintpy_engine
from insar_agent.registry.capabilities import REGISTRY

# 参数化前空链默认渲染(da901f3).环境变量 INSAR_SUBSET_LALO / INSAR_REFERENCE_LALO
# 未设置时的回落值已写入快照;测试里显式清掉这两项以免宿主环境污染。
LEGACY_DEFAULT_CFG = """\
# rendered by insar-agent (diffable, reproducible; shape follows RidgecrestSenDT71.txt)
mintpy.compute.cluster      = local
mintpy.compute.numWorker    = 4
mintpy.load.processor       = hyp3
##---------interferogram datasets:
mintpy.load.unwFile         = ../hyp3/*/*unw_phase_clipped.tif
mintpy.load.corFile         = ../hyp3/*/*corr_clipped.tif
##---------geometry datasets:
mintpy.load.demFile         = ../hyp3/*/*dem_clipped.tif
mintpy.load.incAngleFile    = ../hyp3/*/*lv_theta_clipped.tif
mintpy.load.waterMaskFile   = ../hyp3/*/*water_mask_clipped.tif
##---------subset / reference:
mintpy.subset.lalo          = 391e4:400e4,39e4:51e4
mintpy.reference.lalo       = 391.5e4,45e4
##---------network / inversion:
mintpy.network.tempBaseMax  = 120
mintpy.network.perpBaseMax  = no
mintpy.networkInversion.weightFunc  = no
##---------corrections:
mintpy.unwrapError.method           = no
mintpy.ionosphericDelay.method      = no
mintpy.troposphericDelay.method     = pyaps
mintpy.deramp                       = no
mintpy.topographicResidual          = yes
mintpy.topographicResidual.stepFuncDate      = auto
mintpy.topographicResidual.pixelwiseGeometry = no
mintpy.solidEarthTides              = no
##---------velocity model:
mintpy.timeFunc.polynomial  = 1
mintpy.timeFunc.periodic    = auto
mintpy.timeFunc.stepDate    = auto
##---------velocity uncertainty:
mintpy.timeFunc.uncertaintyQuantification = residue
mintpy.timeFunc.bootstrapCount            = 400
##---------other:
mintpy.plot = no
"""

PROCESSORS = ("hyp3", "isce", "aria", "gamma", "gmtsar", "snap", "roipac", "nisar")

_DATA_PLANE_DEFAULTS = {
    "processor": "hyp3",
    "unw_pattern": "../hyp3/*/*unw_phase_clipped.tif",
    "cor_pattern": "../hyp3/*/*corr_clipped.tif",
    "dem_pattern": "../hyp3/*/*dem_clipped.tif",
    "inc_pattern": "../hyp3/*/*lv_theta_clipped.tif",
    "water_pattern": "../hyp3/*/*water_mask_clipped.tif",
}


def _clear_subset_env(monkeypatch):
    monkeypatch.delenv("INSAR_SUBSET_LALO", raising=False)
    monkeypatch.delenv("INSAR_REFERENCE_LALO", raising=False)


def test_cfg_data_plane_default_is_byte_identical_to_legacy(monkeypatch):
    """参数化后,默认渲染的 cfg 与参数化前逐字节相同(不破坏既有 run 语义)。"""
    _clear_subset_env(monkeypatch)
    cfg = mintpy_engine.render_cfg(
        {"simulated": 0, "chain": {}},
        this_step=7, this_method="mintpy_sbas", this_params={})
    assert cfg == LEGACY_DEFAULT_CFG
    # 新计划会把 registry 默认值写进 chain:同样必须逐字节相同
    cfg2 = mintpy_engine.render_cfg(
        {"simulated": 0},
        this_step=7, this_method="mintpy_sbas",
        this_params=REGISTRY[7].default_params())
    assert cfg2 == LEGACY_DEFAULT_CFG


def test_cfg_data_plane_params_declared_with_legacy_defaults():
    """第 7 步数据面参数进指纹(science),默认值与今日硬编码逐字相同。"""
    cap = REGISTRY[7]
    assert cap.params["processor"].enum == PROCESSORS
    assert cap.params["processor"].kind == "science"
    for key, want in _DATA_PLANE_DEFAULTS.items():
        spec = cap.params[key]
        assert spec.default == want, f"{key}: {spec.default!r} != {want!r}"
        assert spec.kind == "science" and spec.type == "str"
        assert spec.validate(want) is None
    err = cap.validate_params({"processor": "not-a-processor"})
    assert "processor" in err and "hyp3" in err["processor"] and "isce" in err["processor"]


def test_mintpy_fallbacks_match_registry_defaults():
    """引擎回退值与 registry 默认同源,避免两处各写一份 HyP3 布局。"""
    assert mintpy_engine._DEFAULT_PROCESSOR == _DATA_PLANE_DEFAULTS["processor"]
    assert mintpy_engine._DEFAULT_UNW_PATTERN == _DATA_PLANE_DEFAULTS["unw_pattern"]
    assert mintpy_engine._DEFAULT_COR_PATTERN == _DATA_PLANE_DEFAULTS["cor_pattern"]
    assert mintpy_engine._DEFAULT_DEM_PATTERN == _DATA_PLANE_DEFAULTS["dem_pattern"]
    assert mintpy_engine._DEFAULT_INC_PATTERN == _DATA_PLANE_DEFAULTS["inc_pattern"]
    assert mintpy_engine._DEFAULT_WATER_PATTERN == _DATA_PLANE_DEFAULTS["water_pattern"]


def test_old_run_chain_without_data_plane_keys_keeps_hyp3(monkeypatch):
    """既有 run 的 chain[7] 只有网络参数、没有 processor 键 → 仍渲染 HyP3。"""
    _clear_subset_env(monkeypatch)
    params = {"network": "small_baseline", "max_temporal_baseline": 120,
              "parallel_workers": 4}
    cfg = mintpy_engine.render_cfg(
        {"simulated": 0, "chain": {7: {"method": "mintpy_sbas", "params": params}}},
        this_step=7, this_method="mintpy_sbas", this_params=params)
    assert "mintpy.load.processor       = hyp3" in cfg
    assert "mintpy.load.unwFile         = ../hyp3/*/*unw_phase_clipped.tif" in cfg
    assert "mintpy.load.corFile         = ../hyp3/*/*corr_clipped.tif" in cfg
    assert "mintpy.load.demFile         = ../hyp3/*/*dem_clipped.tif" in cfg
    assert "mintpy.load.incAngleFile    = ../hyp3/*/*lv_theta_clipped.tif" in cfg
    assert "mintpy.load.waterMaskFile   = ../hyp3/*/*water_mask_clipped.tif" in cfg
    # 网络参数仍生效,且整份 cfg 除数据面外与空链默认一致
    assert "mintpy.network.tempBaseMax  = 120" in cfg


def test_processor_and_patterns_override_penetrates_cfg():
    """8 种 processor 布局 + 文件模式覆写正确穿透到 cfg。"""
    for proc in PROCESSORS:
        patterns = {
            "processor": proc,
            "unw_pattern": f"../{proc}/*/*unw.tif",
            "cor_pattern": f"../{proc}/*/*cor.tif",
            "dem_pattern": f"../{proc}/*/*dem.tif",
            "inc_pattern": f"../{proc}/*/*inc.tif",
            "water_pattern": f"../{proc}/*/*water.tif",
        }
        assert not REGISTRY[7].validate_params({"processor": proc})
        cfg = mintpy_engine.render_cfg(
            {"simulated": 0, "chain": {7: {"method": "mintpy_sbas", "params": patterns}}},
            this_step=8, this_method="tropo_era5_pyaps", this_params={})
        assert f"mintpy.load.processor       = {proc}" in cfg
        assert f"mintpy.load.unwFile         = ../{proc}/*/*unw.tif" in cfg
        assert f"mintpy.load.corFile         = ../{proc}/*/*cor.tif" in cfg
        assert f"mintpy.load.demFile         = ../{proc}/*/*dem.tif" in cfg
        assert f"mintpy.load.incAngleFile    = ../{proc}/*/*inc.tif" in cfg
        assert f"mintpy.load.waterMaskFile   = ../{proc}/*/*water.tif" in cfg


def test_isce_stripmap_layout_example_penetrates():
    """ISCE2 本地链布局(VALIDATION 实测路径)落参数即可,不改 HyP3 默认。"""
    params = {
        "processor": "isce",
        "unw_pattern": "../isce2/interferogram/filt_topophase.unw.geo",
        "cor_pattern": "../isce2/interferogram/phsig.cor",
        "dem_pattern": "/home/insar/work/baja/dem/dem.wgs84",
    }
    cfg = mintpy_engine.render_cfg(
        {"simulated": 0, "chain": {7: {"method": "mintpy_sbas", "params": params}}},
        this_step=7, this_method="mintpy_sbas", this_params=params)
    assert "mintpy.load.processor       = isce" in cfg
    assert "mintpy.load.unwFile         = ../isce2/interferogram/filt_topophase.unw.geo" in cfg
    assert "mintpy.load.corFile         = ../isce2/interferogram/phsig.cor" in cfg
    assert "mintpy.load.demFile         = /home/insar/work/baja/dem/dem.wgs84" in cfg
    # 未覆写的入射角/水掩膜仍走 HyP3 默认(VALIDATION 未列出这两项,不编造路径)
    assert "mintpy.load.incAngleFile    = ../hyp3/*/*lv_theta_clipped.tif" in cfg
    assert "mintpy.load.waterMaskFile   = ../hyp3/*/*water_mask_clipped.tif" in cfg
