"""Baja 条带链代理编排预检(scripts/agent_baja_rerun.py)——mock WSL 下的全流程断言。

对拍基准:docs/VALIDATION-isce2-wsl.md 与手工链 stripmapApp.xml 原件(金标准
常量随脚本携带)。全部用例零真实 WSL、零重型计算:WSL 探测/路由用假 runner,
宿主→WSL 的 9p 视图用 INSAR_WSL_HOSTROOT 测试缝指向临时目录。

覆盖面:意图命中 / 计划形态(11 步、3-6 方法、真实模式)/ 参数注入(WSL 绝对
路径 = 手工 XML)/ CommandPlan argv 与 --steps 分段形态 / 四段 XML 同源 /
后端路由决策 / 导入与 DEM 核验脚本往返 / cap3 条带产物候选 / 冒烟与矩阵汇总。
"""

from __future__ import annotations

import asyncio
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from insar_agent.brain.facade import Brain
from insar_agent.engines import isce2 as isce2_engine
from insar_agent.engines import localdata, resolve_builder
from insar_agent.engines.isce2 import _STRIPMAP_RANGES
from insar_agent.loop.driver import Driver
from insar_agent.registry.capabilities import REGISTRY
from insar_agent.runtime.backend_select import backend_for_step
from insar_agent.runtime.discover import discover_artifacts
from insar_agent.runtime.jobs import LocalJobBackend
from insar_agent.runtime.probe import ProbeResult
from insar_agent.runtime.wsl import WslJobBackend
from insar_agent.runtime.wsl_probe import merge_wsl_probe

# 按文件路径加载编排脚本(scripts/ 不是包;脚本导入期零副作用)。
# 必须先登记进 sys.modules:dataclasses 装饰器按 cls.__module__ 反查模块字典
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "agent_baja_rerun.py"
_spec = importlib.util.spec_from_file_location("agent_baja_rerun", _SCRIPT)
baja = importlib.util.module_from_spec(_spec)
sys.modules["agent_baja_rerun"] = baja
_spec.loader.exec_module(baja)

_silent = lambda *a, **k: None  # noqa: E731  # 测试里静默脚本的 echo 输出


def fake_probe() -> ProbeResult:
    """规划用假探测:isce2/snaphu 是 WSL 晋升形态,mintpy/pyaps 宿主在位;
    snaphu 独立可执行故意缺席场景已由 test_stripmap_scenario 覆盖。"""
    return ProbeResult(
        engines={"isce2": "2.6.5 (wsl)", "snaphu": "2.0.6 (wsl)",
                 "mintpy": "present(insar)", "pyaps": "present(insar)",
                 "gdal": "present(insar)", "snap": None, "pystamps": None},
        credentials={"earthdata": False, "cds": False, "gacos": False})


def fake_wsl_runner(argv: list[str], timeout: float) -> subprocess.CompletedProcess:
    """假 wsl.exe:只应答可达性探测(wsl -l -q);其他调用一律失败以暴露越界。"""
    if argv[:3] == ["wsl.exe", "-l", "-q"]:
        return subprocess.CompletedProcess(argv, 0, stdout="insar\n", stderr="")
    raise AssertionError(f"预检不应发起该 WSL 调用:{argv}")


@pytest.fixture()
def driver(store, workspace):
    return Driver(store, workspace=workspace, brain=Brain(None),
                  allow_simulated=False, probe=fake_probe())


def plan_baja(driver, store) -> tuple[dict, "baja.Matrix"]:
    """跑规划回合并返回 (run, 矩阵);断言逻辑在脚本的 plan_turn 里。"""
    mx = baja.Matrix()
    run = asyncio.run(baja.plan_turn(driver, store, mx, echo=_silent))
    assert run is not None
    return run, mx


