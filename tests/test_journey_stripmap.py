"""stripmap 场景旅程验证(计划与渲染级,零真实引擎/零真实 WSL)。

旅程主线:意图(「用 ALOS 条带数据做 Baja 同震干涉」)→ 计划(3-6 步全 stripmap
方法,model=linear)→ 干跑级 CommandPlan(--steps 分段 + 四段 XML 同源一致)→
后端路由(WSL 可达时 3-6 步 WslJobBackend,INSAR_JOB_BACKEND=local 可强制)→
模拟模式全链执行(无引擎机器也能演示到 done)→ scripts/agent_baja_rerun.py 的
dry-run 函数级验收(计划矩阵生成 + 三类缺失情形的阻塞项文案)→ 混合意图消解
(priority=5 的 stripmap_coseismic 先于 priority=10 的 quake,双向断言)。

纪律:绝不启动 stripmapApp/snaphu —— WSL 交互全部打桩(假 probe / 假 runner /
monkeypatch wsl_status);模拟执行是纯 Python 合成产物,走 LocalJobBackend。
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

from insar_agent.brain.facade import Brain
from insar_agent.engines import default_builder
from insar_agent.engines.isce2 import _STRIPMAP_RANGES
from insar_agent.loop.driver import Driver
from insar_agent.registry import scenarios as scenarios_mod
from insar_agent.registry.capabilities import REGISTRY
from insar_agent.registry.scenarios import SCENARIOS, classify_text, scenario_of
from insar_agent.runtime import backend_select
from insar_agent.runtime.backend_select import backend_for_step
from insar_agent.runtime.jobs import LocalJobBackend
from insar_agent.runtime.probe import ProbeResult
from insar_agent.runtime.wsl import WslJobBackend

# 编排脚本按文件路径加载(scripts/ 不是包)。若 tests/test_baja_preflight.py 已
# 登记过同名模块则复用,避免同一脚本存在两份模块对象(dataclass 按 __module__ 反查)。
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "agent_baja_rerun.py"
if "agent_baja_rerun" in sys.modules:
    baja = sys.modules["agent_baja_rerun"]
else:
    _spec = importlib.util.spec_from_file_location("agent_baja_rerun", _SCRIPT)
    baja = importlib.util.module_from_spec(_spec)
    sys.modules["agent_baja_rerun"] = baja
    _spec.loader.exec_module(baja)

TURN_TEXT = "用 ALOS 条带数据做 Baja 同震干涉"

# 3-6 步期望:(方法, stripmapApp 分段起, 分段终)—— 与场景包覆写、引擎区间表对拍
CHAIN = {
    3: ("isce2_stripmap_xcorr", "startup", "fine_resample"),
    4: ("isce2_stripmap_ifg", "split_range_spectrum", "filter"),
    5: ("isce2_stripmap_filter", "filter", "filter"),
    6: ("isce2_stripmap_unwrap_snaphu", "filter_low_band", "geocode"),
}


def _silent(*_a, **_k) -> None:
    """静默编排脚本的 echo 输出。"""


def wsl_ready_probe() -> ProbeResult:
    """mock「WSL 可达」的规划探测:isce2/snaphu 以 WSL 晋升形态在位,7-11 引擎齐备。"""
    return ProbeResult(
        engines={"isce2": "2.6.5 (wsl)", "snaphu": "2.0.6 (wsl)",
                 "mintpy": "present(insar)", "pyaps": "present(insar)",
                 "gdal": "present(insar)", "snap": None, "pystamps": None},
        credentials={"earthdata": False, "cds": False, "gacos": False},
        disk_free_gb=100.0, cpu_count=8)


def no_engine_probe() -> ProbeResult:
    """无任何引擎的探测:模拟模式演示与「引擎缺失」阻塞项两用。"""
    return ProbeResult(
        engines={"isce2": None, "mintpy": None, "snaphu": None, "gdal": None,
                 "snap": None, "pystamps": None, "pyaps": None},
        credentials={"earthdata": False, "cds": False, "gacos": False},
        disk_free_gb=100.0, cpu_count=8)


async def _collect(agen) -> list[dict]:
    return [e async for e in agen]


@pytest.fixture()
def driver(store, workspace):
    """真实模式 driver(mock WSL 可达 probe;不许模拟回退)。"""
    return Driver(store, workspace=workspace, brain=Brain(None),
                  allow_simulated=False, probe=wsl_ready_probe())


def _plan_via_turn(driver, store, session: str) -> tuple[dict, list[dict]]:
    events = asyncio.run(_collect(driver.turn(session, TURN_TEXT)))
    run = store.latest_run(session)
    assert run is not None
    return run, events


# ---------------- 1. turn:意图命中 + 3-6 全 stripmap 方法 + model=linear ----------------

def test_turn_journey_hits_stripmap_scenario(driver, store):
    run, events = _plan_via_turn(driver, store, "j1")
    assert run["scenario"] == "stripmap_coseismic"  # 意图规则层命中场景包(零 LLM)
    assert run["status"] == "ready" and not run["simulated"]  # WSL 可达 → 真实可执行计划
    methods = {s.step_id: s.method for s in store.load_steps(run["run_id"])}
    for sid, (method, _s, _e) in CHAIN.items():
        assert methods[sid] == method, f"第 {sid} 步应为 {method},实际 {methods.get(sid)}"
    # 场景 model=linear(单对两景无法区分阶跃与线性)进第 9 步方法
    assert scenario_of("stripmap_coseismic").model == "linear"
    assert methods[9] == "linear"
    # 旅程渲染:thinking 报场景标签,say 报计划就绪,candidates 指向首个待决策步骤
    thinking = next(e for e in events if e["t"] == "thinking")
    assert "条带同震干涉" in thinking["body"]
    says = [p for e in events if e["t"] == "say"
            for p in e["parts"] if isinstance(p, str)]
    assert any("计划就绪" in s for s in says)
    assert any(e["t"] == "candidates" and e["stepId"] == 1 for e in events)


# ---------------- 2. 干跑级 CommandPlan:--steps 分段 + 四段 XML 同源一致 ----------------

def test_dryrun_command_plans_steps_flag_and_xml(driver, store, workspace):
    run, _events = _plan_via_turn(driver, store, "j2")
    steps = {s.step_id: s for s in store.load_steps(run["run_id"])}
    # 全链快照(executor 的 run["chain"] 语义):4-6 段与 3 段同源渲染
    run_ctx = {"simulated": 0, "run_id": run["run_id"],
               "chain": {sid: {"method": s.method, "params": s.params}
                         for sid, s in steps.items()}}
    xmls: dict[int, str] = {}
    for sid, (_method, start, end) in CHAIN.items():
        plan = default_builder(cap=REGISTRY[sid], method=steps[sid].method,
                               params=steps[sid].params, run=run_ctx, workspace=workspace)
        script_rel = f"isce2/run_s{sid:02d}.sh"
        assert plan.argv == ["bash", script_rel]  # argv 执行的就是渲染出的分段脚本
        script = plan.files[script_rel]
        # --steps 必须显式(步进模式才解析 --start/--end,漏掉整链会从头跑)
        assert "--steps" in script and "stripmapApp.py" in script
        assert f"--start={start} --end={end}" in script, f"s{sid} 分段区间漂移"
        assert _STRIPMAP_RANGES[sid] == (start, end)  # 引擎区间表与期望表同步
        xmls[sid] = plan.files[f"isce2/stripmapApp_s{sid:02d}.xml"]
    xml3 = xmls[3]
    # XML 关键字段 = 手工链固化路径(IMAGEFILE/LEADERFILE/RESAMPLE_FLAG/demFilename)
    assert ('<property name="IMAGEFILE">'
            "/home/insar/work/baja/raw/IMG-HH-ALPSRP207600640-H1.0__A</property>") in xml3
    assert ('<property name="LEADERFILE">'
            "/home/insar/work/baja/raw/LED-ALPSRP207600640-H1.0__A</property>") in xml3
    assert ('<property name="IMAGEFILE">'
            "/home/insar/work/baja/raw/IMG-HH-ALPSRP227730640-H1.0__A</property>") in xml3
    assert '<property name="RESAMPLE_FLAG">dual2single</property>' in xml3
    assert ('<property name="demFilename">'
            "/home/insar/work/baja/dem/dem.wgs84</property>") in xml3
    for sid in (4, 5, 6):  # 四段同源一致:逐字节同一份 stripmapApp 配置
        assert xmls[sid] == xml3, f"s{sid} 段 XML 与配准段不一致"


# ---------------- 3. 后端路由:3-6 步 WSL,1/2/7-11 本地;可强制 local ----------------

def test_backend_routing_reachable_and_forced_local(driver, store, monkeypatch):
    run, _events = _plan_via_turn(driver, store, "j3")
    steps = {s.step_id: s for s in store.load_steps(run["run_id"])}
    monkeypatch.setattr(backend_select, "wsl_status",
                        lambda runner=None: {"installed": True, "distros": ["insar"]})

    def engine_of(sid: int) -> str:
        m = REGISTRY[sid].method(steps[sid].method)
        return m.engine if m else "-"

    for sid in sorted(steps):
        backend = backend_for_step(engine=engine_of(sid), simulated=False, env={})
        if sid in CHAIN:
            assert isinstance(backend, WslJobBackend), f"第 {sid} 步应路由 WSL"
            assert backend.paths.distro == "insar"
        else:
            assert isinstance(backend, LocalJobBackend), f"第 {sid} 步应留在本地"
    # 运维逃逸阀:INSAR_JOB_BACKEND=local 强制时 3-6 步同样全部本地
    for sid in sorted(steps):
        backend = backend_for_step(engine=engine_of(sid), simulated=False,
                                   env={"INSAR_JOB_BACKEND": "local"})
        assert isinstance(backend, LocalJobBackend), f"强制 local 下第 {sid} 步仍路由 WSL"


# ---------------- 4. 模拟模式全链执行(无引擎机器也能演示到 done) ----------------

def test_simulated_full_chain_reaches_done(store, workspace):
    """不 mock WSL:探测无任何引擎,allow_simulated 放行 → 全链合成执行到 done。"""
    driver = Driver(store, workspace=workspace, probe=no_engine_probe(), poll=0.05,
                    startup_grace=15.0, brain=Brain(None))  # allow_simulated 缺省 True
    events = asyncio.run(_collect(driver.turn("sim", TURN_TEXT)))
    run = store.latest_run("sim")
    assert run["scenario"] == "stripmap_coseismic" and run["simulated"]
    assert any(e["t"] == "note" and "模拟执行" in e["text"] for e in events)  # 显式横幅

    exec_events = asyncio.run(_collect(driver.execute("sim")))
    kinds = [e["t"] for e in exec_events]
    assert "result" in kinds and "report" in kinds
    assert store.get_run(run["run_id"])["status"] == "done"
    steps = store.load_steps(run["run_id"])
    assert len(steps) == 11 and all(s.state == "done" for s in steps)  # 无 skipped,全链落地
    # 合成产物显式标注 SIMULATED(第 6 步解缠产物为例),绝不以假乱真
    unw = workspace / "data" / "unw" / "sim.dat"
    assert unw.exists() and b"SIMULATED" in unw.read_bytes()


# ---------------- 5. agent_baja_rerun 的 dry-run:函数级调用(不起子进程) ----------------

def test_baja_dryrun_plan_matrix_green(driver, store):
    """计划表(预检矩阵)生成:规划 + 渲染两阶段全绿,关键行逐项在位。"""
    mx = baja.Matrix()
    run = asyncio.run(baja.plan_turn(driver, store, mx, echo=_silent))
    assert run is not None
    plans = baja.inspect_command_plans(driver, store, run, mx, echo=_silent)
    assert mx.blockers == [], [f"{r.item}: {r.evidence}" for r in mx.blockers]
    assert set(plans) == {1, 2, 3, 4, 5, 6}
    items = {r.item: r.status for r in mx.rows}
    assert items["意图命中 stripmap_coseismic"] == "ok"
    assert items["3-6 步为 stripmap 方法"] == "ok"
    assert items["3 步路径参数=手工链 WSL 绝对路径"] == "ok"
    assert items["s3 XML=手工原件(语义比对)"] == "ok"
    for sid in (4, 5, 6):
        assert items[f"s{sid} XML 与 s3 同源一份配置"] == "ok"
    table = mx.render()  # 计划表可渲染:全绿行带 ✓ 标记,分段命令行在列
    assert "✓" in table and "s6 命令形态(--steps 分段)" in table


def test_baja_blocker_engine_missing(store, workspace):
    """引擎缺失(isce2 探测不到,不许模拟回退)→ 阻塞项:计划停在 planning。"""
    driver = Driver(store, workspace=workspace, brain=Brain(None),
                    allow_simulated=False, probe=no_engine_probe())
    mx = baja.Matrix()
    run = asyncio.run(baja.plan_turn(driver, store, mx, echo=_silent))
    assert run is not None and run["status"] == "planning"
    blocked = {r.item: r.evidence for r in mx.blockers}
    assert "计划可执行(非 planning)" in blocked
    assert "make_plan 存在可行性 problems" in blocked["计划可执行(非 planning)"]
    assert "3-6 步为 stripmap 方法" in blocked  # 3-6 步组不出 stripmap 链,如实标红


def test_baja_blocker_distro_unreachable(driver, store):
    """发行版不可达(wsl -l 无 insar)→ 3-6 步回落本地 → 路由阻塞项文案。"""
    mx = baja.Matrix()
    run = asyncio.run(baja.plan_turn(driver, store, mx, echo=_silent))
    assert mx.blockers == []

    def no_distro_runner(argv, timeout):
        if argv[:3] == ["wsl.exe", "-l", "-q"]:
            return subprocess.CompletedProcess(argv, 0, stdout="Ubuntu\n", stderr="")
        raise AssertionError(f"路由预检不应发起该 WSL 调用:{argv}")

    mx2 = baja.Matrix()
    baja.route_backends(store, run, mx2, env={"INSAR_WSL_DISTRO": "insar"},
                        runner=no_distro_runner, echo=_silent)
    assert len(mx2.blockers) == 1
    row = mx2.blockers[0]
    assert row.item == "后端路由:3-6 步 WslJobBackend,其余本地"
    assert "3:local" in row.evidence and "6:local" in row.evidence


def test_baja_blocker_data_missing(driver, store):
    """数据不在位(WSL 冒烟 MISS)与 wsl.exe 调用失败 → 冒烟阻塞项文案。"""
    mx = baja.Matrix()
    run = asyncio.run(baja.plan_turn(driver, store, mx, echo=_silent))
    plans = baja.inspect_command_plans(driver, store, run, mx, echo=_silent)
    assert mx.blockers == []

    def miss_runner(argv, timeout):
        return subprocess.CompletedProcess(
            argv, 1, stdout="  [MISS] IMAGEFILE  /home/insar/work/baja/raw/IMG-…", stderr="")

    baja.smoke_wsl(driver, plans, "insar", mx, wsl_run=miss_runner, echo=_silent)
    row = mx.rows[-1]
    assert row.item == "WSL 冒烟(XML 语法+数据在位)" and row.status == "bad"
    assert "见上方 MISS 行" in row.evidence
    assert mx.blockers and mx.blockers[-1].item == row.item  # 冒烟失败必须拦 --launch

    def boom_runner(argv, timeout):
        raise OSError("wsl.exe not found")

    mx3 = baja.Matrix()
    baja.smoke_wsl(driver, plans, "insar", mx3, wsl_run=boom_runner, echo=_silent)
    assert mx3.blockers and "wsl.exe 调用失败" in mx3.blockers[0].evidence


# ---------------- 6. 混合意图消解(priority 歧义,双向断言) ----------------

def test_intent_disambiguation_bidirectional():
    stripmap = scenario_of("stripmap_coseismic")
    quake = scenario_of("quake")
    mixed = "ALOS 条带的同震"
    # 双向前提:混合文本同时命中两个包的 match —— 是 priority 在消解,不是词面不相交
    assert re.search(stripmap.match, mixed, re.IGNORECASE)
    assert re.search(quake.match, mixed, re.IGNORECASE)
    assert classify_text(mixed).key == "stripmap_coseismic"  # priority=5 先试
    pure = "同震形变分析"
    assert not re.search(stripmap.match, pure, re.IGNORECASE)  # 纯同震不含条带关键词
    assert classify_text(pure).key == "quake"

    def priority_of(sc) -> int:
        front, _body = scenarios_mod._split_frontmatter(
            Path(sc.skill_path).read_text(encoding="utf-8"))
        return int(front["metadata"]["priority"])

    assert priority_of(stripmap) == 5 and priority_of(quake) == 10
    keys = [sc.key for sc in SCENARIOS]  # SCENARIOS 按 priority 排序,先试者在前
    assert keys.index("stripmap_coseismic") < keys.index("quake")
    # Brain 规则路径(零 LLM)与 classify_text 同判
    brain = Brain(None)
    assert brain.intent(mixed).scenario.key == "stripmap_coseismic"
    assert brain.intent(pure).scenario.key == "quake"
