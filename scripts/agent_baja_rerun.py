r"""ALOS Baja 条带链 · 代理自编排复跑(缺省 dry-run,只差一声令下)。

════════════════════════════════════════════════════════════════════════
██  重 型 计 算 警 告  ██████████████████████████████████████████████████
██                                                                      ██
██  本脚本缺省是 dry-run:只做真实规划、命令渲染、后端路由与秒级 WSL    ██
██  冒烟检查,绝不启动 ISCE2/snaphu。                                   ██
██                                                                      ██
██  只有显式加 --launch 才真正执行 ALOS Baja 同震干涉全链:             ██
██    · 实测约 33 分钟(ext4 基准;/mnt 工作区会上浮),8 线程          ██
██    · 工作区峰值约 32 GB 磁盘                                         ██
██  必须获得用户明确批准后由主线运行(no-heavy-compute 纪律)。         ██
██                                                                      ██
════════════════════════════════════════════════════════════════════════

对拍基准:docs/VALIDATION-isce2-wsl.md(2026-08-12 手工验证链)与
/home/insar/work/baja/stripmapApp.xml、.job/cmd.sh 原件。

用法(项目根目录):
  dry-run   .venv\Scripts\python.exe scripts\agent_baja_rerun.py
  真跑      .venv\Scripts\python.exe scripts\agent_baja_rerun.py --launch
            (--steps 1-6 缺省对齐手工验证链;--steps all 含 7-11 形式化通道)
  可选      --home E:\wsl\baja_agent_home  --distro insar  --fresh

环境变量:INSAR_WSL_DISTRO(默认 insar)、INSAR_ENGINE_PREFIX(默认
E:\miniforge3\envs\insar,7-11 步 MintPy 可行性探测用)。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from insar_agent.brain.facade import Brain  # noqa: E402
from insar_agent.core.db import Database  # noqa: E402
from insar_agent.core.store import Store  # noqa: E402
from insar_agent.engines import default_builder  # noqa: E402
from insar_agent.engines.isce2 import _STRIPMAP_RANGES  # noqa: E402
from insar_agent.loop.driver import Driver  # noqa: E402
from insar_agent.registry.capabilities import REGISTRY  # noqa: E402
from insar_agent.runtime.backend_select import backend_for_step, wsl_distro  # noqa: E402
from insar_agent.runtime.jobs import shell_quote  # noqa: E402
from insar_agent.runtime.probe import ProbeResult, probe_environment  # noqa: E402
from insar_agent.runtime.render import render_plan_files  # noqa: E402
from insar_agent.runtime.wsl import WslPaths  # noqa: E402
from insar_agent.runtime.wsl_probe import merge_wsl_probe, probe_wsl_engines  # noqa: E402

# ---------------- 常量:意图文本 / 手工链金标准 ----------------

SESSION = "baja"
PLAN_TEXT = "用 ALOS 条带数据做 Baja 同震干涉"
DEFAULT_HOME = r"E:\wsl\baja_agent_home"
DEFAULT_ENGINE_PREFIX = r"E:\miniforge3\envs\insar"

# 3-6 步的期望方法(场景包覆写;与 registry/engines 分段表对拍)
STRIPMAP_METHODS = {
    3: "isce2_stripmap_xcorr",
    4: "isce2_stripmap_ifg",
    5: "isce2_stripmap_filter",
    6: "isce2_stripmap_unwrap_snaphu",
}

# 手工验证链 stripmapApp.xml 原件(2026-08-12 从 /home/insar/work/baja 抄录)——
# 渲染 XML 的语义对拍金标准(属性顺序无关,逐字段比对)
MANUAL_STRIPMAP_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<stripmapApp>
  <component name="insar">
    <property name="sensor name">ALOS</property>
    <component name="reference">
      <property name="IMAGEFILE">/home/insar/work/baja/raw/IMG-HH-ALPSRP207600640-H1.0__A</property>
      <property name="LEADERFILE">/home/insar/work/baja/raw/LED-ALPSRP207600640-H1.0__A</property>
      <property name="output">reference</property>
    </component>
    <component name="secondary">
      <property name="IMAGEFILE">/home/insar/work/baja/raw/IMG-HH-ALPSRP227730640-H1.0__A</property>
      <property name="LEADERFILE">/home/insar/work/baja/raw/LED-ALPSRP227730640-H1.0__A</property>
      <property name="output">secondary</property>
      <property name="RESAMPLE_FLAG">dual2single</property>
    </component>
    <property name="demFilename">/home/insar/work/baja/dem/dem.wgs84</property>
    <property name="unwrapper name">snaphu</property>
    <property name="do unwrap">True</property>
  </component>
</stripmapApp>
"""