def make_wsl_tree(root: Path, *, drop: str | None = None) -> Path:
    """在临时目录里搭 /home/insar/work/baja 的宿主视图(INSAR_WSL_HOSTROOT 测试缝)。"""
    src = root / "home" / "insar" / "work" / "baja"
    raw = src / "raw"
    raw.mkdir(parents=True)
    for name in ("IMG-HH-ALPSRP207600640-H1.0__A", "IMG-HH-ALPSRP227730640-H1.0__A",
                 "LED-ALPSRP207600640-H1.0__A", "LED-ALPSRP227730640-H1.0__A"):
        if name != drop:
            (raw / name).write_bytes(b"x" * 16)
    dem = src / "dem"
    dem.mkdir()
    if drop != "dem.wgs84":
        (dem / "dem.wgs84").write_bytes(b"d" * 16)
        (dem / "dem.wgs84.xml").write_text("<imageFile/>", encoding="utf-8")
    return root


# ---------------- 1. WSL 引擎晋升(merge → 裸键) ----------------

def test_promote_wsl_engines_only_fills_gaps():
    probe = ProbeResult(engines={"isce2": None, "snaphu": None, "mintpy": "1.6.4"})
    merge_wsl_probe(probe, {
        "ok": True, "distro": "insar", "error": None, "engine_prefix": "/opt/x",
        "engines": {"isce2": {"present": True, "version": "2.6.5"},
                    "snaphu": {"present": True, "version": None},
                    "mintpy": {"present": True, "version": "9.9.9"}}})
    promoted = baja.promote_wsl_engines(probe)
    assert promoted == ["isce2", "snaphu"]
    assert probe.engines["isce2"] == "2.6.5 (wsl)"  # 版本 + 来源标注
    assert probe.engines["snaphu"] == "present (wsl)"
    assert probe.engines["mintpy"] == "1.6.4"  # 宿主本地已有的键绝不覆盖
    assert baja.promote_wsl_engines(probe) == []  # 幂等:二次晋升无事发生
    assert probe.engine_ok("isce2") and probe.engine_ok("snaphu")  # feasibility 可见


def test_promote_noop_when_wsl_unreachable():
    probe = ProbeResult(engines={"isce2": None})
    merge_wsl_probe(probe, {"ok": False, "distro": "insar", "error": "不可达",
                            "engines": {}})
    assert baja.promote_wsl_engines(probe) == []
    assert not probe.engine_ok("isce2")  # 不可达绝不无中生有


# ---------------- 2. 意图(规则层,零 LLM) ----------------

def test_intent_rules_hit_stripmap_coseismic():
    r = Brain(None).intent(baja.PLAN_TEXT)
    assert r.ok and r.source == "rules" and not r.need_form
    assert r.scenario.key == "stripmap_coseismic"


# ---------------- 3. 金标准自检(手工 XML 原件形态) ----------------

def test_manual_golden_xml_shape():
    props = baja.xml_props(baja.MANUAL_STRIPMAP_XML)
    assert props[("stripmapApp/insar", "sensor name")] == "ALOS"
    assert props[("stripmapApp/insar", "do unwrap")] == "True"
    assert props[("stripmapApp/insar", "unwrapper name")] == "snaphu"
    # RESAMPLE_FLAG 只在 secondary(FBD 从影像),reference 无
    assert props[("stripmapApp/insar/secondary", "RESAMPLE_FLAG")] == "dual2single"
    assert ("stripmapApp/insar/reference", "RESAMPLE_FLAG") not in props
    assert props[("stripmapApp/insar", "demFilename")] == "/home/insar/work/baja/dem/dem.wgs84"


# ---------------- 4. 规划回合:意图/计划/方法/真实模式 ----------------

def test_turn_plans_real_stripmap_chain(driver, store):
    run, mx = plan_baja(driver, store)
    assert mx.blockers == [], [r.evidence for r in mx.blockers]
    assert run["scenario"] == "stripmap_coseismic"
    assert run["status"] == "ready" and not run["simulated"]
    steps = {s.step_id: s for s in store.load_steps(run["run_id"])}
    assert sorted(steps) == list(range(1, 12))
    for sid, method in baja.STRIPMAP_METHODS.items():
        assert steps[sid].method == method
    assert steps[1].method == "local_import" and steps[2].method == "dem_local"


