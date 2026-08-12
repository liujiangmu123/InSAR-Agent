"""stripmapApp(ALOS raw 条带链)命令构建测试 + ISCE2 步骤区间连续性断言。

对拍基准:docs/VALIDATION-isce2-wsl.md(2026-08 ALOS Baja WSL 实测)。
不执行引擎,只验证 XML 渲染、CommandPlan 与步骤区间表(pickle 链连续性)。
"""

from __future__ import annotations

import pytest

from insar_agent.engines import resolve_builder
from insar_agent.engines import isce2 as isce2_engine
from insar_agent.engines.isce2 import (
    _STEP_RANGES,
    _STRIPMAP_RANGES,
    STRIPMAP_STEPS,
    TOPS_STEPS,
)
from insar_agent.registry.capabilities import REGISTRY

RUN = {"simulated": 0}


# ---------- 通用区间连续性断言(pickle 链约束) ----------

def assert_ranges_contiguous(sequence, ranges):
    """断言区间表在步骤全序列上首尾相接、无跳步。

    ISCE2 --steps 续跑恢复时只加载"起始步骤直接前驱"的 pickle,因此:
    - 首段必须从全序列第一步起(第一步没有前驱,才不需要 pickle);
    - 下一段起点最多到上一段终点的下一步(跳过任何步骤,含 do_xxx=False 的
      空转占位步,都意味着前驱 pickle 缺失,恢复得到空状态后 NoneType 崩溃);
    - 链条单调推进(允许回头单步重跑,如滤波段 filter..filter)。
    """
    idx = {name: i for i, name in enumerate(sequence)}
    items = sorted(ranges.items())
    assert items, "区间表不能为空"
    for cap_id, (start, end) in items:
        assert start in idx, f"cap{cap_id} 起点 {start!r} 不在全序列中"
        assert end in idx, f"cap{cap_id} 终点 {end!r} 不在全序列中"
        assert idx[start] <= idx[end], f"cap{cap_id} 区间反向:{start} → {end}"
    first_cap, (first_start, _) = items[0]
    assert idx[first_start] == 0, (
        f"cap{first_cap} 必须从全序列第一步 {sequence[0]!r} 起,实际 {first_start!r}")
    for (prev_cap, (_, prev_end)), (next_cap, (next_start, next_end)) in zip(items, items[1:]):
        assert idx[next_start] <= idx[prev_end] + 1, (
            f"cap{prev_cap}→cap{next_cap} 跳步:{prev_end} 之后直接跳到 {next_start},"
            "中间步骤的 pickle 不会生成,续跑必崩")
        assert idx[next_end] >= idx[prev_end], (
            f"cap{next_cap} 终点 {next_end} 早于 cap{prev_cap} 终点 {prev_end},链条必须单调推进")


def test_tops_ranges_contiguous():
    assert_ranges_contiguous(TOPS_STEPS, _STEP_RANGES)


def test_stripmap_ranges_contiguous():
    assert_ranges_contiguous(STRIPMAP_STEPS, _STRIPMAP_RANGES)


def test_contiguity_assertion_catches_skipped_steps():
    """断言函数必须能抓住实测踩过的两类断链。"""
    # 修复前的 topsApp 缺陷:cap4 从 mergebursts 起跳,跳过 ion/burstifg 占位步
    broken_tops = {**_STEP_RANGES, 4: ("mergebursts", "filter")}
    with pytest.raises(AssertionError, match="跳步"):
        assert_ranges_contiguous(TOPS_STEPS, broken_tops)
    # 实测教训 2:解缠段从 unwrap 起,跳过 filter_low_band/filter_high_band 占位步
    broken_stripmap = {**_STRIPMAP_RANGES, 6: ("unwrap", "geocode")}
    with pytest.raises(AssertionError, match="跳步"):
        assert_ranges_contiguous(STRIPMAP_STEPS, broken_stripmap)


def test_contiguity_assertion_requires_start_at_first_step():
    """首段不从第一步起 → 起始步骤前驱 pickle 不存在,必须拦下。"""
    with pytest.raises(AssertionError, match="第一步"):
        assert_ranges_contiguous(TOPS_STEPS, {3: ("preprocess", "fineresamp")})