# WSL 侧秒级冒烟脚本(写进工作区后由 wsl.exe 调 python3 执行):
# XML 语法 + XML 引用的数据路径在位 + 作业契约目录在位。零重型计算。
_SMOKE_PY = """\
# insar-agent Baja 预检冒烟(WSL 侧):XML 语法 / 数据在位 / 作业目录(秒级,零计算)
import os, sys
import xml.etree.ElementTree as ET

base = os.path.dirname(os.path.abspath(__file__))
job_root = sys.argv[1] if len(sys.argv) > 1 else ""
rc = 0
names = sorted(n for n in os.listdir(base)
               if n.startswith("stripmapApp_s") and n.endswith(".xml"))
if not names:
    print("[smoke] ERROR: 未发现渲染出的 stripmapApp_s*.xml")
    sys.exit(1)
checked = set()
for name in names:
    try:
        root = ET.parse(os.path.join(base, name)).getroot()
    except ET.ParseError as exc:
        print(f"[xml] {name}: FAIL 语法错误 {exc}")
        rc = 1
        continue
    print(f"[xml] {name}: OK")
    for prop in root.iter("property"):
        if prop.get("name") in ("IMAGEFILE", "LEADERFILE", "demFilename"):
            path = (prop.text or "").strip()
            if path in checked:
                continue
            checked.add(path)
            ok = os.path.isfile(path)
            size = os.path.getsize(path) / 1e6 if ok else 0.0
            mark = "ok" if ok else "MISS"
            print(f"  [{mark}] {prop.get('name'):<11} {path}"
                  + (f"  {size:.1f} MB" if ok else ""))
            if not ok:
                rc = 1
if job_root:
    ok = os.path.isdir(job_root)
    print(f"[job] 作业契约目录 {job_root}: {'ok' if ok else 'MISS(首跑会自动 mkdir -p)'}")
print(f"[smoke] {'全部通过' if rc == 0 else '存在缺失项'}")
sys.exit(rc)
"""


# ---------------- 预检矩阵 ----------------

@dataclass
class Row:
    item: str
    status: str  # ok | warn | bad
    evidence: str
    critical: bool = True  # bad 且 critical → 阻塞项


@dataclass
class Matrix:
    rows: list[Row] = field(default_factory=list)

    def ok(self, item: str, evidence: str) -> None:
        self.rows.append(Row(item, "ok", evidence))

    def warn(self, item: str, evidence: str) -> None:
        self.rows.append(Row(item, "warn", evidence, critical=False))

    def bad(self, item: str, evidence: str, *, critical: bool = True) -> None:
        self.rows.append(Row(item, "bad", evidence, critical=critical))

    def check(self, item: str, cond: bool, evidence: str) -> bool:
        (self.ok if cond else self.bad)(item, evidence)
        return cond

    @property
    def blockers(self) -> list[Row]:
        return [r for r in self.rows if r.status == "bad" and r.critical]

    def render(self) -> str:
        mark = {"ok": "✓", "warn": "⚠", "bad": "✗"}
        width = max(len(r.item) for r in self.rows) if self.rows else 8
        lines = [f"  {mark[r.status]} {r.item:<{width}}  {r.evidence}" for r in self.rows]
        return "\n".join(lines)


# ---------------- 事件格式化(对齐 real_ridgecrest.py 风格) ----------------

def fmt(e: dict) -> str:
    t = e.get("t")
    if t == "tool.log":
        return f"    | {e['line']}"
    if t == "tool.start":
        return f"  ▶ {e.get('verb', '')} {e.get('label', '')} $ {e.get('cmd', '')}"
    if t == "tool.end":
        return f"  ■ exit={e.get('exit')} {e.get('summary', '')}"
    if t in ("step.start", "step.end"):
        return f"[{t} {e.get('stepId')}]" + (f" exit={e['exit']}" if "exit" in e else "")
    if t == "step.stage":
        return f"    [stage {e.get('stage')}] step {e.get('stepId')}"
    if t == "note":
        return f"  ({e.get('tone')}) {e.get('text')}"
    if t == "say":
        return "AGENT: " + "".join(p if isinstance(p, str) else str(p) for p in e.get("parts", []))
    if t == "plan":
        return "PLAN: " + " / ".join(i["text"] for i in e.get("items", []))
    if t == "thinking":
        return f"THINKING: {e.get('title')}"
    return f"[{t}] " + json.dumps({k: v for k, v in e.items() if k != 't'},
                                  ensure_ascii=False)[:200]