def test_plan_params_equal_manual_xml(driver, store):
    """场景包参数 → 计划落库的闭环:3 步路径参数与手工 XML 原件逐字段一致。"""
    run, _mx = plan_baja(driver, store)
    steps = {s.step_id: s for s in store.load_steps(run["run_id"])}
    manual = baja.xml_props(baja.MANUAL_STRIPMAP_XML)
    p3 = steps[3].params
    assert p3["reference_image"] == manual[("stripmapApp/insar/reference", "IMAGEFILE")]
    assert p3["reference_leader"] == manual[("stripmapApp/insar/reference", "LEADERFILE")]
    assert p3["secondary_image"] == manual[("stripmapApp/insar/secondary", "IMAGEFILE")]
    assert p3["secondary_leader"] == manual[("stripmapApp/insar/secondary", "LEADERFILE")]
    assert p3["resample_flag"] == manual[("stripmapApp/insar/secondary", "RESAMPLE_FLAG")]
    assert p3["dem_path"] == manual[("stripmapApp/insar", "demFilename")]
    assert steps[1].params["source"] == "/home/insar/work/baja"
    assert steps[2].params["dem"] == p3["dem_path"]


# ---------------- 5. CommandPlan:argv 形态 / --steps 分段 / 四段 XML 同源 ----------------

def test_command_plans_argv_steps_flag_and_xml(driver, store):
    run, mx = plan_baja(driver, store)
    plans = baja.inspect_command_plans(driver, store, run, mx, echo=_silent)
    assert mx.blockers == [], [f"{r.item}: {r.evidence}" for r in mx.blockers]
    xml3 = plans[3].files["isce2/stripmapApp_s03.xml"]
    assert baja.diff_props(xml3, baja.MANUAL_STRIPMAP_XML) == []  # 语义=手工原件
    for sid in (3, 4, 5, 6):
        plan = plans[sid]
        assert plan.argv == ["bash", f"isce2/run_s{sid:02d}.sh"]
        script = plan.files[f"isce2/run_s{sid:02d}.sh"]
        start, end = _STRIPMAP_RANGES[sid]
        # --steps 必须显式(步进模式才解析 --start/--end);nice 对齐手工 cmd.sh
        assert (f"nice -n 10 stripmapApp.py stripmapApp_s{sid:02d}.xml "
                f"--steps --start={start} --end={end}") in script
        # 全链共享一份配置:4-6 段渲染与 3 段逐字节一致(run.chain 语义)
        assert plan.files[f"isce2/stripmapApp_s{sid:02d}.xml"] == xml3
    # 1/2 步是宿主 python 脚本(LocalJobBackend 域)
    assert plans[1].argv[0] == sys.executable and plans[2].argv[0] == sys.executable


def test_stripmap_segments_share_step3_params_via_chain(workspace):
    """引擎级守卫:4 段用 run.chain[3] 的路径参数渲染;无 chain 时回落默认不变。"""
    p3 = {"reference_image": "/w/raw/IMG-A", "reference_leader": "/w/raw/LED-A",
          "secondary_image": "/w/raw/IMG-B", "secondary_leader": "/w/raw/LED-B",
          "resample_flag": "dual2single", "dem_path": "/w/dem/dem.wgs84"}
    run = {"simulated": 0, "chain": {3: {"method": "isce2_stripmap_xcorr", "params": p3},
                                     4: {"method": "isce2_stripmap_ifg", "params": {}}}}
    plan4 = isce2_engine.build(cap=REGISTRY[4], method="isce2_stripmap_ifg",
                               params={"pairs": 1}, run=run, workspace=workspace)
    xml4 = plan4.files["isce2/stripmapApp_s04.xml"]
    assert "/w/raw/IMG-A" in xml4 and "/w/dem/dem.wgs84" in xml4
    assert '<property name="RESAMPLE_FLAG">dual2single</property>' in xml4
    bare = isce2_engine.build(cap=REGISTRY[4], method="isce2_stripmap_ifg",
                              params={}, run={"simulated": 0}, workspace=workspace)
    assert "data/raw/reference/IMG-HH" in bare.files["isce2/stripmapApp_s04.xml"]


# ---------------- 6. 后端路由决策(假 runner,零真实 wsl.exe) ----------------

