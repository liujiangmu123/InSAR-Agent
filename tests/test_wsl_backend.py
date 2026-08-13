"""WslJobBackend 接线测试(runtime/backend_select + runtime/wsl)。

三层覆盖,前两层全部走假 runner,绝不触碰真实 WSL:
  1. 工厂路由:引擎 × WSL 可达性 × INSAR_JOB_BACKEND/INSAR_WSL_DISTRO 全分支,
     以及 driver 每步选择与 executor 作业目录根的接线点。
  2. WslJobBackend 契约操作:prepare/launch/state/cancel/rc 解析与路径换算。
  3. 真实 WSL 冒烟(自动跳过):秒级 echo 作业,作业目录用后即删;
     绝不触碰 /home/insar/work/baja,不跑任何重型计算。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time
import uuid
from functools import lru_cache
from importlib import resources
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from insar_agent.registry.model import Capability, Method
from insar_agent.runtime import backend_select
from insar_agent.runtime.backend_select import (
    WSL_ENGINES,
    backend_for_step,
    select_backend,
    wsl_distro,
    wsl_reachable,
)
from insar_agent.runtime.executor import _job_dir
from insar_agent.runtime.jobs import CommandPlan, LocalJobBackend
from insar_agent.runtime.wsl import WslJobBackend, WslPaths, wsl_status

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class FakeRunner:
    """按脚本(argv 末位)子串匹配返回预设结果;记录全部调用供断言。

    rules: list[(子串, (rc, stdout, stderr) | Exception)]
    """

    def __init__(self, rules=()):
        self.rules = list(rules)
        self.calls: list[tuple[list[str], float]] = []

    def __call__(self, argv: list[str], timeout: float) -> subprocess.CompletedProcess:
        self.calls.append((list(argv), timeout))
        script = argv[-1]
        for sub, res in self.rules:
            if sub in script:
                if isinstance(res, Exception):
                    raise res
                rc, out, err = res
                return subprocess.CompletedProcess(argv, rc, stdout=out, stderr=err)
        return subprocess.CompletedProcess(argv, 127, stdout="", stderr=f"no rule for: {script}")

    def scripts(self) -> list[str]:
        return [argv[-1] for argv, _ in self.calls]


def runner_with_distros(*distros: str) -> FakeRunner:
    return FakeRunner([("-q", (0, "\n".join(distros) + "\n", ""))])


# ================================================================ wsl_status

def test_wsl_status_strips_utf16_nul_and_blank_lines():
    runner = FakeRunner([("-q", (0, "i\x00n\x00s\x00a\x00r\x00\n\x00\nUbuntu\n\n", ""))])
    st = wsl_status(runner)
    assert st == {"installed": True, "distros": ["insar", "Ubuntu"]}


def test_wsl_status_no_wsl_exe_is_not_installed():
    st = wsl_status(FakeRunner([("-q", FileNotFoundError("wsl.exe"))]))
    assert st == {"installed": False, "distros": []}


# ================================================================ 工厂路由

def test_route_wsl_engines_to_wsl_when_distro_reachable():
    for engine in sorted(WSL_ENGINES):  # isce2 与 snaphu 两个分支都要走到
        backend = select_backend(engine, env={}, runner=runner_with_distros("insar"))
        assert isinstance(backend, WslJobBackend), engine
        assert backend.paths.distro == "insar"  # 默认发行版(实测机命名)
        assert backend.user == "root"  # 实测教训:发行版用户 ≠ 发行版名,统一 root


def test_route_to_local_when_wsl_missing_or_distro_absent():
    # wsl.exe 不存在
    backend = select_backend("isce2", env={}, runner=FakeRunner([("-q", FileNotFoundError())]))
    assert isinstance(backend, LocalJobBackend)
    # wsl.exe 在,但目标发行版没注册
    backend = select_backend("isce2", env={}, runner=runner_with_distros("Ubuntu"))
    assert isinstance(backend, LocalJobBackend)


def test_non_wsl_engines_stay_local_and_never_probe():
    runner = FakeRunner([("-q", (0, "insar\n", ""))])
    for engine in ("mintpy", "-", "", "pystamps", "hyp3", "dem_service"):
        backend = select_backend(engine, env={}, runner=runner)
        assert isinstance(backend, LocalJobBackend), engine
    assert runner.calls == []  # 本地引擎绝不为路由决策起 wsl.exe


def test_forced_local_overrides_engine_without_probe():
    runner = runner_with_distros("insar")
    backend = select_backend("isce2", env={"INSAR_JOB_BACKEND": "local"}, runner=runner)
    assert isinstance(backend, LocalJobBackend)
    assert runner.calls == []


def test_forced_wsl_overrides_engine_without_probe():
    runner = FakeRunner([("-q", AssertionError("强制 wsl 不该探测"))])
    backend = select_backend("mintpy", env={"INSAR_JOB_BACKEND": "wsl"}, runner=runner)
    assert isinstance(backend, WslJobBackend)
    assert backend.paths.distro == "insar"
    assert runner.calls == []


def test_forced_unknown_value_fails_loudly():
    with pytest.raises(ValueError, match="INSAR_JOB_BACKEND"):
        select_backend("isce2", env={"INSAR_JOB_BACKEND": "docker"})


def test_distro_env_respected_and_matched_case_insensitively():
    env = {"INSAR_WSL_DISTRO": "InSAR"}
    assert wsl_distro(env) == "InSAR"
    backend = select_backend("snaphu", env=env, runner=runner_with_distros("insar"))
    assert isinstance(backend, WslJobBackend)
    assert backend.paths.distro == "InSAR"  # 用用户给的名字起 wsl.exe
    assert not wsl_reachable("insar", FakeRunner([("-q", (0, "Ubuntu\n", ""))]))


def test_backend_for_step_override_and_simulated_win():
    sentinel = LocalJobBackend(hb_stale=42.0)
    # 显式注入永远最优先(测试/运维接管整个 run)
    assert backend_for_step(engine="isce2", override=sentinel) is sentinel
    # 模拟运行即使被强制 wsl 也留在本地(合成产物是纯 Python)
    backend = backend_for_step(engine="isce2", simulated=True,
                               env={"INSAR_JOB_BACKEND": "wsl"})
    assert isinstance(backend, LocalJobBackend)


# ================================================================ 接线点

def test_executor_job_dir_follows_backend_job_root(tmp_path):
    wsl_backend = WslJobBackend(paths=WslPaths(distro="insar"), runner=FakeRunner(),
                                keepalive=False)
    ctx = SimpleNamespace(backend=wsl_backend, workspace=tmp_path)
    job = _job_dir(ctx, "r1", 3, 1)
    assert job == Path(r"\\wsl.localhost\insar\home\insar\work\.jobs") / "r1" / "s03" / "a1"
    # LocalJobBackend 无 job_root:维持工作区 .jobs 原布局(无 WSL 时行为不变)
    ctx_local = SimpleNamespace(backend=LocalJobBackend(), workspace=tmp_path)
    assert _job_dir(ctx_local, "r1", 3, 1) == tmp_path / ".jobs" / "r1" / "s03" / "a1"


_CAP = Capability(
    id=99, name="接线测试步骤", deps=(),
    methods=(Method("m_isce2", "m_isce2", "isce2"),
             Method("m_snaphu", "m_snaphu", "snaphu"),
             Method("m_mintpy", "m_mintpy", "mintpy"),
             Method("m_pure", "m_pure", "-")),
    default_method="m_pure")


def _driver(tmp_path, backend=None):
    from insar_agent.loop.driver import Driver
    return Driver(None, workspace=tmp_path / "ws", backend=backend)


def test_driver_backend_for_routes_per_engine(tmp_path, monkeypatch):
    monkeypatch.delenv("INSAR_JOB_BACKEND", raising=False)
    monkeypatch.delenv("INSAR_WSL_DISTRO", raising=False)
    monkeypatch.setattr(backend_select, "wsl_status",
                        lambda runner=None: {"installed": True, "distros": ["insar"]})
    driver = _driver(tmp_path)
    run = {"simulated": 0}
    assert isinstance(driver._backend_for(run, _CAP, "m_isce2"), WslJobBackend)
    assert isinstance(driver._backend_for(run, _CAP, "m_snaphu"), WslJobBackend)
    assert isinstance(driver._backend_for(run, _CAP, "m_mintpy"), LocalJobBackend)
    assert isinstance(driver._backend_for(run, _CAP, "m_pure"), LocalJobBackend)
    # 方法不在 capability 声明里(异常数据)→ 引擎按 '-' 处理,本地兜底
    assert isinstance(driver._backend_for(run, _CAP, "ghost"), LocalJobBackend)
    # 模拟运行:引擎哪怕是 isce2 也本地跑
    assert isinstance(driver._backend_for({"simulated": 1}, _CAP, "m_isce2"),
                      LocalJobBackend)


def test_driver_explicit_backend_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(backend_select, "wsl_status",
                        lambda runner=None: {"installed": True, "distros": ["insar"]})
    injected = LocalJobBackend(hb_stale=7.0)
    driver = _driver(tmp_path, backend=injected)
    assert driver._backend_for({"simulated": 0}, _CAP, "m_isce2") is injected
    assert driver._exec_ctx().backend is injected


def test_driver_no_wsl_behaves_exactly_as_before(tmp_path, monkeypatch):
    monkeypatch.delenv("INSAR_JOB_BACKEND", raising=False)
    monkeypatch.setattr(backend_select, "wsl_status",
                        lambda runner=None: {"installed": False, "distros": []})
    driver = _driver(tmp_path)
    backend = driver._backend_for({"simulated": 0}, _CAP, "m_isce2")
    assert isinstance(backend, LocalJobBackend)
    assert isinstance(driver._exec_ctx().backend, LocalJobBackend)


# ================================================================ 路径换算

def test_to_wsl_windows_drive_maps_to_mnt():
    assert WslPaths.to_wsl(r"E:\ws\demo") == PurePosixPath("/mnt/e/ws/demo")
    assert WslPaths.to_wsl(Path(r"c:\a b\c")) == PurePosixPath("/mnt/c/a b/c")


def test_to_wsl_posix_passthrough_and_unc_strip():
    assert WslPaths.to_wsl("/home/insar/work") == PurePosixPath("/home/insar/work")
    assert WslPaths.to_wsl(r"\\wsl.localhost\insar\home\x") == PurePosixPath("/home/x")
    assert WslPaths.to_wsl(r"\\wsl$\insar\home\x") == PurePosixPath("/home/x")


def test_to_wsl_rejects_unmappable_paths():
    with pytest.raises(ValueError):
        WslPaths.to_wsl(r"\\fileserver\share\a")  # 非 WSL 的网络路径,绝不猜
    with pytest.raises(ValueError):
        WslPaths.to_wsl("relative/path")


def test_to_posix_handles_windows_unc_anchor():
    conv = WslJobBackend._to_posix
    assert conv(Path(r"\\wsl.localhost\insar\home\insar\work\.jobs\r1\s03\a1")) \
        == "/home/insar/work/.jobs/r1/s03/a1"
    assert conv(Path(r"\\wsl$\insar\home\insar\work")) == "/home/insar/work"
    with pytest.raises(ValueError):
        conv(Path(r"E:\ws\.jobs\r1"))  # 作业目录必须在 Linux fs(§4.8)


def test_host_root_and_job_root_point_into_linux_fs():
    paths = WslPaths(distro="insar")
    assert paths.host_root(".jobs") == Path(r"\\wsl.localhost\insar\home\insar\work\.jobs")
    backend = WslJobBackend(paths=paths, runner=FakeRunner(), keepalive=False)
    # 刻意无视 workspace:契约文件的高频小写入不能落 9p
    assert backend.job_root(Path(r"E:\whatever")) == paths.host_root(".jobs")


# ================================================================ 契约操作(假 wsl.exe)

def _backend(tmp_path, rules=None) -> tuple[WslJobBackend, FakeRunner]:
    runner = FakeRunner(rules if rules is not None else [("mkdir -p", (0, "", ""))])
    backend = WslJobBackend(paths=WslPaths(distro="insar"), runner=runner, keepalive=False)
    return backend, runner


_PLAN = CommandPlan(
    argv=["bash", "isce2/run_s03.sh"], cwd=r"E:\ws\demo",
    env={"OMP_NUM_THREADS": "8"}, files={}, shell_line="bash isce2/run_s03.sh")


def test_prepare_renders_contract_files_with_wsl_paths(tmp_path):
    backend, runner = _backend(tmp_path)
    job = tmp_path / "a1"
    backend.prepare(job, _PLAN, wsl_job_dir="/home/insar/work/.jobs/r1/s03/a1")

    argv, _timeout = runner.calls[0]
    assert argv[:7] == ["wsl.exe", "-d", "insar", "-u", "root", "--exec", "bash"]
    assert "mkdir -p /home/insar/work/.jobs/r1/s03/a1" in argv[-1]

    cmd = (job / "cmd.sh").read_text(encoding="utf-8")
    assert "cd /mnt/e/ws/demo" in cmd  # cwd 已换算成 WSL 坐标系
    assert r"# host cwd: E:\ws\demo" in cmd  # 原始宿主路径留痕,可 diff
    assert "export OMP_NUM_THREADS=8" in cmd
    assert cmd.rstrip().endswith("bash isce2/run_s03.sh")
    assert b"\r" not in (job / "cmd.sh").read_bytes()  # CRLF 会让 bash 报错(实测教训)

    wrapper = (job / "wrapper.sh").read_text(encoding="utf-8")
    packaged = (resources.files("insar_agent.runtime") / "wsl_wrapper.sh").read_text("utf-8")
    assert wrapper == packaged
    assert b"\r" not in (job / "wrapper.sh").read_bytes()


def test_prepare_fails_loudly_when_wsl_mkdir_fails(tmp_path):
    backend, _runner = _backend(tmp_path, rules=[("mkdir -p", (1, "", "mount failed"))])
    job = tmp_path / "a1"
    with pytest.raises(RuntimeError, match="mount failed"):
        backend.prepare(job, _PLAN, wsl_job_dir="/home/insar/work/.jobs/r1/s03/a1")
    assert not (job / "cmd.sh").exists()  # 显式失败,不留半成品契约


def test_launch_starts_detached_wrapper(tmp_path):
    backend, runner = _backend(tmp_path, rules=[("setsid", (0, "", ""))])
    backend.launch(tmp_path, wsl_job_dir="/home/insar/work/.jobs/r1/s03/a1")
    argv, timeout = runner.calls[0]
    assert argv[:7] == ["wsl.exe", "-d", "insar", "-u", "root", "--exec", "bash"]
    script = argv[-1]
    # setsid --fork:实测 nohup+&+disown 会被 WSL 会话回收连坐,独立会话才存活
    assert ("setsid --fork bash /home/insar/work/.jobs/r1/s03/a1/wrapper.sh"
            " /home/insar/work/.jobs/r1/s03/a1 ") in script
    assert ">/dev/null 2>&1 </dev/null" in script
    assert timeout == 15.0


def test_launch_fails_loudly_when_wrapper_cannot_start(tmp_path):
    backend, _ = _backend(
        tmp_path, rules=[("setsid", (127, "", "setsid: command not found"))])
    with pytest.raises(RuntimeError, match="setsid"):
        backend.launch(tmp_path, wsl_job_dir="/home/insar/work/.jobs/r1/s03/a1")


def test_state_finished_parses_rc(tmp_path):
    backend, runner = _backend(tmp_path, rules=[])
    (tmp_path / "job.rc").write_text("143")
    st = backend.state(tmp_path)
    assert (st.kind, st.exit_code) == ("finished", 143)
    assert runner.calls == []  # rc 在场就不需要跨边界核实

    (tmp_path / "job.rc").write_text("not-a-number")
    assert backend.state(tmp_path).exit_code == -1  # 解析失败按 -1,仍算 finished

    (tmp_path / "job.rc").write_text("")
    assert backend.state(tmp_path).exit_code == -1


def test_state_unknown_before_wrapper_writes_pid(tmp_path):
    backend, _ = _backend(tmp_path, rules=[])
    assert backend.state(tmp_path).kind == "unknown"


def test_state_alive_orphaned_by_proc_starttime(tmp_path):
    (tmp_path / "job.pid").write_text("4242\n")
    (tmp_path / "job.start").write_text("777\n")

    backend, runner = _backend(tmp_path, rules=[("/proc/4242/stat", (0, "777\n", ""))])
    assert backend.state(tmp_path).kind == "alive"
    assert "awk" in runner.scripts()[0]  # 判活在 WSL 内核实,绝不宿主 os.kill

    backend, _ = _backend(tmp_path, rules=[("/proc/4242/stat", (0, "888\n", ""))])
    assert backend.state(tmp_path).kind == "orphaned"  # starttime 不符:PID 被复用

    backend, _ = _backend(tmp_path, rules=[("/proc/4242/stat", (0, "", ""))])
    assert backend.state(tmp_path).kind == "orphaned"  # 进程没了且没写 rc

    backend, _ = _backend(
        tmp_path, rules=[("/proc/4242/stat", subprocess.TimeoutExpired("wsl.exe", 10))])
    assert backend.state(tmp_path).kind == "unknown"  # WSL 不可达:保守 unknown

    backend, _ = _backend(tmp_path, rules=[("/proc/4242/stat", OSError("boom"))])
    assert backend.state(tmp_path).kind == "unknown"


def test_state_rejects_non_numeric_pid_without_shelling_out(tmp_path):
    """job.pid 内容非纯数字(契约损坏/篡改):按 orphaned 处置,且绝不把它
    插进以 root 执行的 bash(REVIEW-r2 P2-4 注入面收口)。"""
    (tmp_path / "job.pid").write_text("1/stat; touch /tmp/pwned #\n")
    backend, runner = _backend(tmp_path, rules=[])
    st = backend.state(tmp_path)
    assert (st.kind, st.exit_code) == ("orphaned", None)
    assert runner.calls == []  # 坏 pid 根本不进 wsl.exe


def test_state_rejects_empty_or_padded_pid(tmp_path):
    backend, runner = _backend(tmp_path, rules=[])
    (tmp_path / "job.pid").write_text("\n")            # 空内容
    assert backend.state(tmp_path).kind == "orphaned"
    (tmp_path / "job.pid").write_text("-4242\n")       # 负号也不是合法 pid 形态
    assert backend.state(tmp_path).kind == "orphaned"
    assert runner.calls == []


def test_prepare_rejects_invalid_env_key_before_side_effects(tmp_path):
    """env 键名逐字进 cmd.sh 的 export 语句:白名单([A-Z_][A-Z0-9_]*)外
    显式拒绝,且失败发生在一切副作用之前(不 spawn wsl.exe、不留半截作业目录)。"""
    backend, runner = _backend(tmp_path)
    bad = CommandPlan(argv=["true"], cwd=r"E:\ws",
                      env={"OMP_NUM_THREADS; rm -rf /": "8"}, files={},
                      shell_line="true")
    with pytest.raises(ValueError, match="环境变量名"):
        backend.prepare(tmp_path / "a1", bad,
                        wsl_job_dir="/home/insar/work/.jobs/r1/s03/a1")
    assert runner.calls == []
    assert not (tmp_path / "a1").exists()
    # 小写键同样拒绝(白名单是精确形态,不是"看起来无害")
    lower = CommandPlan(argv=["true"], cwd=r"E:\ws", env={"path": "/x"}, files={},
                        shell_line="true")
    with pytest.raises(ValueError, match="环境变量名"):
        backend.prepare(tmp_path / "a2", lower,
                        wsl_job_dir="/home/insar/work/.jobs/r1/s03/a2")


def test_cancel_touches_contract_file(tmp_path):
    backend, runner = _backend(tmp_path, rules=[])
    backend.cancel(tmp_path)
    assert (tmp_path / "job.cancel").exists()
    assert runner.calls == []  # 取消不需要信号能力,纯文件契约


def test_read_new_lines_commits_only_complete_lines(tmp_path):
    backend, _ = _backend(tmp_path, rules=[])
    log = tmp_path / "job.log"
    log.write_bytes(b"line1\nline2\nhalf")
    lines, offset = backend.read_new_lines(tmp_path, 0)
    assert lines == ["line1", "line2"]
    assert offset == len(b"line1\nline2\n")
    lines, offset2 = backend.read_new_lines(tmp_path, offset)
    assert (lines, offset2) == ([], offset)  # 半行不提交
    log.write_bytes(b"line1\nline2\nhalf done\n")
    lines, _ = backend.read_new_lines(tmp_path, offset)
    assert lines == ["half done"]


def test_read_new_lines_final_mode_flushes_tail(tmp_path):
    """终读模式(FOLLOWUPS #7):作业 finished 后无换行尾行交付,offset 到 EOF。"""
    backend, _ = _backend(tmp_path, rules=[])
    (tmp_path / "job.log").write_bytes(b"line1\nhalf-tail")
    lines, offset = backend.read_new_lines(tmp_path, 0)
    assert (lines, offset) == (["line1"], 6)
    lines, offset = backend.read_new_lines(tmp_path, offset, final=True)
    assert (lines, offset) == (["half-tail"], 15)
    assert backend.read_new_lines(tmp_path, offset, final=True) == ([], offset)


@pytest.mark.parametrize("error", [FileNotFoundError, PermissionError, OSError])
def test_state_toctou_window_returns_unknown(tmp_path, error):
    """9p 上 exists→read 窗口文件被删/独占/会话断开 → unknown 不抛(#6)。

    \\\\wsl.localhost 走网络重定向器,除本地竞态外还有 VM 关闭/9p 断开的
    整类 OSError —— 判活探测绝不能把调用方打崩。
    """

    class _Vanishing:
        def exists(self):
            return True

        def read_text(self, *a, **kw):
            raise error("gone between exists() and read")

    class _Dir:
        def __truediv__(self, name):
            return _Vanishing()

    backend, _ = _backend(tmp_path, rules=[])
    st = backend.state(_Dir())
    assert st.kind == "unknown" and st.exit_code is None


def test_state_probe_declared_expensive():
    """WslJobBackend 声明判活高成本(每次 spawn wsl.exe),follow_job 据此
    启用自适应节流;LocalJobBackend 不声明,判活节奏不变(WSL P2)。"""
    assert getattr(WslJobBackend, "state_probe_expensive", False) is True
    assert getattr(LocalJobBackend, "state_probe_expensive", False) is False


def test_release_keepalive_idempotent():
    """release_keepalive 幂等:只终结自己 spawn 的保活进程;重复调用、
    从未拉起、进程已死都安全(WSL P2 释放钩子)。"""
    backend = WslJobBackend(paths=WslPaths(distro="insar"), runner=FakeRunner())

    class _FakeProc:
        def __init__(self):
            self.terminated = 0
            self._rc = None

        def poll(self):
            return self._rc

        def terminate(self):
            self.terminated += 1
            self._rc = 1

        def wait(self, timeout=None):
            return self._rc

        def kill(self):
            pass

    backend.release_keepalive()             # 从未拉起:no-op
    proc = _FakeProc()
    backend._keepalive_proc = proc
    backend.release_keepalive()
    assert proc.terminated == 1 and backend._keepalive_proc is None
    backend.release_keepalive()             # 重复调用:无进程可释放
    assert proc.terminated == 1
    dead = _FakeProc()
    dead._rc = 0                            # 进程已自亡:不再 terminate
    backend._keepalive_proc = dead
    backend.release_keepalive()
    assert dead.terminated == 0


# ================================================================ driver 收尾释放(WSL P2)

def _empty_probe():
    from insar_agent.runtime.probe import ProbeResult
    return ProbeResult(engines={"isce2": None, "mintpy": None, "snaphu": None,
                                "gdal": None, "snap": None, "pystamps": None,
                                "pyaps": None},
                       credentials={"earthdata": False, "cds": False, "gacos": False},
                       disk_free_gb=100.0, cpu_count=8)


async def _collect(agen) -> list[dict]:
    return [e async for e in agen]


class _ReleasableLocal(LocalJobBackend):
    """带保活释放钩子的本地后端替身:接口对齐 WslJobBackend.release_keepalive,
    又能真正跑模拟作业(测试机不可依赖真 WSL)。"""

    def __init__(self):
        super().__init__(hb_stale=3.0)
        self.released = 0

    def release_keepalive(self):
        self.released += 1


def _release_driver(store, workspace, monkeypatch, factory):
    """把 driver 的每步后端选择替换为 factory(override 语义保持原样)。"""
    from insar_agent.brain.facade import Brain
    from insar_agent.loop import driver as driver_mod

    def fake_backend_for_step(**kwargs):
        if kwargs.get("override") is not None:
            return kwargs["override"]
        return factory()

    monkeypatch.setattr(driver_mod, "backend_for_step", fake_backend_for_step)
    return driver_mod.Driver(store, workspace=workspace, probe=_empty_probe(),
                             poll=0.05, startup_grace=15.0, brain=Brain(None))


def test_driver_releases_keepalive_at_run_end(store, workspace, monkeypatch):
    """run 正常收尾(done):本 run 用过的每个后端实例的 keepalive 恰好释放
    一次,登记表清空(WSL P2:此前 sleep infinity 无人释放,随 run 堆积)。"""
    created: list[_ReleasableLocal] = []

    def factory():
        b = _ReleasableLocal()
        created.append(b)
        return b

    driver = _release_driver(store, workspace, monkeypatch, factory)
    asyncio.run(_collect(driver.turn("s1", "Ridgecrest 地震同震形变分析")))
    events = asyncio.run(_collect(driver.execute("s1")))
    assert any(e["t"] == "result" for e in events)            # run 跑到 done
    assert created and all(b.released == 1 for b in created)  # 恰好各释放一次
    assert not driver._run_backends                           # 登记表已清空


def test_driver_releases_keepalive_on_interrupted_run(store, workspace, monkeypatch):
    """中途 KILL → run interrupted 提前返回:finally 收尾同样释放保活。"""
    created: list[_ReleasableLocal] = []

    def factory():
        b = _ReleasableLocal()
        created.append(b)
        return b

    driver = _release_driver(store, workspace, monkeypatch, factory)
    asyncio.run(_collect(driver.turn("s1", "Ridgecrest 地震同震形变分析")))
    run = store.latest_run("s1")

    async def scenario():
        async for e in driver.execute("s1"):
            if e["t"] == "step.start":
                # run_id 归属:执行期消费已改严格匹配(REVIEW-r2 P1-3),
                # 未定向动作不再被消费 —— 与 API 入队行为一致
                store.push_action(scope="run", target=run["run_id"], action="KILL",
                                  deliver_as="steer", run_id=run["run_id"])

    asyncio.run(scenario())
    assert store.get_run(run["run_id"])["status"] == "interrupted"
    assert created and all(b.released == 1 for b in created)
    assert not driver._run_backends


def test_driver_never_releases_injected_override(store, workspace, monkeypatch):
    """注入 override 后端可能跨 run 共享:生命周期归注入方,driver 绝不代释。"""
    from insar_agent.brain.facade import Brain
    from insar_agent.loop.driver import Driver

    injected = _ReleasableLocal()
    driver = Driver(store, workspace=workspace, probe=_empty_probe(), poll=0.05,
                    startup_grace=15.0, brain=Brain(None), backend=injected)
    asyncio.run(_collect(driver.turn("s1", "Ridgecrest 地震同震形变分析")))
    events = asyncio.run(_collect(driver.execute("s1")))
    assert any(e["t"] == "result" for e in events)
    assert injected.released == 0


def test_driver_survives_backend_launch_failure(store, workspace, monkeypatch):
    """launch 抛 RuntimeError(WSL 不可达类)→ 步骤 failed、分类 wsl_orphaned、
    分诊 note 正常发出,执行回合流完整走完不裸崩(WSL P2 分诊)。"""

    class _LaunchBoom(_ReleasableLocal):
        def launch(self, job_dir):
            raise RuntimeError("WSL 侧启动 wrapper 失败(rc=127):setsid not found")

    created: list[_LaunchBoom] = []

    def factory():
        b = _LaunchBoom()
        created.append(b)
        return b

    driver = _release_driver(store, workspace, monkeypatch, factory)
    asyncio.run(_collect(driver.turn("s1", "Ridgecrest 地震同震形变分析")))
    run = store.latest_run("s1")
    events = asyncio.run(_collect(driver.execute("s1")))      # 正常返回,无异常
    assert store.get_run(run["run_id"])["status"] == "failed"
    failed = [s for s in store.load_steps(run["run_id"]) if s.state == "failed"]
    assert failed and failed[0].failure_class == "wsl_orphaned"
    assert any(e["t"] == "note" and "wsl_orphaned" in e.get("text", "")
               for e in events)
    assert created and all(b.released == 1 for b in created)  # 失败收尾同样释放


# ================================================================ 真实 WSL 冒烟

def _default_distro() -> str:
    return os.environ.get("INSAR_WSL_DISTRO", "insar")


@lru_cache(maxsize=None)
def _real_distro_reachable(distro: str) -> bool:
    """秒级 echo 探测;任何异常都视为不可达(冒烟自动跳过的依据)。"""
    try:
        cp = subprocess.run(
            ["wsl.exe", "-d", distro, "-u", "root", "--exec", "bash", "-lc",
             "echo __insar_backend_smoke__"],
            capture_output=True, timeout=60, creationflags=_NO_WINDOW,
            env={**os.environ, "WSL_UTF8": "1"})
    except (OSError, subprocess.TimeoutExpired):
        return False
    out = cp.stdout.decode("utf-8", errors="replace").replace("\x00", "")
    return cp.returncode == 0 and "__insar_backend_smoke__" in out


@pytest.mark.skipif(sys.platform != "win32" or shutil.which("wsl.exe") is None,
                    reason="无 wsl.exe,跳过真实 WSL 冒烟")
def test_real_wsl_echo_job_roundtrip():
    distro = _default_distro()
    if not _real_distro_reachable(distro):
        pytest.skip(f"WSL 发行版 {distro!r} 不可达")

    paths = WslPaths(distro=distro)
    backend = WslJobBackend(paths=paths, keepalive=False)  # echo 秒级结束,无需保活
    name = f"test-{uuid.uuid4().hex[:8]}"
    posix = f"/home/insar/work/.jobs/{name}"
    job_dir = paths.host_root(".jobs", name)
    plan = CommandPlan(
        argv=["echo", "wsl-backend-smoke-ok"], cwd=posix,  # cd 到刚建的作业目录,必然存在
        env={"INSAR_SMOKE": "1"}, files={},
        shell_line='echo wsl-backend-smoke-ok; echo "smoke-env=$INSAR_SMOKE"')

    try:
        backend.prepare(job_dir, plan)
        backend.launch(job_dir)
        deadline = time.time() + 90
        state = backend.state(job_dir)
        while state.kind != "finished" and time.time() < deadline:
            time.sleep(0.5)
            state = backend.state(job_dir)
        log_hint = ""
        if (job_dir / "job.log").exists():
            log_hint = (job_dir / "job.log").read_text(encoding="utf-8", errors="replace")
        assert state.kind == "finished", f"作业未在期限内结束:{state} log={log_hint!r}"
        assert state.exit_code == 0
        lines, _ = backend.read_new_lines(job_dir, 0)
        assert "wsl-backend-smoke-ok" in lines
        assert "smoke-env=1" in lines  # env 确实进了 cmd.sh 并被导出
    finally:
        # 清理保险栓:只删本测试自己建的 test-* 作业目录,别的一律不碰
        assert posix.startswith("/home/insar/work/.jobs/test-")
        subprocess.run(
            ["wsl.exe", "-d", distro, "-u", "root", "--exec", "bash", "-lc",
             f"rm -rf {posix}"],
            capture_output=True, timeout=60, creationflags=_NO_WINDOW)
