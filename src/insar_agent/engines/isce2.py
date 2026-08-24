"""ISCE2 薄封装:topsApp/stripmapApp XML 渲染 + 分步执行命令(零决策)。

ISCE2 链是零起点(AGENT-DESIGN 前置结论 2:InSAR-Pro 没有 ISCE2 封装),
本模块只做命令构建;--start/--end 分步是引擎侧断点的基础。

两条链按方法名路由:方法名含 "stripmap"(如 isce2_stripmap_xcorr)走
stripmapApp(ALOS raw 条带链,2026-08 WSL 实测形态),其余走 topsApp(S1 IW)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from insar_agent.registry.model import Capability
from insar_agent.runtime.jobs import CommandPlan

# topsApp 步骤全序列(ISCE2 实际执行顺序;区间表的分段基准)
TOPS_STEPS: tuple[str, ...] = (
    "startup", "preprocess", "computeBaselines", "verifyDEM", "topo",
    "subsetoverlaps", "coarseoffsets", "coarseresamp", "overlapifg", "prepesd",
    "esd", "rangecoreg", "fineoffsets", "fineresamp", "ion", "burstifg",
    "mergebursts", "filter", "unwrap", "unwrap2stage", "geocode",
)

# stripmapApp 步骤全序列(2026-08 ALOS Baja WSL 实测打印顺序,
# docs/VALIDATION-isce2-wsl.md;区间表的分段基准)
STRIPMAP_STEPS: tuple[str, ...] = (
    "startup", "preprocess", "cropraw", "formslc", "cropslc", "verifyDEM",
    "topo", "geo2rdr", "coarse_resample", "misregistration", "refined_resample",
    "rubber_sheet_range", "rubber_sheet_azimuth", "fine_resample",
    "split_range_spectrum", "sub_band_resample", "interferogram",
    "sub_band_interferogram", "filter", "filter_low_band", "filter_high_band",
    "unwrap", "unwrap_low_band", "unwrap_high_band", "ionosphere", "geocode",
)

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

# capability → topsApp 步骤区间(TOPS_STEPS 全序列的分段)。
#
# 硬约束(2026-08 ALOS Baja 实测教训):ISCE2 --steps 续跑要求 pickle 链连续。
# Application 恢复时只加载"起始步骤的直接前驱"的 pickle;若分段之间跳过了任何
# 步骤(哪怕是 do_xxx=False 时的空转占位步),前驱 pickle 不存在,恢复得到空
# 状态,在首个产品引用处以 NoneType 崩溃。因此相邻 capability 的区间必须在
# TOPS_STEPS 全序列上首尾相接(连续性由 tests/test_isce2_stripmap.py 断言)。
_STEP_RANGES: dict[int, tuple[str, str]] = {
    3: ("startup", "fineresamp"),        # 配准:几何配准 + ESD + 精配准
    4: ("ion", "filter"),                # 干涉:ion/burstifg 占位步不可跳过
    5: ("filter", "filter"),             # 滤波(单步重跑;前驱 burstifg pickle 已存在)
}

_STRIPMAP_XML_HEAD = """\
<?xml version="1.0" encoding="UTF-8"?>
<!-- 由 insar-agent 渲染;所有值来自 registry 参数声明,可 diff 可复现。
     形态对齐 2026-08 ALOS Baja WSL 实测(docs/VALIDATION-isce2-wsl.md):
     sensor=ALOS raw,reference/secondary 用 IMAGEFILE/LEADERFILE/output,
     demFilename 指 ISCE 格式 DEM,unwrapper=snaphu 且 do unwrap=True。 -->
<stripmapApp>
  <component name="insar">
    <property name="sensor name">ALOS</property>
    <component name="reference">
      <property name="IMAGEFILE">{reference_image}</property>
      <property name="LEADERFILE">{reference_leader}</property>
      <property name="output">reference</property>
    </component>
    <component name="secondary">
      <property name="IMAGEFILE">{secondary_image}</property>
      <property name="LEADERFILE">{secondary_leader}</property>{resample_line}
      <property name="output">secondary</property>
    </component>
    <property name="demFilename">{dem_path}</property>
    <property name="do unwrap">True</property>
    <property name="unwrapper name">snaphu</property>
  </component>
