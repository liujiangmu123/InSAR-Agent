"""engines:薄封装·零决策(AGENT-DESIGN §2)。

每个引擎模块只提供 build(cap, method, params, run, workspace) -> CommandPlan,
不做任何决策 —— 方法/参数选择在 planner/brain,执行在 runtime,校验在 audit。

真实引擎缺失时(本机 WSL 未装,§0.5.1),run.simulated=1 走 simulate 构建器:
产出合成产物并在日志/证据上显式标注,证据阶梯封顶 runnable(取最差原则)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from insar_agent.registry.model import Capability
from insar_agent.runtime.jobs import CommandPlan


class ToolMissing(RuntimeError):
    """引擎不可用(§4.12 TOOL_MISSING:停链,报环境问题)。"""


def resolve_builder(cap: Capability, method_id: str, *, simulated: bool):
    """路由到构建器:passthrough/register_sources 永远物化真文件;
    其余 simulated 走合成;真实模式先按方法特判,再按引擎。

    真实模式下没有实现的方法必须显式 ToolMissing —— 绝不静默回退到合成构建器
    往真实 run 里注入假产物。
    """
    from insar_agent.engines import simulate

    # 规范路径物化:真实文件硬链接/拷贝,绝不走 simulate(禁止注入合成产物)
    if method_id in ("passthrough", "register_sources"):
        from insar_agent.engines import passthrough
        return passthrough.build
    if method_id == "extrapolate_fitted":
        from insar_agent.engines import predict
        return predict.build

    if simulated:
        return simulate.build

    # 方法级路由(自建纯 Python 实现)
    # dem_local:本地/WSL DEM 核验登记(Baja 预检发现真实 run 里 ToolMissing 的缺口)
    if method_id in ("local_import", "dem_local"):
        from insar_agent.engines import localdata
        return localdata.build
    if method_id == "figure_journal":
        from insar_agent.engines import figures
        return figures.build
    if method_id in ("coherence_mask", "crossval_ps_sbas", "loop_closure"):
        from insar_agent.engines import qa
        return qa.build
    if method_id in ("asc_desc_horz_vert", "raster_diff", "mask_by_coherence",
                     "subset_lalo", "spatial_average", "temporal_average",
                     "transection", "timeseries_rms", "plate_motion_itrf",
                     "view_snapshot", "transection_figure", "kmz", "kmz_timeseries",
                     "epoch_diff", "quadratic_accel", "velocity_compare",
                     "bridge_gbis", "bridge_kite", "bridge_gmt", "bridge_qgis",
                     "bridge_hdfeos5"):
        from insar_agent.engines import mintpy_post
        return mintpy_post.build

    method = cap.method(method_id)
    engine = method.engine if method else "-"
    if engine in ("-", ""):
        raise ToolMissing(f"方法 {method_id} 尚无真实实现(演示请用模拟模式)")
    if engine == "mintpy":
        from insar_agent.engines import mintpy
        return mintpy.build
    if engine == "isce2":
        from insar_agent.engines import isce2
        return isce2.build
    if engine == "snaphu":
        from insar_agent.engines import snaphu
        return snaphu.build
    if engine == "pystamps":
        from insar_agent.engines import pystamps
        return pystamps.build
    if engine in ("hyp3", "asf_api"):
        from insar_agent.engines import hyp3
        return hyp3.build
    if engine == "pyaps":
        from insar_agent.engines import pyaps
        return pyaps.build
    raise ToolMissing(f"引擎 {engine} 无构建器")


def default_builder(*, cap: Capability, method: str, params: dict[str, Any],
                    run: dict, workspace: Path) -> CommandPlan:
    """executor 的默认 Builder:按 run.simulated 路由。"""
    builder = resolve_builder(cap, method, simulated=bool(run.get("simulated")))
    return builder(cap=cap, method=method, params=params, run=run, workspace=workspace)
