"""MintPy 薄封装:smallbaselineApp 分段命令构建(零决策)。

拆步依据:InSAR-Pro backend/app/engines/mintpy.py 的独立 CLI 子命令调用模式
(AGENT-DESIGN 前置结论 3)。这里用 --start/--end 单命令表达每个 capability
的步骤区间 —— 单 argv,无需 bash,Windows/WSL 双端可跑;引擎内部的
--dostep 级断点由 MintPy 自身的状态文件保证。

cfg 是全链共享配置(MintPy 语义),从 run["chain"](规划器落库的各步方法/参数)
渲染,形状对齐实测配置 RidgecrestSenDT71.txt(§0.5.4 唯一可信真值)。

引擎 Python 解析顺序:INSAR_ENGINE_PYTHON > INSAR_ENGINE_PREFIX/python.exe > 'python'。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from insar_agent.registry.model import Capability
from insar_agent.runtime.jobs import CommandPlan

# MintPy smallbaselineApp 标准步骤序列(1.5/1.6)
# load_data modify_network reference_point quick_overview correct_unwrap_error
# invert_network correct_LOD correct_SET correct_ionosphere correct_troposphere
# deramp correct_topography residual_RMS reference_date velocity geocode ...

# capability id → (--start, --end)
_RANGES: dict[int, tuple[str, str]] = {
    7: ("load_data", "invert_network"),
    8: ("correct_LOD", "reference_date"),
    9: ("velocity", "velocity"),
}

# cfg 必须纯 ASCII:MintPy read_template 不指定编码,中文 Windows 上按 GBK 读会崩
_CFG_TEMPLATE = """\
# rendered by insar-agent (diffable, reproducible; shape follows RidgecrestSenDT71.txt)
mintpy.compute.cluster      = local
mintpy.compute.numWorker    = {num_worker}
mintpy.load.processor       = {processor}
##---------interferogram datasets:
mintpy.load.unwFile         = {unw_file}
mintpy.load.corFile         = {cor_file}
##---------geometry datasets:
mintpy.load.demFile         = {dem_file}
mintpy.load.incAngleFile    = {inc_file}
mintpy.load.waterMaskFile   = {water_file}
##---------subset / reference:
mintpy.subset.lalo          = {subset_lalo}
mintpy.reference.lalo       = {reference_lalo}
##---------network / inversion:
mintpy.network.tempBaseMax  = {temp_base_max}
mintpy.networkInversion.weightFunc  = no
##---------corrections:
mintpy.troposphericDelay.method     = {tropo_method}
mintpy.deramp                       = {ramp}
mintpy.topographicResidual          = {dem_error}
mintpy.topographicResidual.stepFuncDate      = {step_func_date}
mintpy.topographicResidual.pixelwiseGeometry = no
mintpy.solidEarthTides              = {solid_earth_tides}
##---------velocity model:
mintpy.timeFunc.polynomial  = {poly_order}
mintpy.timeFunc.periodic    = {periods}
mintpy.timeFunc.stepDate    = {step_date}
##---------other:
mintpy.plot = no
"""


def engine_python() -> str:
    exe = os.environ.get("INSAR_ENGINE_PYTHON")
    if exe:
        return exe
    prefix = os.environ.get("INSAR_ENGINE_PREFIX")
    if prefix:
        cand = Path(prefix) / ("python.exe" if sys.platform == "win32" else "bin/python")
        if cand.exists():
            return str(cand)
    return "python"


def _chain_params(run: dict, step_id: int, fallback: dict) -> dict:
    chain = run.get("chain") or {}
    entry = chain.get(step_id) or chain.get(str(step_id)) or {}
    return entry.get("params", fallback if entry == {} else {})


def _chain_method(run: dict, step_id: int, fallback: str = "") -> str:
    chain = run.get("chain") or {}
    entry = chain.get(step_id) or chain.get(str(step_id)) or {}
    return entry.get("method", fallback)


def render_cfg(run: dict, *, this_step: int, this_method: str, this_params: dict) -> str:
    """全链 cfg:第 7 步的网络参数 + 第 8 步的校正参数 + 第 9 步的模型参数。"""
    p7 = _chain_params(run, 7, this_params if this_step == 7 else {})
    p8 = _chain_params(run, 8, this_params if this_step == 8 else {})
    p9 = _chain_params(run, 9, this_params if this_step == 9 else {})
    m8 = _chain_method(run, 8, this_method if this_step == 8 else "tropo_era5_pyaps")
    m9 = _chain_method(run, 9, this_method if this_step == 9 else "linear")

    tropo = {"tropo_era5_pyaps": "pyaps", "tropo_gacos": "gacos",
             "tropo_height_corr": "height_correlation"}.get(m8, "pyaps")
    periods = p9.get("periods", []) if m9 == "poly_periodic" else []
    step_date = str(p9.get("step_date", "") or "") if m9 == "step" else ""

    # 数据面(HyP3 路线):相对 mintpy/ 工作目录
    return _CFG_TEMPLATE.format(
        num_worker=int(p7.get("parallel_workers", 4)),
        processor="hyp3",
        unw_file="../hyp3/*/*unw_phase_clipped.tif",
        cor_file="../hyp3/*/*corr_clipped.tif",
        dem_file="../hyp3/*/*dem_clipped.tif",
        inc_file="../hyp3/*/*lv_theta_clipped.tif",
        water_file="../hyp3/*/*water_mask_clipped.tif",
        subset_lalo=os.environ.get("INSAR_SUBSET_LALO", "391e4:400e4,39e4:51e4"),
        reference_lalo=os.environ.get("INSAR_REFERENCE_LALO", "391.5e4,45e4"),
        temp_base_max=p7.get("max_temporal_baseline", 120),
        tropo_method=tropo,
        ramp=p8.get("ramp", "linear"),
        dem_error="yes" if p8.get("dem_error", True) else "no",
        step_func_date=str(p8.get("step_func_date", "") or step_date or "auto"),
        solid_earth_tides="yes" if p8.get("solid_earth_tides", True) else "no",
        poly_order=p9.get("poly_order", 1),
        periods=",".join(str(x) for x in periods) if periods else "auto",
        step_date=step_date or "auto",
    )


def build(*, cap: Capability, method: str, params: dict[str, Any], run: dict,
          workspace: Path) -> CommandPlan:
    start, end = _RANGES.get(cap.id, ("velocity", "velocity"))
    cfg_rel = "mintpy/insar_agent.cfg"  # 全链共享一份 cfg(MintPy 语义)
    cfg = render_cfg(run, this_step=cap.id, this_method=method, this_params=params)
    workers = int(params.get("parallel_workers", params.get("threads", 8)) or 8)
    return CommandPlan(
        argv=[engine_python(), "-u", "-m", "mintpy.cli.smallbaselineApp",
              "insar_agent.cfg", "--start", start, "--end", end],
        cwd=str(workspace / "mintpy"),
        env={
            "HDF5_USE_FILE_LOCKING": "FALSE",   # DESIGN.md:472
            "OMP_NUM_THREADS": str(min(workers, 8)),   # 重型计算管控:限线程
            "MKL_NUM_THREADS": str(min(workers, 8)),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",  # 中文 Windows:强制 UTF-8 默认编码(MintPy 不指定 encoding)
        },
        files={cfg_rel: cfg},
        shell_line=f"python -m mintpy.cli.smallbaselineApp insar_agent.cfg"
                   f" --start {start} --end {end}",
    )