</stripmapApp>
"""

# capability → stripmapApp 步骤区间(STRIPMAP_STEPS 全序列的分段)。
#
# 与 _STEP_RANGES 同一条硬约束:ISCE2 --steps 续跑只加载"起始步骤直接前驱"的
# pickle,分段之间跳过任何步骤(含 do_xxx=False 的空转占位步)都会拿空状态崩溃,
# 因此相邻 capability 的区间必须在 STRIPMAP_STEPS 全序列上首尾相接。
# 实测教训(docs/VALIDATION-isce2-wsl.md 教训 2):从 filter 停、从 unwrap 起,
# 恢复要找 unwrap 的直接前驱 filter_high_band(分频谱占位步、从未跑过)的 pickle,
# 结果空状态 NoneType 崩溃;正确做法是从上次终点的下一步 filter_low_band 续起,
# 让空转占位步补齐 pickle 链 —— 即 cap6 区间的由来。
# 方法 id 映射(registry 声明,stripmap_coseismic 场景包覆写 3-6 步):
#   3=isce2_stripmap_xcorr,4=isce2_stripmap_ifg,5=isce2_stripmap_filter,
#   6=isce2_stripmap_unwrap_snaphu;路由按方法名含 "stripmap" + cap.id 查本表,
# 连续性与映射核对由 tests/test_isce2_stripmap.py、tests/test_stripmap_scenario.py 断言。
_STRIPMAP_RANGES: dict[int, tuple[str, str]] = {
    3: ("startup", "fine_resample"),        # 配准:raw→SLC→精配准(含 rubber sheet)
    4: ("split_range_spectrum", "filter"),  # 干涉:分频谱占位步不可跳过
    5: ("filter", "filter"),                # 滤波(单步重跑;前驱 sub_band_interferogram pickle 已存在)  # noqa: E501
    6: ("filter_low_band", "geocode"),      # 解缠+地理编码:从 filter 的下一步续起(教训 2)
}


def _stripmap_shared_params(run: dict, params: dict[str, Any]) -> dict[str, Any]:
    """stripmapApp 全链共享一份配置(与 mintpy cfg 同款语义)。

    路径类 science 参数(IMG/LED/dem/resample_flag)只声明在第 3 步(配准);
    4-6 段渲染时从 run["chain"][3](executor 落库的全链快照)取同一份,保证
    四段 XML 同源 —— Baja 预检 dry-run 暴露的缺口:4-6 段各读自己的 params
    会回落到默认相对路径,与第 3 段渲染出两套数据源。本步 params 显式出现的
    键仍最优先(参数试探语义);无 chain 上下文(单测直接调 build)时行为不变。
    """
    chain = run.get("chain") or {}
    entry = chain.get(3) or chain.get("3") or {}
    merged = dict(entry.get("params") or {})
    merged.update(params)
    return merged


def _build_stripmap(*, cap: Capability, params: dict[str, Any], run: dict,
                    workspace: Path) -> CommandPlan:
    """stripmapApp 命令构建(ALOS raw 条带链)。路径参数是 science 输入,来自 registry。"""
    params = _stripmap_shared_params(run, params)
    start, end = _STRIPMAP_RANGES.get(cap.id, ("startup", "geocode"))
    xml_rel = f"isce2/stripmapApp_s{cap.id:02d}.xml"
    script_rel = f"isce2/run_s{cap.id:02d}.sh"
    # RESAMPLE_FLAG 只在声明了重采样时渲染(FBD 从影像配 FBS 主影像用 dual2single);
    # 空值不渲染该属性 —— 与实测 XML 形态一致
    flag = str(params.get("resample_flag", "") or "")
    resample_line = (
        f'\n      <property name="RESAMPLE_FLAG">{escape(flag)}</property>' if flag else ""
    )
    xml = _STRIPMAP_XML_HEAD.format(
        reference_image=escape(str(params.get("reference_image", "data/raw/reference/IMG-HH"))),
        reference_leader=escape(str(params.get("reference_leader", "data/raw/reference/LED"))),
        secondary_image=escape(str(params.get("secondary_image", "data/raw/secondary/IMG-HH"))),
        secondary_leader=escape(str(params.get("secondary_leader", "data/raw/secondary/LED"))),
        resample_line=resample_line,
        dem_path=escape(str(params.get("dem_path", "data/dem/dem.wgs84"))),
    )
    # 命令形态对拍手工验证链(docs/VALIDATION-isce2-wsl.md 的 .job/cmd.sh):
    #   --steps 必须显式给出 —— ISCE2 Application 只在步进模式下才解析 --start/--end,
    #   漏掉它每个分段都会从头跑整条链(预检 dry-run 与手工 cmd.sh 对拍暴露的缺口);
    #   nice -n 10 与手工链一致(重型计算管控:降优先级,不占满宿主 CPU)。
    script = (
        "#!/usr/bin/env bash\nset -euo pipefail\ncd isce2\n"
        f"nice -n 10 stripmapApp.py {Path(xml_rel).name} --steps --start={start} --end={end}\n"
    )
    threads = int(params.get("threads", 8))
    return CommandPlan(
        argv=["bash", script_rel],
        cwd=str(workspace),
        env={"OMP_NUM_THREADS": str(threads)},  # 资源参数:进 env 不进指纹(§5.4)
        files={xml_rel: xml, script_rel: script},
        shell_line=f"bash {script_rel}",
    )


def build(*, cap: Capability, method: str, params: dict[str, Any], run: dict,
          workspace: Path) -> CommandPlan:
    # 方法名含 stripmap → ALOS raw 条带链;其余方法保持原 topsApp 路径不变
    if "stripmap" in method:
        return _build_stripmap(cap=cap, params=params, run=run, workspace=workspace)
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