# ---------- stripmapApp XML 渲染(关键属性逐个断言,不做整文件比对) ----------

# ALOS Baja 实测数据的参数形态(FBS 主影像 + FBD 从影像)
STRIPMAP_PARAMS = {
    "reference_image": "data/raw/reference/IMG-HH-ALPSRP207600640-H1.0__A",
    "reference_leader": "data/raw/reference/LED-ALPSRP207600640-H1.0__A",
    "secondary_image": "data/raw/secondary/IMG-HH-ALPSRP227730640-H1.0__A",
    "secondary_leader": "data/raw/secondary/LED-ALPSRP227730640-H1.0__A",
    "resample_flag": "dual2single",
    "dem_path": "data/dem/dem.wgs84",
    "threads": 8,
}


def _stripmap_plan(workspace, cap_id=3, params=None):
    return isce2_engine.build(
        cap=REGISTRY[cap_id], method="isce2_stripmap_xcorr",
        params=STRIPMAP_PARAMS if params is None else params,
        run=RUN, workspace=workspace)


def test_stripmap_xml_matches_measured_form(workspace):
    """XML 关键属性与实测可用形态一致(VALIDATION-isce2-wsl.md)。"""
    xml = _stripmap_plan(workspace).files["isce2/stripmapApp_s03.xml"]
    assert '<component name="insar">' in xml
    assert '<property name="sensor name">ALOS</property>' in xml
    # reference/secondary 组件用 IMAGEFILE/LEADERFILE/output 属性
    assert ('<property name="IMAGEFILE">'
            "data/raw/reference/IMG-HH-ALPSRP207600640-H1.0__A</property>") in xml
    assert ('<property name="LEADERFILE">'
            "data/raw/reference/LED-ALPSRP207600640-H1.0__A</property>") in xml
    assert ('<property name="IMAGEFILE">'
            "data/raw/secondary/IMG-HH-ALPSRP227730640-H1.0__A</property>") in xml
    assert ('<property name="LEADERFILE">'
            "data/raw/secondary/LED-ALPSRP227730640-H1.0__A</property>") in xml
    assert xml.count('<property name="output">reference</property>') == 1
    assert xml.count('<property name="output">secondary</property>') == 1
    # FBD 从影像:RESAMPLE_FLAG=dual2single
    assert '<property name="RESAMPLE_FLAG">dual2single</property>' in xml
    # demFilename 指 ISCE 格式 DEM;snaphu 解缠开启
    assert '<property name="demFilename">data/dem/dem.wgs84</property>' in xml
    assert '<property name="do unwrap">True</property>' in xml
    assert '<property name="unwrapper name">snaphu</property>' in xml


def test_stripmap_resample_flag_only_on_secondary(workspace):
    """RESAMPLE_FLAG 只渲染在 secondary 组件内(FBD 从影像),reference 不带。"""
    xml = _stripmap_plan(workspace).files["isce2/stripmapApp_s03.xml"]
    assert xml.count("RESAMPLE_FLAG") == 1
    assert "RESAMPLE_FLAG" in xml.split('<component name="secondary">')[1]


def test_stripmap_resample_flag_omitted_when_empty(workspace):
    """空 resample_flag → 不渲染该属性(与实测 XML 形态一致)。"""
    plan = _stripmap_plan(workspace, params={**STRIPMAP_PARAMS, "resample_flag": ""})
    assert "RESAMPLE_FLAG" not in plan.files["isce2/stripmapApp_s03.xml"]


def test_stripmap_defaults_are_reasonable(workspace):
    """不传参也能渲染:默认相对路径落在工作区数据布局下,缺省不重采样。"""
    xml = _stripmap_plan(workspace, params={}).files["isce2/stripmapApp_s03.xml"]
    assert '<property name="IMAGEFILE">data/raw/reference/IMG-HH</property>' in xml
    assert '<property name="LEADERFILE">data/raw/reference/LED</property>' in xml
    assert '<property name="IMAGEFILE">data/raw/secondary/IMG-HH</property>' in xml
    assert '<property name="demFilename">data/dem/dem.wgs84</property>' in xml
    assert "RESAMPLE_FLAG" not in xml


# ---------- CommandPlan:files/argv/env ----------

