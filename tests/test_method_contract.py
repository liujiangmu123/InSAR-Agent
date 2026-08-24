"""方法声明 × 构建器实现的一致性契约。

守护的缺口(2026-08-17 清点发现):注册表声明了方法、probe 又能探到对应引擎,
但 engines 里没有构建器 —— 可行性收窄会放行,计划把这一步排进去,跑到那一步
才 ToolMissing 停链。真实中招的四个:snap_backgeocoding / snap_interferogram
(装了 SNAP 时)、none(无条件)、gdal_warp(装了 GDAL 时,本机 conda 环境即是)。

纪律(engines/__init__.py 已写明「绝不静默回退」)要再加一条:**拒绝必须早于
执行**。本文件把 Method.implemented 与 resolve_builder 双向锁死:

  implemented=True  ⇒ resolve_builder 必须成功(否则就是新的陷阱)
  implemented=False ⇒ 真实模式必须拒绝,或落在 _SKELETON_BUILDERS 白名单里
                      (构建器在但只是骨架,不完成声明的工作)

并断言收窄期就把未实现方法排除、理由如实(不冒充「工具链缺失:<不存在的工具>」)。
"""

from __future__ import annotations

import pytest

from insar_agent.engines import ToolMissing, resolve_builder
from insar_agent.planner.feasibility import UNIMPLEMENTED_PREFIX, narrow_methods
from insar_agent.planner.score import pick_method
from insar_agent.registry.capabilities import ANALYSIS, PIPELINE, REGISTRY
from insar_agent.runtime.probe import ProbeResult

#: probe 能探到的引擎键闭集(runtime/probe.py 的 _ENGINE_EXES + _ENGINE_MODULES)。
#: 注册表用了这个集合之外的 engine 名时,该方法永远不可行 —— 那种「声明即死」
#: 必须显式标 implemented=False,否则用户得到的排除理由是查无此物的工具名。
PROBEABLE_ENGINES = frozenset({"isce2", "mintpy", "snaphu", "gdal", "snap",
                               "pystamps", "pyaps", "dolphin"})

#: implemented=False 但 resolve_builder 仍返回构建器的方法:构建器只是骨架。
#: 每条都要写清骨架在哪儿缺 —— 补全时连白名单一起删。
_SKELETON_BUILDERS = {
    "asf_search_slc": "engines/hyp3.py 只回显凭据方式并自检 asf_search 可导入,不检索不下载",
    "hyp3_submit": "engines/hyp3.py 同一脚本,不提交作业、不回收结果",
}

ALL_CAPS = (*PIPELINE, *ANALYSIS)


def _full_probe() -> ProbeResult:
    """理想环境:所有可探测引擎装齐 + 所有凭据齐备。

    陷阱方法只在「引擎装了」时才伪装成可行,所以一致性必须在这个最宽松的
    probe 下检查 —— 引擎缺失的机器上跑不出这类问题。
    """
    return ProbeResult(engines={k: "present" for k in PROBEABLE_ENGINES},
                       credentials={"earthdata": True, "cds": True, "gacos": True})


def _all_methods():
    for cap in ALL_CAPS:
        for m in cap.methods:
            yield cap, m


# ---------------------------------------------------------------------------
# 1. 双向一致性:声明可用 ⇔ 真的能构建
# ---------------------------------------------------------------------------

def test_implemented_methods_all_resolve_to_a_builder():
    """implemented=True 的方法必须都能解析出构建器。

    这一条挡住的正是「跑到那一步才 ToolMissing」的陷阱:新增方法时忘了写
    构建器,在这里就红,而不是等真实 run 停链。
    """
    broken = []
    for cap, m in _all_methods():
        if not m.implemented:
            continue
        try:
            resolve_builder(cap, m.id, simulated=False)
        except Exception as exc:  # noqa: BLE001 — 任何异常都算不可达
            broken.append(f"步 {cap.id} / {m.id}(engine={m.engine}): {exc}")
    assert not broken, (
        "以下方法声明 implemented=True 但解析不出构建器 —— 要么补构建器,"
        "要么标 implemented=False 并写 unimplemented_note:\n  " + "\n  ".join(broken))


def test_unimplemented_methods_are_rejected_or_whitelisted_skeletons():
    """implemented=False 的方法:真实模式必须拒绝,或明确登记为骨架构建器。"""
    leaking = []
    for cap, m in _all_methods():
        if m.implemented:
            continue
        try:
            resolve_builder(cap, m.id, simulated=False)
        except ToolMissing:
            continue  # 期望路径:显式拒绝
        if m.id not in _SKELETON_BUILDERS:
            leaking.append(f"步 {cap.id} / {m.id}")
    assert not leaking, (
        "以下方法标了 implemented=False 却能解析出构建器,且不在骨架白名单里 —— "
        "要么它其实已实现(改 implemented=True),要么把骨架原因登记进 "
        "_SKELETON_BUILDERS:\n  " + "\n  ".join(leaking))


def test_unimplemented_methods_carry_an_honest_note():
    """未实现必须给出如实说明:收窄理由直接引用它,不能是空话。"""
    for cap, m in _all_methods():
        if m.implemented:
            continue
        note = m.unimplemented_note
        assert note.strip(), f"步 {cap.id} / {m.id} 标了未实现但没写 unimplemented_note"
        # 说明要落在「本项目缺什么 / 该用什么替代」上,而不是只丢一个工具名
        assert len(note) >= 8, f"步 {cap.id} / {m.id} 的说明过短:{note!r}"


def test_skeleton_whitelist_has_no_stale_entries():
    """白名单不许留过期条目(方法改名/补全实现后必须同步清理)。"""
    declared = {m.id for _cap, m in _all_methods() if not m.implemented}
    stale = sorted(set(_SKELETON_BUILDERS) - declared)
    assert not stale, f"_SKELETON_BUILDERS 有过期条目(已不是未实现方法):{stale}"