def test_backend_routing_decision():
    env = {"INSAR_WSL_DISTRO": "insar"}
    b = backend_for_step(engine="isce2", simulated=False, env=env, runner=fake_wsl_runner)
    assert isinstance(b, WslJobBackend) and b.paths.distro == "insar"
    assert isinstance(backend_for_step(engine="mintpy", simulated=False, env=env,
                                       runner=fake_wsl_runner), LocalJobBackend)
    assert isinstance(backend_for_step(engine="-", simulated=False, env=env,
                                       runner=fake_wsl_runner), LocalJobBackend)
    # 模拟运行即使强制 wsl 也留在本地(演示模式不依赖 WSL)
    assert isinstance(backend_for_step(engine="isce2", simulated=True,
                                       env={"INSAR_JOB_BACKEND": "wsl"}), LocalJobBackend)
    # 运维逃逸阀:显式强制 local
    assert isinstance(backend_for_step(engine="isce2", simulated=False,
                                       env={"INSAR_JOB_BACKEND": "local"}), LocalJobBackend)


def test_route_backends_matrix_with_fake_wsl(driver, store):
    run, _ = plan_baja(driver, store)
    mx = baja.Matrix()
    baja.route_backends(store, run, mx, env={"INSAR_WSL_DISTRO": "insar"},
                        runner=fake_wsl_runner, echo=_silent)
    assert mx.blockers == []
    row = mx.rows[-1]
    assert row.status == "ok" and "3:WSL 4:WSL 5:WSL 6:WSL" in row.evidence


# ---------------- 7. 导入/DEM 脚本往返(INSAR_WSL_HOSTROOT 测试缝) ----------------

def _run_script(plan, cwd: Path, hostroot: Path) -> subprocess.CompletedProcess:
    for rel, content in plan.files.items():
        target = cwd / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    import os
    return subprocess.run(plan.argv, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60,
                          env={**os.environ, "INSAR_WSL_HOSTROOT": str(hostroot),
                               "INSAR_WSL_DISTRO": "insar"})


def test_wsl_import_script_roundtrip(tmp_path, workspace):
    hostroot = make_wsl_tree(tmp_path / "fakewsl")
    plan = localdata.build(cap=REGISTRY[1], method="local_import",
                           params={"source": "/home/insar/work/baja"},
                           run={}, workspace=workspace)
    cp = _run_script(plan, workspace, hostroot)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    manifest = (workspace / "data" / "slc" / "manifest.json").read_text(encoding="utf-8")
    assert '"mode": "wsl_workspace"' in manifest
    assert "IMG-HH-ALPSRP207600640-H1.0__A" in manifest
    assert '"raw_dir": "/home/insar/work/baja/raw"' in manifest
    assert '"dem": "/home/insar/work/baja/dem/dem.wgs84"' in manifest


def test_wsl_import_script_fails_on_incomplete_pair(tmp_path, workspace):
    hostroot = make_wsl_tree(tmp_path / "fakewsl", drop="LED-ALPSRP227730640-H1.0__A")
    plan = localdata.build(cap=REGISTRY[1], method="local_import",
                           params={"source": "/home/insar/work/baja"},
                           run={}, workspace=workspace)
    cp = _run_script(plan, workspace, hostroot)
    assert cp.returncode == 3  # 干涉对不完整 → 显式失败,不静默
    assert "干涉对不完整" in cp.stdout
    assert not (workspace / "data" / "slc" / "manifest.json").exists()


def test_dem_local_script_roundtrip_and_missing(tmp_path, workspace):
    assert resolve_builder(REGISTRY[2], "dem_local", simulated=False) is localdata.build
    hostroot = make_wsl_tree(tmp_path / "fakewsl")
    plan = localdata.build(cap=REGISTRY[2], method="dem_local",
                           params={"dem": "/home/insar/work/baja/dem/dem.wgs84"},
                           run={}, workspace=workspace)
    cp = _run_script(plan, workspace, hostroot)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert (workspace / "data" / "dem" / "manifest.json").exists()
    # DEM 缺失 → rc=2 显式失败(fixImageXml 产物 .xml 同样必须在位)
    bare = make_wsl_tree(tmp_path / "fakewsl2", drop="dem.wgs84")
    ws2 = tmp_path / "ws2"
    ws2.mkdir()
    cp2 = _run_script(plan, ws2, bare)
    assert cp2.returncode == 2 and "DEM 不在位" in cp2.stdout