# ---------------- 探测:真实 probe + WSL 引擎晋升 ----------------

def promote_wsl_engines(probe: ProbeResult,
                        engines: tuple[str, ...] = ("isce2", "snaphu")) -> list[str]:
    """把 merge_wsl_probe 并入的「isce2 (wsl)」等键晋升为可行性判定用的裸引擎键。

    背景:planner/feasibility 只认 probe.engines["isce2"] 这类裸键,而 WSL 探测
    结果带 " (wsl)" 后缀(仅面板展示用)—— 生产 make_plan 路径尚未接这道晋升,
    这里在编排脚本内完成(Driver(probe=...) 是设计好的注入点)。执行期由
    backend_for_step 把这些引擎的作业真实路由到 WslJobBackend,闭环成立。
    只补空缺:宿主本地已有的引擎不覆盖。
    """
    promoted: list[str] = []
    for eng in engines:
        wsl_ver = probe.engines.get(f"{eng} (wsl)")
        if wsl_ver and not probe.engines.get(eng):
            probe.engines[eng] = f"{wsl_ver} (wsl)"
            promoted.append(eng)
    return promoted


def build_real_probe(workspace: Path, distro: str, *,
                     wsl_engines_fn=probe_wsl_engines) -> tuple[ProbeResult, dict, list[str]]:
    """真实环境探测:宿主 probe + WSL 内引擎探测(秒级)+ 晋升。全程不 mock。"""
    probe = probe_environment(workspace, check_wsl=True)
    wsl_result = wsl_engines_fn(distro=distro)
    merge_wsl_probe(probe, wsl_result)
    promoted = promote_wsl_engines(probe)
    return probe, wsl_result, promoted


# ---------------- XML 语义比对(属性顺序无关) ----------------

def xml_props(xml_text: str) -> dict[tuple[str, str], str]:
    """展平为 {(组件路径, 属性名): 值};比对与属性顺序无关(ISCE2 按名取值)。"""
    root = ET.fromstring(xml_text)

    out: dict[tuple[str, str], str] = {}

    def walk(el: ET.Element, scope: str) -> None:
        for child in el:
            if child.tag == "property":
                out[(scope, child.get("name") or "")] = (child.text or "").strip()
            elif child.tag == "component":
                walk(child, f"{scope}/{child.get('name')}")

    walk(root, root.tag)
    return out


def diff_props(rendered: str, manual: str) -> list[str]:
    """返回差异清单(空 = 语义一致)。"""
    a, b = xml_props(rendered), xml_props(manual)
    diffs: list[str] = []
    for key in sorted(set(a) | set(b)):
        va, vb = a.get(key), b.get(key)
        if va != vb:
            scope, name = key
            diffs.append(f"{scope}[{name}]: 渲染={va!r} 手工={vb!r}")
    return diffs


# ---------------- Phase A:真实规划回合 ----------------

async def collect(aiter) -> list[dict]:
    return [e async for e in aiter]


def make_driver(store: Store, home: Path, probe: ProbeResult) -> Driver:
    return Driver(store, workspace=home / "ws", brain=Brain(None),
                  allow_simulated=False, probe=probe)