# ---------------------------------------------------------------------------
# 2. 引擎名闭集:注册表不得引用探测不到的引擎并声称可用
# ---------------------------------------------------------------------------

def test_methods_claiming_unprobeable_engines_are_marked_unimplemented():
    """engine / requires_engines 用了 probe 探不到的键 ⇒ 必须标未实现。

    否则该方法永远被收窄排除,而理由是「工具链缺失:<查无此物>」——
    用户会去装一个不存在的工具(dem_service / unw3d / asf_api 都是这种)。
    """
    offenders = []
    for cap, m in _all_methods():
        keys = {e for e in (m.engine, *(m.requires_engines or ()))
                if e not in ("-", "")}
        unprobeable = sorted(keys - PROBEABLE_ENGINES)
        if unprobeable and m.implemented:
            offenders.append(f"步 {cap.id} / {m.id}: {unprobeable}")
    assert not offenders, (
        "以下方法引用了 probe 探测不到的引擎键,却声明 implemented=True —— "
        "要么给 probe 加探测(如 dolphin),要么标未实现:\n  " + "\n  ".join(offenders))


# ---------------------------------------------------------------------------
# 3. 收窄期就要拒绝(真实模式),演示模式仍放行
# ---------------------------------------------------------------------------

def test_unimplemented_never_feasible_in_real_mode_even_with_engines_present():
    """核心回归:引擎全装齐时,未实现方法在真实模式下依然不可行。"""
    probe = _full_probe()
    for cap in ALL_CAPS:
        by_id = {f.method.id: f for f in
                 narrow_methods(cap, probe, scenario=None, allow_simulated=False)}
        for m in cap.methods:
            if m.implemented:
                continue
            f = by_id[m.id]
            assert not f.ok, (
                f"步 {cap.id} / {m.id} 未实现却被判可行 —— 计划会排进它,"
                f"跑到该步才停链")
            assert UNIMPLEMENTED_PREFIX in f.blocked_reason
            assert m.unimplemented_note in f.blocked_reason


def test_unimplemented_still_demoable_in_simulated_mode():
    """演示/教学不受影响:允许 simulated 时未实现方法放行为模拟,阻塞原因留痕。

    安全性依据:resolve_builder 在 simulated=True 时统一走 simulate.build,
    不会碰缺失的真实构建器。
    """
    probe = _full_probe()
    for cap in ALL_CAPS:
        by_id = {f.method.id: f for f in
                 narrow_methods(cap, probe, scenario=None, allow_simulated=True)}
        for m in cap.methods:
            if m.implemented or m.scenario_only:
                continue
            f = by_id[m.id]
            assert f.ok and f.simulated, f"步 {cap.id} / {m.id} 在演示模式应放行为模拟"
            assert UNIMPLEMENTED_PREFIX in f.blocked_reason, "阻塞原因不许被洗白"
            resolve_builder(cap, m.id, simulated=True)  # 模拟路径必须可达


@pytest.mark.parametrize("cap_id", sorted(REGISTRY))
def test_every_step_keeps_at_least_one_runnable_method(cap_id):
    """收紧收窄后,每一步在引擎齐备的真实模式下仍至少有一个可跑方法。

    这是「不要把能力收窄到无路可走」的护栏 —— 排除未实现方法不能让任何步骤
    变成死胡同(第 2 步只剩 dem_local、第 5 步只剩 goldstein/boxcar 即可)。
    """
    cap = REGISTRY[cap_id]
    feas = narrow_methods(cap, _full_probe(), scenario=None, allow_simulated=False)
    picked = pick_method(feas)
    assert picked is not None, f"步 {cap_id} 在引擎齐备时无任何可行方法"
    resolve_builder(cap, picked.method.id, simulated=False)  # 选出来的必须能构建


def test_step2_dem_falls_back_to_local_and_step10_to_journal():
    """两处具体回退的行为锁定(清点时发现的两个默认路径问题)。

    第 2 步:在线 DEM 下载未实现 → 选中 dem_local(自备瓦片核验登记);
    第 10 步:gdal_warp 未实现 → 选中 figure_journal(GIS 导出走 /api/export)。
    """
    probe = _full_probe()
    dem = pick_method(narrow_methods(REGISTRY[2], probe))
    assert dem is not None and dem.method.id == "dem_local"

    fig = pick_method(narrow_methods(REGISTRY[10], probe))
    assert fig is not None and fig.method.id == "figure_journal"


def test_dolphin_becomes_feasible_once_probed():
    """dolphin 接进 probe 后,第 7 步的 PS/DS 方法真的能被选中并构建。

    修复前:probe 没有 dolphin 键 → 永久排除,engines/dolphin.py 白写。
    """
    cap7 = REGISTRY[7]
    by_id = {f.method.id: f for f in narrow_methods(cap7, _full_probe())}
    assert by_id["dolphin_ps_ds"].ok, "dolphin 装齐时 dolphin_ps_ds 应可行"
    resolve_builder(cap7, "dolphin_ps_ds", simulated=False)

    # 反面:dolphin 缺失时如实报工具链缺失(而不是「未实现」)
    lean = ProbeResult(engines={k: "present" for k in PROBEABLE_ENGINES - {"dolphin"}}
                       | {"dolphin": None}, credentials={})
    f = {x.method.id: x for x in narrow_methods(cap7, lean)}["dolphin_ps_ds"]
    assert not f.ok and "工具链缺失" in f.blocked_reason and "dolphin" in f.blocked_reason
    assert UNIMPLEMENTED_PREFIX not in f.blocked_reason