# ---------------- 8. cap3 条带产物候选(配准段产物发现) ----------------

def test_cap3_coreg_candidates_cover_stripmap_layout(workspace):
    spec = next(a for a in REGISTRY[3].artifacts if a.id == "coreg")
    assert spec.candidates[0] == "data/coreg"  # 首候选不变(tops/simulate 兼容)
    assert "isce2/coregisteredSlc" in spec.candidates  # 手工链实测布局
    (workspace / "isce2" / "coregisteredSlc").mkdir(parents=True)
    found, missing = discover_artifacts(workspace, REGISTRY[3].artifacts)
    by_id = {f.spec.id: f for f in found}
    assert by_id["coreg"].path == workspace / "isce2" / "coregisteredSlc"
    assert not [m for m in missing if m.spec.id == "coreg"]


# ---------------- 9. 冒烟 + 全流程矩阵(mock WSL) ----------------

def test_smoke_wsl_matrix_row_with_fake_runner(driver, store):
    run, _ = plan_baja(driver, store)
    mx = baja.Matrix()
    plans = baja.inspect_command_plans(driver, store, run, mx, echo=_silent)

    calls: list[list[str]] = []

    def ok_runner(argv, timeout):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="[smoke] 全部通过", stderr="")

    baja.smoke_wsl(driver, plans, "insar", mx, wsl_run=ok_runner, echo=_silent)
    assert mx.rows[-1].status == "ok"
    assert calls and calls[0][:4] == ["wsl.exe", "-d", "insar", "-u"]
    # 渲染文件已真实落进工作区(--launch 用的就是这批配置)
    assert (driver.workspace / "isce2" / "stripmapApp_s03.xml").exists()
    assert (driver.workspace / "isce2" / "_smoke_check.py").exists()

    def bad_runner(argv, timeout):
        return subprocess.CompletedProcess(argv, 1, stdout="[MISS] x", stderr="")

    baja.smoke_wsl(driver, plans, "insar", mx, wsl_run=bad_runner, echo=_silent)
    assert mx.rows[-1].status == "bad"  # 冒烟失败 = 阻塞项,--launch 会被拒绝


def test_full_dry_run_matrix_all_green_with_mocks(driver, store, tmp_path):
    """mock WSL 下的 dry-run 全流程:规划 → 渲染 → 试运行 → 路由 → 冒烟,零阻塞。"""
    import os

    mx = baja.Matrix()
    run = asyncio.run(baja.plan_turn(driver, store, mx, echo=_silent))
    plans = baja.inspect_command_plans(driver, store, run, mx, echo=_silent)
    hostroot = make_wsl_tree(tmp_path / "fakewsl")
    os.environ["INSAR_WSL_HOSTROOT"] = str(hostroot)
    try:
        baja.rehearse_import_scripts(plans, mx, tmp_path, echo=_silent)
    finally:
        os.environ.pop("INSAR_WSL_HOSTROOT", None)
    baja.route_backends(store, run, mx, env={"INSAR_WSL_DISTRO": "insar"},
                        runner=fake_wsl_runner, echo=_silent)
    baja.smoke_wsl(driver, plans, "insar", mx,
                   wsl_run=lambda argv, t: subprocess.CompletedProcess(argv, 0, "", ""),
                   echo=_silent)
    baja.known_gaps(mx, run, driver.workspace)
    assert mx.blockers == [], [f"{r.item}: {r.evidence}" for r in mx.blockers]
    # 已知风险如实在矩阵中呈现(warn 不拦启动,但必须可见)
    warns = [r.item for r in mx.rows if r.status == "warn"]
    assert any("7-11" in w for w in warns) and any("9p" in w or "/mnt" in w for w in warns)