async def plan_turn(driver: Driver, store: Store, mx: Matrix, *, echo=print) -> dict | None:
    """经 Driver.turn 走真实规划;断言意图/计划/方法形态,返回 run 行。"""
    async for e in driver.turn(SESSION, PLAN_TEXT):
        echo(fmt(e))
    run = store.latest_run(SESSION)
    if run is None:
        mx.bad("规划产出 run", "Driver.turn 未产出任何 run(意图未命中?)")
        return None
    mx.check("意图命中 stripmap_coseismic", run["scenario"] == "stripmap_coseismic",
             f"文本「{PLAN_TEXT}」→ scenario={run['scenario']}(Brain 规则层,零 LLM)")
    mx.check("计划可执行(非 planning)", run["status"] in ("ready", "done", "running"),
             f"run={run['run_id']} status={run['status']}"
             + ("" if run["status"] != "planning" else " —— make_plan 存在可行性 problems"))
    mx.check("真实模式(非模拟)", not run["simulated"],
             f"simulated={bool(run['simulated'])}(allow_simulated=False,引擎可行性全过)")
    steps = store.load_steps(run["run_id"])
    mx.check("计划组出 1-11 步", len(steps) == 11,
             f"steps={sorted(s.step_id for s in steps)}")
    methods = {s.step_id: s.method for s in steps}
    ok = all(methods.get(sid) == m for sid, m in STRIPMAP_METHODS.items())
    mx.check("3-6 步为 stripmap 方法", ok,
             " ".join(f"{sid}={methods.get(sid)}" for sid in (3, 4, 5, 6)))
    p1 = next(s for s in steps if s.step_id == 1).params
    mx.check("第 1 步 local_import(WSL 数据源)", methods.get(1) == "local_import",
             f"1={methods.get(1)} source={p1.get('source')}")
    mx.check("第 2 步 dem_local(本地 DEM)", methods.get(2) == "dem_local",
             f"2={methods.get(2)} dem={next(s for s in steps if s.step_id == 2).params.get('dem')}")
    # 3 步参数须与手工 XML 逐字段一致(场景包固化 → 计划落库的闭环)
    p3 = next(s for s in steps if s.step_id == 3).params
    manual = xml_props(MANUAL_STRIPMAP_XML)
    expect = {
        "reference_image": manual[("stripmapApp/insar/reference", "IMAGEFILE")],
        "reference_leader": manual[("stripmapApp/insar/reference", "LEADERFILE")],
        "secondary_image": manual[("stripmapApp/insar/secondary", "IMAGEFILE")],
        "secondary_leader": manual[("stripmapApp/insar/secondary", "LEADERFILE")],
        "resample_flag": manual[("stripmapApp/insar/secondary", "RESAMPLE_FLAG")],
        "dem_path": manual[("stripmapApp/insar", "demFilename")],
    }
    bad = {k: (p3.get(k), v) for k, v in expect.items() if p3.get(k) != v}
    mx.check("3 步路径参数=手工链 WSL 绝对路径", not bad,
             "与 /home/insar/work/baja/stripmapApp.xml 逐字段一致" if not bad else f"{bad}")
    return run


# ---------------- Phase B:CommandPlan 渲染检查(dry-run 核心) ----------------

def inspect_command_plans(driver: Driver, store: Store, run: dict, mx: Matrix,
                          *, echo=print) -> dict[int, object]:
    """为 1-6 步构建 CommandPlan 并打印 argv/cwd/env/关键文件;3-6 步与手工链对拍。"""
    steps = {s.step_id: s for s in store.load_steps(run["run_id"])}
    # 全链方法/参数快照:与 executor 的 run["chain"] 语义一致(stripmapApp/MintPy
    # 这类整链共享配置的引擎按它渲染,4-6 段与 3 段同源)
    run_ctx = {"simulated": 0, "run_id": run["run_id"],
               "chain": {sid: {"method": s.method, "params": s.params}
                         for sid, s in steps.items()}}
    ws = driver.workspace
    plans: dict[int, object] = {}
    xml_s03 = ""
    for sid in (1, 2, 3, 4, 5, 6):
        cap = REGISTRY[sid]
        step = steps[sid]
        plan = default_builder(cap=cap, method=step.method, params=step.params,
                               run=run_ctx, workspace=ws)
        plans[sid] = plan
        echo(f"\n-- 第 {sid} 步「{cap.name}」method={step.method}")
        echo(f"   argv: {plan.argv}")
        echo(f"   cwd : {plan.cwd}")
        try:
            echo(f"   cwd(WSL 视角): {WslPaths.to_wsl(plan.cwd)}")
        except ValueError:
            pass
        echo(f"   env : {plan.env}")
        echo(f"   files: {sorted(plan.files)}")
        if sid in STRIPMAP_METHODS:
            script = plan.files[f"isce2/run_s{sid:02d}.sh"]
            for line in script.strip().splitlines():
                echo(f"     run| {line}")
            start, end = _STRIPMAP_RANGES[sid]
            wanted = f"--steps --start={start} --end={end}"
            mx.check(f"s{sid} 命令形态(--steps 分段)", wanted in script and "nice -n 10" in script,
                     f"nice -n 10 stripmapApp.py … {wanted}(手工 cmd.sh 同款)")
            xml = plan.files[f"isce2/stripmapApp_s{sid:02d}.xml"]
            if sid == 3:
                xml_s03 = xml
                for line in xml.strip().splitlines():
                    echo(f"     xml| {line}")
                diffs = diff_props(xml, MANUAL_STRIPMAP_XML)
                mx.check("s3 XML=手工原件(语义比对)", not diffs,
                         "属性顺序无关逐字段一致" if not diffs else "; ".join(diffs))
            else:
                mx.check(f"s{sid} XML 与 s3 同源一份配置", xml == xml_s03,
                         "分段共用同一 stripmapApp 配置(实测形态)")
    return plans