def test_stripmap_commandplan_files_argv_env(workspace):
    plan = _stripmap_plan(workspace)
    assert plan.argv == ["bash", "isce2/run_s03.sh"]
    assert plan.cwd == str(workspace)
    assert plan.env == {"OMP_NUM_THREADS": "8"}
    assert set(plan.files) == {"isce2/stripmapApp_s03.xml", "isce2/run_s03.sh"}
    script = plan.files["isce2/run_s03.sh"]
    assert "cd isce2" in script
    assert "stripmapApp.py stripmapApp_s03.xml --start=startup --end=fine_resample" in script
    assert plan.shell_line == "bash isce2/run_s03.sh"


def test_stripmap_threads_param_controls_env(workspace):
    """threads 是 resource 参数:只进 env,不改 XML/脚本内容。"""
    plan = _stripmap_plan(workspace, params={**STRIPMAP_PARAMS, "threads": 4})
    base = _stripmap_plan(workspace)
    assert plan.env["OMP_NUM_THREADS"] == "4"
    assert plan.files == base.files


def test_stripmap_range_per_capability(workspace):
    """区间表中各 capability 的 --start/--end 正确落进脚本。

    cap6(解缠)的 stripmap 方法尚未在 registry 声明,此处直接驱动构建器,
    验证实测得到的分段(filter_low_band 起,教训 2)已固化。
    """
    for cap_id, (start, end) in _STRIPMAP_RANGES.items():
        plan = _stripmap_plan(workspace, cap_id=cap_id)
        script = plan.files[f"isce2/run_s{cap_id:02d}.sh"]
        assert f"--start={start} --end={end}" in script
        assert f"stripmapApp_s{cap_id:02d}.xml" in plan.files[f"isce2/run_s{cap_id:02d}.sh"]


def test_stripmap_routes_to_isce2_builder():
    """真实模式下 isce2_stripmap_xcorr 路由到 isce2 构建器。"""
    assert resolve_builder(REGISTRY[3], "isce2_stripmap_xcorr",
                           simulated=False) is isce2_engine.build


# ---------- registry 参数声明 ----------

def test_registry_declares_stripmap_params():
    """路径类是 science 输入(进指纹),线程是 resource;enum 校验生效。"""
    cap = REGISTRY[3]
    kinds = cap.param_kinds()
    for key in ("reference_image", "reference_leader", "secondary_image",
                "secondary_leader", "resample_flag", "dem_path"):
        assert kinds[key] == "science", f"{key} 决定处理哪份数据,必须是 science"
    assert kinds["threads"] == "resource"
    assert cap.validate_params({"resample_flag": "dual2single"}) == {}
    assert cap.validate_params({"resample_flag": ""}) == {}
    assert "resample_flag" in cap.validate_params({"resample_flag": "fbd"})
    assert cap.validate_params({"reference_image": "data/raw/x/IMG-HH"}) == {}
    assert "reference_image" in cap.validate_params({"reference_image": 42})


# ---------- topsApp 回归:原有行为不变 ----------

def test_tops_path_unchanged(workspace):
    """tops 方法(不含 stripmap)仍走 topsApp:区间/文件名/XML 关键属性不变。"""
    chain = {3: ("isce2_tops_geom_esd", "startup", "fineresamp"),
             4: ("isce2_ifg_multilook", "ion", "filter"),
             5: ("goldstein", "filter", "filter")}
    for cap_id, (method, start, end) in chain.items():
        plan = isce2_engine.build(cap=REGISTRY[cap_id], method=method,
                                  params=REGISTRY[cap_id].default_params(),
                                  run=RUN, workspace=workspace)
        xml_rel = f"isce2/topsApp_s{cap_id:02d}.xml"
        script_rel = f"isce2/run_s{cap_id:02d}.sh"
        assert plan.argv == ["bash", script_rel]
        assert set(plan.files) == {xml_rel, script_rel}
        assert (f"topsApp.py topsApp_s{cap_id:02d}.xml "
                f"--start={start} --end={end}") in plan.files[script_rel]
        xml = plan.files[xml_rel]
        assert '<property name="Sensor name">SENTINEL1</property>' in xml
        assert '<property name="do unwrap">False</property>' in xml  # tops 链解缠走 snaphu 引擎
        assert "stripmapApp" not in xml
