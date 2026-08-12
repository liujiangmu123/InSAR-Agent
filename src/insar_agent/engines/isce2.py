"""ISCE2 薄封装:topsApp.xml 渲染 + 分步执行命令(零决策)。

ISCE2 链是零起点(AGENT-DESIGN 前置结论 2:InSAR-Pro 没有 ISCE2 封装),
本模块只做命令构建;topsApp 的 --start/--end 分步是引擎侧断点的基础。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from insar_agent.registry.model import Capability
from insar_agent.runtime.jobs import CommandPlan

_TOPS_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<!-- 由 insar-agent 渲染;所有值来自 registry 参数声明,可 diff 可复现 -->
<topsApp>
  <component name="topsinsar">
    <property name="Sensor name">SENTINEL1</property>
    <component name="reference">
      <property name="safe">data/slc/reference.SAFE</property>
      <property name="orbit directory">data/orbits</property>
    </component>
    <component name="secondary">
      <property name="safe">data/slc/secondary.SAFE</property>
      <property name="orbit directory">data/orbits</property>
    </component>
    <property name="demFilename">data/dem/dem.wgs84</property>
    <property name="do ESD">True</property>
    <property name="ESD coherence threshold">{esd}</property>
    <property name="range looks">{range_looks}</property>
    <property name="azimuth looks">{azimuth_looks}</property>
    <property name="filter strength">{filter_strength}</property>
    <property name="do unwrap">False</property>
  </component>
</topsApp>
"""

# capability → topsApp 步骤区间(startup..geocode 全序列的分段)。
#
# 硬约束(2026-08 ALOS Baja 实测教训):ISCE2 --steps 续跑要求 pickle 链连续。
# Application 恢复时只加载"起始步骤的直接前驱"的 pickle;若分段之间跳过了任何
# 步骤(哪怕是 do_xxx=False 时的空转占位步),前驱 pickle 不存在,恢复得到空
# 状态,在首个产品引用处以 NoneType 崩溃。因此相邻 capability 的区间必须在
# topsApp 步骤全序列上首尾相接:
#   startup preprocess computeBaselines verifyDEM topo subsetoverlaps
#   coarseoffsets coarseresamp overlapifg prepesd esd rangecoreg fineoffsets
#   fineresamp ion burstifg mergebursts filter unwrap unwrap2stage geocode
_STEP_RANGES: dict[int, tuple[str, str]] = {
    3: ("startup", "fineresamp"),        # 配准:几何配准 + ESD + 精配准
    4: ("ion", "filter"),                # 干涉:ion/burstifg 占位步不可跳过
    5: ("filter", "filter"),             # 滤波(单步重跑;前驱 burstifg pickle 已存在)
}


def build(*, cap: Capability, method: str, params: dict[str, Any], run: dict,
          workspace: Path) -> CommandPlan:
    start, end = _STEP_RANGES.get(cap.id, ("startup", "geocode"))
    xml_rel = f"isce2/topsApp_s{cap.id:02d}.xml"
    script_rel = f"isce2/run_s{cap.id:02d}.sh"
    xml = _TOPS_XML.format(
        esd=params.get("esd_coherence_threshold", 0.85),
        range_looks=params.get("range_looks", 10),
        azimuth_looks=params.get("azimuth_looks", 2),
        filter_strength=params.get("filter_strength", 0.5),
    )
    script = (
        "#!/usr/bin/env bash\nset -euo pipefail\ncd isce2\n"
        f"topsApp.py {Path(xml_rel).name} --start={start} --end={end}\n"
    )
    threads = int(params.get("threads", 8))
    return CommandPlan(
        argv=["bash", script_rel],
        cwd=str(workspace),
        env={"OMP_NUM_THREADS": str(threads)},  # 资源参数:进 env 不进指纹(§5.4)
        files={xml_rel: xml, script_rel: script},
        shell_line=f"bash {script_rel}",
    )