# ---------------- Phase B2:导入脚本试运行(秒级,临时目录,零副作用) ----------------

def rehearse_import_scripts(plans: dict[int, object], mx: Matrix, home: Path,
                            *, echo=print) -> None:
    """在临时目录里真实执行 1/2 步渲染出的导入/核验脚本:只读核验 WSL 数据在位,
    清单写进临时目录 —— 不触碰 run 状态,不写代理工作区,验证宿主经
    \\\\wsl.localhost 的 9p 通路(--launch 时这两步走的就是这条路)。"""
    import tempfile

    for sid in (1, 2):
        plan = plans[sid]
        label = {1: "s1 导入脚本试运行(WSL 清单登记)", 2: "s2 DEM 核验脚本试运行"}[sid]
        with tempfile.TemporaryDirectory(dir=str(home), prefix="rehearsal_") as tmp:
            tmp_path = Path(tmp)
            for rel, content in plan.files.items():
                target = tmp_path / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            try:
                cp = subprocess.run(plan.argv, cwd=tmp, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=120,
                                    env={**os.environ, **plan.env},
                                    creationflags=0x08000000 if sys.platform == "win32" else 0)
            except (OSError, subprocess.TimeoutExpired) as exc:
                mx.bad(label, f"脚本启动失败:{exc}")
                continue
            for line in (cp.stdout or "").splitlines():
                echo(f"    s{sid}| {line}")
            manifest = tmp_path / "data" / ("slc" if sid == 1 else "dem") / "manifest.json"
            mx.check(label, cp.returncode == 0 and manifest.exists(),
                     f"rc={cp.returncode},manifest={'已生成' if manifest.exists() else '缺失'}"
                     f"(经 \\\\wsl.localhost 只读核验)")


# ---------------- Phase C:后端路由(backend_for_step 真探测) ----------------

def route_backends(store: Store, run: dict, mx: Matrix, *, env=None, runner=None,
                   echo=print) -> None:
    steps = {s.step_id: s for s in store.load_steps(run["run_id"])}
    expect_wsl = set(STRIPMAP_METHODS)
    got: dict[int, str] = {}
    for sid in sorted(steps):
        cap = REGISTRY[sid]
        m = cap.method(steps[sid].method)
        backend = backend_for_step(engine=m.engine if m else "-",
                                   simulated=bool(run["simulated"]),
                                   env=env, runner=runner)
        name = type(backend).__name__
        got[sid] = name
        extra = ""
        if name == "WslJobBackend":
            paths = backend.paths
            extra = (f"  distro={paths.distro}  作业根={paths.root}/.jobs"
                     f"(宿主视图 {paths.host_root('.jobs')})")
            backend.release_keepalive()  # 路由探测不留保活进程
        echo(f"  s{sid:02d} {cap.name:<6} engine={m.engine if m else '-':<8} → {name}{extra}")
    ok = (all(got[s] == "WslJobBackend" for s in expect_wsl)
          and all(got[s] == "LocalJobBackend" for s in got if s not in expect_wsl))
    mx.check("后端路由:3-6 步 WslJobBackend,其余本地", ok,
             " ".join(f"{s}:{'WSL' if got[s] == 'WslJobBackend' else 'local'}"
                      for s in sorted(got)))


# ---------------- Phase D:预算(方法 extra 实测数字 + 声明公式) ----------------

def print_budget(store: Store, run: dict, probe: ProbeResult, workspace: Path,
                 *, echo=print) -> None:
    steps = {s.step_id: s for s in store.load_steps(run["run_id"])}
    scenes = int(steps[1].params.get("scenes", 2) or 2)
    pairs = int(steps[4].params.get("pairs", 1) or 1)
    echo(f"  输入规模:scenes={scenes} pairs={pairs}(单干涉对)")
    for sid in (3, 4, 5, 6):
        cap = REGISTRY[sid]
        m = cap.method(steps[sid].method)
        est = cap.disk.estimate_gb(pairs=pairs, scenes=scenes)
        echo(f"  s{sid:02d} {cap.name:<6} 声明磁盘≈{est:.1f} GB(峰值×{cap.disk.peak_multiplier})"
             f" 超时 idle={cap.timeouts.idle / 60:.0f}min/total={cap.timeouts.total / 3600:.0f}h")
        if m and m.extra:
            echo(f"        实测:{m.extra}")
    echo("  全链实测:33 min / 工作区峰值 32 GB(ext4 基准,docs/VALIDATION-isce2-wsl.md)")
    echo(f"  工作区磁盘:{probe.disk_free_gb:.0f} GB 可用(@ {workspace})")


# ---------------- Phase E:WSL 秒级冒烟(语法/在位;绝不起 stripmapApp) ----------------

def _default_wsl_run(argv: list[str], timeout: float) -> subprocess.CompletedProcess:
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                          creationflags=flags, encoding="utf-8", errors="replace",
                          env={**os.environ, "WSL_UTF8": "1"})


def smoke_wsl(driver: Driver, plans: dict[int, object], distro: str, mx: Matrix,
              *, wsl_run=_default_wsl_run, echo=print) -> None:
    """把 3-6 步渲染文件落进工作区,在 WSL 内做 ET.parse + 路径在位检查(秒级)。"""
    ws = driver.workspace
    for sid in STRIPMAP_METHODS:
        render_plan_files(ws, plans[sid])
    smoke_rel = "isce2/_smoke_check.py"
    (ws / "isce2").mkdir(parents=True, exist_ok=True)
    (ws / smoke_rel).write_text(_SMOKE_PY, encoding="utf-8", newline="\n")
    smoke_posix = WslPaths.to_wsl(ws / smoke_rel)
    job_root = str(WslPaths(distro=distro).root / ".jobs")
    argv = ["wsl.exe", "-d", distro, "-u", "root", "--exec", "bash", "-lc",
            f"python3 {shell_quote(str(smoke_posix))} {shell_quote(job_root)}"]
    echo(f"  $ {' '.join(argv[:6])} … python3 {smoke_posix}")
    try:
        cp = wsl_run(argv, 120.0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        mx.bad("WSL 冒烟(XML 语法+数据在位)", f"wsl.exe 调用失败:{exc}")
        return
    for line in (cp.stdout or "").splitlines():
        echo(f"    {line}")
    if (cp.stderr or "").strip():
        for line in cp.stderr.strip().splitlines()[:5]:
            echo(f"    [stderr] {line}")
    mx.check("WSL 冒烟(XML 语法+数据在位)", cp.returncode == 0,
             f"rc={cp.returncode}:4 份分段 XML 可解析,IMG/LED/DEM 均在位"
             if cp.returncode == 0 else f"rc={cp.returncode},见上方 MISS 行")


# ---------------- 已知差异/风险(与手工链的 diff 清单) ----------------

def known_gaps(mx: Matrix, run: dict, workspace: Path) -> None:
    try:
        mnt = WslPaths.to_wsl(workspace)
    except ValueError:
        mnt = "?"
    mx.warn("工作区在 /mnt(9p)",
            f"手工链 33 min 是 ext4 基准;代理工作区 {workspace} → {mnt},"
            "重 IO 段耗时会上浮(§4.8 迁移 Linux 工作区列为后续工作)")
    mx.warn("7-11 步为形式化通道",
            "MintPy 数据面仍是 HyP3 形态(engines/mintpy.py processor=hyp3),"
            "单干涉对条带产物未接 —— --launch 缺省只跑 1-6(与手工验证链同域),"
            "--steps all 才含 7-11(预期第 7 步失败,诚实呈现)")
    mx.warn("生产 make_plan 未并 WSL 引擎探测",
            "probe_environment 不含 WSL 内引擎;本脚本经 Driver(probe=…) 注入晋升后的"
            "真实探测(API /api/env 已并同款数据,规划路径接线列为后续工作)")
    mx.warn("stripmapApp 调用形态差异",
            "手工链 python3 $ISCE_HOME/applications/stripmapApp.py,代理经登录 shell "
            "PATH 直呼 stripmapApp.py —— 同一解释器/入口,等价;分段各渲染一份同内容 XML")


# ---------------- 启动指引 ----------------

def launch_guide(home: Path) -> str:
    py = r".venv\Scripts\python.exe"
    return f"""
━━━ 主线一键启动指引(需用户明确批准后执行)━━━━━━━━━━━━━━━━━━━━━━
  命令(项目根目录):
    {py} scripts\\agent_baja_rerun.py --launch
  范围:1-6 步(数据登记 → 配准 → 干涉 → 滤波 → 解缠+地理编码),
        与 2026-08-12 手工验证链同域;--steps all 才含 7-11 形式化通道。
  预计:约 33 分钟(ext4 实测;/mnt 工作区会上浮,首段两景聚焦 ~20 min 占大头)
        + 峰值 32 GB 磁盘(落在 {home} 所在盘)。
  监控点:
    · 事件流:本脚本 stdout 实时打印 step.stage / tool.log;
    · 作业目录:\\\\wsl.localhost\\<distro>\\home\\insar\\work\\.jobs\\<run>\\sNN\\a1\\job.log;
    · 里程碑:s03 结束(~20 min,产 coregisteredSlc/)→ s04/s05(分钟级)→
      s06(~13 min,snaphu ~10 min,产 filt_topophase.unw(.geo));
    · 断点:随时 Ctrl+C / KILL 干预 —— 五阶段执行器支持续跑,不重跑已完成阶段。
  产物:<home>\\ws\\isce2\\interferogram\\filt_topophase.unw(+.conncomp/.geo)
        + provenance/等价命令脚本(run 收尾自动导出)。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


# ---------------- --launch:真实执行(主线专用) ----------------

async def launch(driver: Driver, store: Store, run: dict, step_ids: list[int] | None,
                 *, echo=print) -> int:
    t0 = time.time()
    echo("=" * 70)
    echo(f"Phase L · 真实执行 {('步骤 ' + str(step_ids)) if step_ids else '全部待跑步骤'}"
         f"(重型计算已获批准)")
    echo("=" * 70)
    async for e in driver.execute(SESSION, run["run_id"], step_ids=step_ids):
        echo(fmt(e))
    fresh = store.get_run(run["run_id"]) or {}
    echo("=" * 70)
    echo(f"RUN: {fresh.get('run_id')}  status={fresh.get('status')}"
         f"  wall={time.time() - t0:.0f}s")
    for s in store.load_steps(run["run_id"]):
        arts = store.artifacts_of(run["run_id"], s.step_id)
        echo(f"  step {s.step_id:2d} {s.name:6s} {s.state:12s} stage={s.stage:9s}"
             f" run_ok={s.run_ok} artifacts={len(arts)}"
             + (f" failure={s.failure_class}" if s.failure_class else ""))
    if fresh.get("status") == "done":
        from insar_agent.audit.contract import load_contract
        from insar_agent.core.ledger import export_provenance

        prov = export_provenance(store, run["run_id"], contract=load_contract(),
                                 workspace=driver.workspace)
        echo(f"EVIDENCE: {prov['evidence_level']}  (ceiling: {prov['evidence']['ceiling']}"
             f" — {prov['evidence']['ceiling_reason']})")
    return 0 if fresh.get("status") == "done" else 1


# ---------------- 主流程 ----------------

def parse_steps(text: str) -> list[int] | None:
    if text.strip().lower() in ("all", "1-11"):
        return None  # driver 自取全部待跑步骤
    lo, _, hi = text.partition("-")
    return list(range(int(lo), int(hi or lo) + 1))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ALOS Baja 条带链代理编排(缺省 dry-run)")
    ap.add_argument("--launch", action="store_true",
                    help="真正执行(重型计算,约 33 min/32 GB;须用户明确批准)")
    ap.add_argument("--steps", default="1-6",
                    help="--launch 的执行范围:1-6(缺省,对齐手工验证链)或 all")
    ap.add_argument("--home", default=DEFAULT_HOME, help=f"独立 INSAR_HOME(默认 {DEFAULT_HOME})")
    ap.add_argument("--distro", default=None, help="WSL 发行版(默认 INSAR_WSL_DISTRO 或 insar)")
    ap.add_argument("--fresh", action="store_true", help="清空 home 后重来")
    args = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.environ.setdefault("INSAR_ENGINE_PREFIX", DEFAULT_ENGINE_PREFIX)
    if args.distro:
        os.environ["INSAR_WSL_DISTRO"] = args.distro
    distro = wsl_distro()

    home = Path(args.home)
    if args.fresh and home.exists():
        shutil.rmtree(home)
    (home / "ws").mkdir(parents=True, exist_ok=True)  # 先建好,磁盘探测才有落点
    db = Database(home / "insar.db")
    store = Store(db)
    mx = Matrix()

    print("=" * 70)
    print(f"Phase 0 · 真实环境探测(宿主 + WSL:{distro});INSAR_HOME={home}")
    print("=" * 70, flush=True)
    probe, wsl_result, promoted = build_real_probe(home / "ws", distro)
    for eng, ver in sorted(probe.engines.items()):
        print(f"  {eng:<14} {ver or '✗ 缺失'}")
    mx.check(f"WSL 发行版 {distro} 可达", bool(wsl_result.get("ok")),
             wsl_result.get("error") or f"engine_prefix={wsl_result.get('engine_prefix')}")
    mx.check("isce2 引擎可用(WSL 晋升)", bool(probe.engines.get("isce2")),
             f"isce2={probe.engines.get('isce2')}(晋升:{promoted or '无需'})")
    mx.check("7-11 步引擎(宿主 mintpy/pyaps)",
             bool(probe.engines.get("mintpy") and probe.engines.get("pyaps")),
             f"mintpy={probe.engines.get('mintpy')} pyaps={probe.engines.get('pyaps')}"
             f"(INSAR_ENGINE_PREFIX={os.environ.get('INSAR_ENGINE_PREFIX')})")

    driver = make_driver(store, home, probe)

    print("=" * 70)
    print(f"Phase A · 规划回合(Driver.turn:「{PLAN_TEXT}」)")
    print("=" * 70, flush=True)
    run = asyncio.run(plan_turn(driver, store, mx))
    if run is None or mx.blockers:
        print("\n预检矩阵:\n" + mx.render())
        print("\n阻塞项:规划阶段即失败,后续阶段跳过")
        db.close()
        return 1

    print("\n" + "=" * 70)
    print("Phase B · CommandPlan 渲染(1-6 步;3-6 与手工链对拍)")
    print("=" * 70, flush=True)
    plans = inspect_command_plans(driver, store, run, mx)

    print("\n" + "=" * 70)
    print("Phase B2 · 导入脚本试运行(临时目录;经 \\\\wsl.localhost 只读核验)")
    print("=" * 70, flush=True)
    rehearse_import_scripts(plans, mx, home)

    print("\n" + "=" * 70)
    print("Phase C · 后端路由(backend_for_step 真探测)")
    print("=" * 70, flush=True)
    route_backends(store, run, mx)

    print("\n" + "=" * 70)
    print("Phase D · 预算(实测数字 + 声明公式)")
    print("=" * 70, flush=True)
    print_budget(store, run, probe, home / "ws")

    print("\n" + "=" * 70)
    print("Phase E · WSL 秒级冒烟(XML 语法 / 数据在位;绝不起 stripmapApp)")
    print("=" * 70, flush=True)
    smoke_wsl(driver, plans, distro, mx)

    known_gaps(mx, run, driver.workspace)

    print("\n" + "=" * 70)
    print("预检结论矩阵")
    print("=" * 70)
    print(mx.render())
    blockers = mx.blockers
    print(f"\n阻塞项:{len(blockers)} 个"
          + ("".join(f"\n  ✗ {r.item} —— {r.evidence}" for r in blockers) if blockers else "(无)"))

    if not args.launch:
        print(launch_guide(home))
        print("dry-run 结束:未启动任何重型计算。--launch 才真跑(须用户明确批准)。")
        db.close()
        return 1 if blockers else 0

    if blockers:
        print("\n--launch 被拒绝:预检存在阻塞项(见上),先修复再启动。")
        db.close()
        return 1
    rc = asyncio.run(launch(driver, store, run, parse_steps(args.steps)))
    db.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
