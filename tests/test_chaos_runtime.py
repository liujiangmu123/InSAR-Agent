"""执行层故障注入(混沌)测试 —— 验证崩溃恢复语义(AGENT-DESIGN §4.7)。

与 test_executor.py 的分工:那边走 execute_step 全链路(五阶段/店面语义),
这里直接对 follow_job + LocalJobBackend + local_wrapper 注入真实故障,
锁定流层的 JobOutcome 契约:

  1. wrapper 被外部强杀(taskkill /F / kill -9),job.rc 永不落盘 → orphaned
     (孤儿判定连续两次命中;单次瞬时命中不误判)
  2. 心跳停更(wrapper 挂死/机器休眠)→ orphaned
  3. 子进程挂死无输出 → idle_timeout;长任务 → total_timeout;
     两者都走协作取消(job.cancel → wrapper 整组终止 rc=143),无残留进程
  4. 取消与正常退出竞态(rc 先落盘)→ 保留真实退出码,取消幂等
  5. job.rc 损坏(空/非整数)→ finished(-1),不崩溃
  6. 日志被外部删除/独占 → follow 不崩溃(独占场景曾是真实 bug,见下)
  7. wrapper 启动即失败(可执行文件不存在 / cmd.json 缺失损坏)→ rc=127
     快速失败,诊断行进入日志流(cmd.json 场景曾是真实 bug,见下)
  8. state() 的 exists→read/stat TOCTOU 窗口(FOLLOWUPS #6)→ unknown 不抛
  9. finished 终读冲刷无换行尾行(FOLLOWUPS #7)→ 尾部余量不丢
 10. 取消分支遇不可杀进程(FOLLOWUPS #8)→ 诊断进日志 + rc=129 显式终局
 11. 高成本判活(WSL)自适应节流 → 探测降频且孤儿双击语义不放大

所有场景用 tmp_path 作业目录 + 真实 LocalJobBackend;子进程一律秒级
python 小脚本(sys.executable),长睡脚本都带自灭上限并在断言后清理。
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from conftest import TIME_FACTOR
from insar_agent.runtime.jobs import CommandPlan, JobState, LocalJobBackend
from insar_agent.runtime.stream import CancelToken, follow_job

WIN = sys.platform == "win32"

# 全模块真实子进程 + 真实时钟判定(心跳/双超时/宽限):时序敏感,
# 判定窗(hb_stale/idle/total/grace 与等待上限)统一乘 TIME_FACTOR(简写 TF);
# 轮询间隔 poll、作业脚本内 sleep(自灭上限)与断言语义不变
pytestmark = pytest.mark.timing
TF = TIME_FACTOR

if WIN:
    _K32 = ctypes.windll.kernel32
    _K32.OpenProcess.restype = ctypes.c_void_p
    _K32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    _K32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _K32.CloseHandle.argtypes = [ctypes.c_void_p]
    _K32.CreateFileW.restype = ctypes.c_void_p
    _K32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                 ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                 ctypes.c_void_p]


def run_async(coro):
    return asyncio.run(coro)


# ---------------- 进程探测 / 强杀(不依赖 psutil) ----------------

def _pid_alive(pid: int) -> bool:
    if WIN:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        h = _K32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if not _K32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            _K32.CloseHandle(h)
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def _force_kill(pid: int, tree: bool = False) -> None:
    """外部强杀:Windows 用 taskkill /F(等价 kill -9),不给进程清理机会。"""
    if WIN:
        argv = ["taskkill", "/F", "/PID", str(pid)] + (["/T"] if tree else [])
        subprocess.run(argv, capture_output=True)
    else:
        import signal
        try:
            os.kill(int(pid), signal.SIGKILL)
        except OSError:
            pass


def _wait_until(cond, timeout: float = 30.0, interval: float = 0.05, msg: str = "条件未满足"):
    timeout *= TF  # 等待上限随负载系数放宽;轮询节奏不变,空载不多等一秒
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(interval)
    pytest.fail(f"{msg}(等待 {timeout}s)")


async def _await_until(cond, timeout: float = 10.0, msg: str = "条件未满足"):
    timeout *= TF
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        await asyncio.sleep(0.02)
    pytest.fail(f"{msg}(等待 {timeout}s)")


def _assert_no_residue(job: Path) -> None:
    """断言 wrapper 与作业子进程都已退出(不留残留进程)。"""
    for name in ("job.pid", "child.pid"):
        f = job / name
        if f.exists():
            pid = int(f.read_text().strip())
            _wait_until(lambda: not _pid_alive(pid), timeout=8.0,
                        msg=f"{name}={pid} 进程残留未退出")


# ---------------- 作业启动 ----------------

# 子脚本统一先把自身 PID 写进作业目录(cwd=job_dir),供残留断言/清理
_HEADER = (
    "import os, sys, time\n"
    "from pathlib import Path\n"
    "Path('child.pid').write_text(str(os.getpid()), encoding='utf-8')\n"
)


def _launch(backend, job: Path, body: str) -> None:
    """把 python 脚本体作为真实作业启动(经 LocalJobBackend + wrapper)。"""
    job.mkdir(parents=True, exist_ok=True)
    script = job / "payload.py"
    script.write_text(_HEADER + body, encoding="utf-8")
    plan = CommandPlan(argv=[sys.executable, "-X", "utf8", str(script)],
                       cwd=str(job), env={"PYTHONIOENCODING": "utf-8"}, files={})
    backend.prepare(job, plan)
    backend.launch(job)


def _wait_wrapper_up(backend, job: Path) -> None:
    # 负载/杀软下 python 启动可达秒级,轮询而非固定 sleep(同 test_executor 教训)
    _wait_until(lambda: backend.state(job).kind in ("alive", "finished"),
                msg="wrapper 未启动")


def _fake_alive_job(job: Path, log_text: str = "") -> None:
    """伪造「wrapper 活着」现场:有 pid + 新鲜 hb,无 rc(不起真实进程的场景用)。"""
    job.mkdir(parents=True, exist_ok=True)
    (job / "job.pid").write_text("99999", encoding="utf-8")
    (job / "job.hb").touch()
    if log_text:
        (job / "job.log").write_text(log_text, encoding="utf-8")


# ================ 场景 1:wrapper 被外部强杀,rc 永不落盘 ================

def test_orphan_wrapper_killed_externally(tmp_path):
    """只杀 wrapper、留下仍在写日志的孤儿子进程 —— 最恶劣情形。

    孤儿判定必须只看心跳停更,不被「日志仍在增长」迷惑;job.rc 永不落盘,
    与 finished 严格区分(§4.7:环境事件,非计算失败)。
    """
    backend = LocalJobBackend(hb_stale=0.8 * TF)
    job = tmp_path / "job"
    body = ("for i in range(200):\n"          # 自灭上限 20s,实际 ~2s 内被清理
            "    print(f'line {i}', flush=True)\n"
            "    time.sleep(0.1)\n")
    _launch(backend, job, body)
    try:
        _wait_wrapper_up(backend, job)
        _wait_until(lambda: (job / "child.pid").exists(), msg="子进程未写 child.pid")
        wrapper_pid = int((job / "job.pid").read_text())
        _force_kill(wrapper_pid)              # kill -9 语义:心跳线程随 wrapper 即死
        _wait_until(lambda: not _pid_alive(wrapper_pid), timeout=8.0, msg="wrapper 未死")

        lines: list[str] = []
        out = run_async(follow_job(backend, job, idle_timeout=30 * TF,
                                   total_timeout=30 * TF,
                                   on_line=lines.append, poll=0.1,
                                   startup_grace=5 * TF))
        assert out.kind == "orphaned" and out.exit_code is None
        assert not (job / "job.rc").exists()  # rc 永不落盘:orphaned ≠ finished
        assert lines and lines[0] == "line 0"  # 判定期间孤儿子进程的输出仍被 drain
    finally:
        cp = job / "child.pid"
        if cp.exists():
            _force_kill(int(cp.read_text()), tree=True)
    _assert_no_residue(job)


class _OneShotOrphanBackend:
    """代理真实后端,仅首次 state() 强制报 orphaned:模拟心跳文件的瞬时竞态。"""

    def __init__(self, inner):
        self._inner = inner
        self._fired = False

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def state(self, job_dir):
        if not self._fired:
            self._fired = True
            return JobState("orphaned")
        return self._inner.state(job_dir)


def test_single_orphan_strike_does_not_misjudge(tmp_path):
    """孤儿判定需连续两次命中:单次瞬时 orphaned 后恢复 → 仍按真实结局 finished。"""
    backend = _OneShotOrphanBackend(LocalJobBackend(hb_stale=5.0 * TF))
    job = tmp_path / "job"
    _launch(backend, job, "print('ok', flush=True)\n")
    out = run_async(follow_job(backend, job, idle_timeout=20 * TF, total_timeout=30 * TF,
                               poll=0.1, startup_grace=30 * TF))
    assert out.kind == "finished" and out.exit_code == 0


# ================ 场景 2:心跳停更 / 子进程挂死 ================

def test_heartbeat_stall_marks_orphan(tmp_path):
    """wrapper 名存实亡(心跳文件停更,如休眠恢复/线程僵死)→ 连续命中判孤儿。"""
    backend = LocalJobBackend(hb_stale=0.5 * TF)
    job = tmp_path / "job"
    _fake_alive_job(job, log_text="partial output\n")  # hb 此后不再刷新 = 停更
    log_size = (job / "job.log").stat().st_size  # 文本模式落盘为 \r\n,按实际字节数对账

    lines: list[str] = []
    out = run_async(follow_job(backend, job, idle_timeout=30 * TF, total_timeout=30 * TF,
                               on_line=lines.append, poll=0.1, startup_grace=5 * TF))
    assert out.kind == "orphaned" and out.exit_code is None
    assert lines == ["partial output"]  # 判定前既有日志已 drain,offset 不回退
    assert out.offset == log_size


def test_idle_timeout_on_hung_child(tmp_path):
    """子进程挂死(wrapper 心跳正常、无输出)→ idle_timeout 触发协作取消。

    断言:rc=143(wrapper 整组终止)、job.cancel 存在(走文件契约而非宿主强杀)、
    wrapper 与子进程都不残留。
    """
    backend = LocalJobBackend(hb_stale=3.0 * TF)
    job = tmp_path / "job"
    _launch(backend, job, "print('start', flush=True)\ntime.sleep(60)\n")
    _wait_wrapper_up(backend, job)

    out = run_async(follow_job(backend, job, idle_timeout=1.0 * TF, total_timeout=60 * TF,
                               poll=0.1, cancel_grace=10 * TF, startup_grace=30 * TF))
    assert out.kind == "idle_timeout" and out.exit_code == 143
    assert (job / "job.cancel").exists()
    assert (job / "job.rc").read_text().strip() == "143"
    _assert_no_residue(job)


# ================ 场景 3:total timeout ================

def test_total_timeout_kills_active_child(tmp_path):
    """任务持续产出但超总时长 → total_timeout;有输出不能豁免总超时,进程整组清理。"""
    backend = LocalJobBackend(hb_stale=3.0 * TF)
    job = tmp_path / "job"
    body = ("for i in range(300):\n"          # 自灭上限 30s,实际 ~1.5s×TF 被总超时终止
            "    print(f'busy {i}', flush=True)\n"
            "    time.sleep(0.1)\n")
    _launch(backend, job, body)
    _wait_wrapper_up(backend, job)
    # 先等到「确实在产出」再挂总超时钟:饱和负载下 python 子进程启动可超过
    # total_timeout 本身,零输出即被总超时杀掉会让 lines 断言空翻车(压测实录);
    # 语义不变 —— 总超时杀的仍是持续产出中的任务,已产出的行经 offset=0 全量 drain
    _wait_until(lambda: (job / "job.log").exists() and (job / "job.log").stat().st_size > 0,
                msg="作业无输出")

    lines: list[str] = []
    out = run_async(follow_job(backend, job, idle_timeout=30 * TF, total_timeout=1.5 * TF,
                               on_line=lines.append, poll=0.1, cancel_grace=10 * TF,
                               startup_grace=30 * TF))
    assert out.kind == "total_timeout" and out.exit_code == 143
    assert lines  # 终止前日志正常流出(证明是总超时而非 idle 判的)
    assert (job / "job.rc").read_text().strip() == "143"
    _assert_no_residue(job)


# ================ 场景 4:取消竞态 / 取消幂等 ================

def test_cancel_race_rc_lands_first(tmp_path):
    """发 cancel 时子进程已正常退出(rc=0 先落盘)→ 不误报。

    语义锁定:kind 如实呈现用户取消意图("cancelled"),exit_code 必须是
    真实 rc(0),不得伪造成 143/None;残留的 job.cancel 标记不毒化后续判活。
    """
    backend = LocalJobBackend(hb_stale=3.0 * TF)
    job = tmp_path / "job"
    _launch(backend, job, "print('quick done', flush=True)\n")
    _wait_until(lambda: (job / "job.rc").exists(), msg="作业未在时限内结束")

    token = CancelToken()
    token.cancel()
    token.cancel()  # 令牌幂等:重复取消无害
    lines: list[str] = []
    out = run_async(follow_job(backend, job, idle_timeout=10 * TF, total_timeout=15 * TF,
                               token=token, on_line=lines.append, poll=0.05,
                               cancel_grace=5 * TF, startup_grace=10 * TF))
    assert out.kind == "cancelled"
    assert out.exit_code == 0             # 真实退出码,不被伪造
    assert any("quick done" in ln for ln in lines)  # 返回前日志 drain 到底

    backend.cancel(job)
    backend.cancel(job)                   # 后端取消幂等:对已结束作业无副作用
    st = backend.state(job)
    assert st.kind == "finished" and st.exit_code == 0
    # 残留 cancel 标记不影响 reattach 再跟随:仍按 finished 收口
    out2 = run_async(follow_job(backend, job, idle_timeout=5 * TF, total_timeout=5 * TF,
                                poll=0.05))
    assert out2.kind == "finished" and out2.exit_code == 0


def test_cancel_midrun_terminates_group(tmp_path):
    """基线取消路径:运行中取消 → rc=143,wrapper/子进程双双退出,重复取消无害。"""
    backend = LocalJobBackend(hb_stale=3.0 * TF)
    job = tmp_path / "job"
    body = ("for i in range(200):\n"          # 自灭上限 20s
            "    print(f'line {i}', flush=True)\n"
            "    time.sleep(0.1)\n")
    _launch(backend, job, body)
    _wait_wrapper_up(backend, job)

    token = CancelToken()

    async def scenario():
        task = asyncio.create_task(
            follow_job(backend, job, idle_timeout=30 * TF, total_timeout=60 * TF,
                       token=token, poll=0.1, cancel_grace=10 * TF,
                       startup_grace=30 * TF))
        await _await_until(lambda: (job / "job.log").exists()
                           and (job / "job.log").stat().st_size > 0, msg="作业无输出")
        token.cancel()
        token.cancel()
        return await task

    out = run_async(scenario())
    assert out.kind == "cancelled" and out.exit_code == 143
    _assert_no_residue(job)


# ================ 场景 5:job.rc 内容损坏 ================

@pytest.mark.parametrize("payload", ["", "not-a-number", "12.5"])
def test_corrupt_rc_classified_finished(tmp_path, payload):
    """rc 空/非整数(写入方被杀在半途、磁盘损坏)→ 归类 finished(-1),不崩溃。

    -1 与合法退出码域(>=0)分离,上层可识别「结束了但退出码不可信」。
    """
    backend = LocalJobBackend(hb_stale=5.0 * TF)
    job = tmp_path / "job"
    _fake_alive_job(job, log_text="some output\n")

    async def scenario():
        task = asyncio.create_task(
            follow_job(backend, job, idle_timeout=10 * TF, total_timeout=15 * TF,
                       poll=0.05))
        await asyncio.sleep(0.2)  # 先跟随一阵,再注入损坏的 rc
        (job / "job.rc").write_text(payload, encoding="utf-8")
        return await task

    out = run_async(scenario())
    assert out.kind == "finished" and out.exit_code == -1


# ================ 场景 6:日志被删除 / 被独占 ================

def test_log_deleted_then_recreated_smaller(tmp_path):
    """日志被外部删除、再重建成更短文件(轮转/误清理)→ follow 不崩溃。

    旧 offset 超出新文件长度时安全失效(读不到东西,不重复、不错乱)。
    """
    backend = LocalJobBackend(hb_stale=5.0 * TF)
    job = tmp_path / "job"
    _fake_alive_job(job, log_text="line-1\nline-2\n")

    async def scenario():
        lines: list[str] = []
        task = asyncio.create_task(
            follow_job(backend, job, idle_timeout=10 * TF, total_timeout=15 * TF,
                       on_line=lines.append, poll=0.05))
        await _await_until(lambda: len(lines) >= 2, msg="初始日志未读到")
        (job / "job.log").unlink()            # 外部删除(此时无进程持有句柄)
        await asyncio.sleep(0.2)              # 删除期间 follow 照常轮询
        (job / "job.log").write_text("x\n", encoding="utf-8")  # 重建且更短
        await asyncio.sleep(0.2)
        (job / "job.rc").write_text("0", encoding="utf-8")
        return await task, lines

    out, lines = run_async(scenario())
    assert out.kind == "finished" and out.exit_code == 0
    assert lines == ["line-1", "line-2"]  # 不崩、不重复、不把新短文件错读成增量


@pytest.mark.skipif(not WIN, reason="零共享句柄语义仅 Windows")
def test_log_exclusively_locked_mid_follow(tmp_path):
    """日志被外部进程独占(杀软/索引器零共享打开)→ 读失败视为瞬时,follow 不崩溃。

    修复前的真实 bug:read_new_lines 的 open() 抛 PermissionError
    (ERROR_SHARING_VIOLATION),直接杀死 follow 协程,offset 进度与
    取消能力一起丢失。修复后:该轮当作无新输出,解锁后续读不丢行。
    """
    backend = LocalJobBackend(hb_stale=5.0 * TF)
    job = tmp_path / "job"
    _fake_alive_job(job, log_text="line-1\n")

    GENERIC_READ = 0x80000000
    OPEN_EXISTING = 3
    INVALID_HANDLE = ctypes.c_void_p(-1).value
    handle = _K32.CreateFileW(str(job / "job.log"), GENERIC_READ, 0, None,
                              OPEN_EXISTING, 0, None)
    assert handle and handle != INVALID_HANDLE, "独占打开日志失败"
    locked = True

    async def scenario():
        nonlocal locked
        lines: list[str] = []
        task = asyncio.create_task(
            follow_job(backend, job, idle_timeout=10 * TF, total_timeout=15 * TF,
                       on_line=lines.append, poll=0.05))
        await asyncio.sleep(0.4)              # 独占期间每轮读都失败,follow 必须活着
        assert not task.done(), "follow 在日志被独占期间崩溃退出"
        _K32.CloseHandle(handle)              # 外部进程放手
        locked = False
        with open(job / "job.log", "ab") as f:
            f.write(b"line-2\n")
        (job / "job.rc").write_text("0", encoding="utf-8")
        return await task, lines

    try:
        out, lines = run_async(scenario())
    finally:
        if locked:
            _K32.CloseHandle(handle)
    assert out.kind == "finished" and out.exit_code == 0
    assert lines == ["line-1", "line-2"]      # 解锁后从 offset=0 完整补读,不丢行


class _FlakyStateBackend:
    """代理真实后端,首次 state() 抛 OSError:模拟判活撞上独占句柄/删除竞态。"""

    def __init__(self, inner):
        self._inner = inner
        self._fired = False

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def state(self, job_dir):
        if not self._fired:
            self._fired = True
            raise PermissionError("job.hb is exclusively locked")
        return self._inner.state(job_dir)


def test_transient_state_error_does_not_kill_follow(tmp_path):
    """判活读 hb/rc 的一次 OSError(独占/删除竞态)→ 视为瞬时,按真实结局收口。

    修复前的真实 bug:backend.state 异常直接冒出 follow_job,协程崩溃。
    """
    backend = _FlakyStateBackend(LocalJobBackend(hb_stale=5.0 * TF))
    job = tmp_path / "job"
    _launch(backend, job, "print('ok', flush=True)\n")
    out = run_async(follow_job(backend, job, idle_timeout=20 * TF, total_timeout=30 * TF,
                               poll=0.1, startup_grace=30 * TF))
    assert out.kind == "finished" and out.exit_code == 0


# ================ 场景 7:wrapper 启动即失败 ================

def test_spawn_failure_missing_executable(tmp_path):
    """目标可执行文件不存在 → rc=127 快速失败,诊断行随日志流可读。"""
    backend = LocalJobBackend(hb_stale=3.0 * TF)
    job = tmp_path / "job"
    plan = CommandPlan(argv=[str(tmp_path / "no_such_tool.exe"), "--run"],
                       cwd=str(tmp_path), env={}, files={})
    backend.prepare(job, plan)
    backend.launch(job)

    lines: list[str] = []
    t0 = time.monotonic()
    out = run_async(follow_job(backend, job, idle_timeout=20 * TF, total_timeout=40 * TF,
                               on_line=lines.append, poll=0.1, startup_grace=30 * TF))
    assert out.kind == "finished" and out.exit_code == 127
    assert time.monotonic() - t0 < 15 * TF    # 快速失败,不等超时/孤儿宽限
    assert any("[wrapper] spawn failed" in ln for ln in lines)  # 诊断可读


@pytest.mark.parametrize("mode", ["missing", "corrupt"])
def test_bad_cmd_json_fails_fast(tmp_path, mode):
    """cmd.json 缺失/损坏(渲染中断、外部篡改)→ rc=127 快速失败 + 诊断进日志流。

    修复前的真实 bug:wrapper 未捕获读取/解析异常,静默死亡(无 pid 无 rc),
    follow 只能等 startup_grace 耗尽后误判 orphaned,诊断埋在 wrapper.err
    里不进日志流。
    """
    backend = LocalJobBackend(hb_stale=3.0 * TF)
    job = tmp_path / "job"
    plan = CommandPlan(argv=[sys.executable, "-c", "print('nope')"],
                       cwd=str(tmp_path), env={}, files={})
    backend.prepare(job, plan)
    if mode == "missing":
        (job / "cmd.json").unlink()
    else:
        (job / "cmd.json").write_text("{broken json", encoding="utf-8")
    backend.launch(job)

    lines: list[str] = []
    t0 = time.monotonic()
    out = run_async(follow_job(backend, job, idle_timeout=20 * TF, total_timeout=40 * TF,
                               on_line=lines.append, poll=0.1, startup_grace=30 * TF))
    assert out.kind == "finished" and out.exit_code == 127
    assert time.monotonic() - t0 < 15 * TF    # 不吃满 startup_grace
    assert any("[wrapper] bad cmd.json" in ln for ln in lines)


# ================ 场景 8:判活 exists→read/stat TOCTOU 窗口(#6) ================

class _TocFile:
    """exists() 说在、随后的 stat()/read_text() 已被删/被独占:
    精确复现 exists→read 之间的 TOCTOU 窗口(确定性,不靠线程时序)。"""

    def __init__(self, exists: bool = True, error: type[OSError] = FileNotFoundError):
        self._exists = exists
        self._error = error

    def exists(self) -> bool:
        return self._exists

    def stat(self):
        raise self._error("gone between exists() and stat()")

    def read_text(self, *a, **kw):
        raise self._error("gone between exists() and read_text()")


class _TocJobDir:
    """鸭子类型作业目录:state() 只用 `/` 与文件方法,按名字给预设文件桩。"""

    def __init__(self, files: dict):
        self._files = files

    def __truediv__(self, name: str):
        return self._files[name]


@pytest.mark.parametrize("error", [FileNotFoundError, PermissionError])
def test_state_rc_vanishes_between_exists_and_read(error):
    """job.rc 在 exists→read 窗口被删/被独占 → unknown,不抛异常。

    修复前(FOLLOWUPS #6)异常直接抛给调用方:stream 侧有兜底,但 executor
    的认领判活(state(prev_dir))与 admin 外部判活会被探测本身打崩。
    """
    st = LocalJobBackend().state(_TocJobDir({"job.rc": _TocFile(error=error)}))
    assert st.kind == "unknown" and st.exit_code is None


@pytest.mark.parametrize("error", [FileNotFoundError, PermissionError])
def test_state_hb_vanishes_between_exists_and_stat(error):
    """job.hb 在 exists→stat 窗口消失/被独占 → unknown(单轮不判定,下一轮
    自然重试;孤儿判定连续两次命中的语义不受影响)。"""
    st = LocalJobBackend().state(_TocJobDir({
        "job.rc": _TocFile(exists=False),
        "job.pid": _TocFile(),           # 只被 exists() 询问,不读内容
        "job.hb": _TocFile(error=error),
    }))
    assert st.kind == "unknown"


def test_state_survives_concurrent_file_flapping(tmp_path):
    """真线程竞态:后台线程高频创建/删除 job.hb 与 job.rc,state() 全程只返回
    契约内四态、绝不抛异常(命中窗口与否取决于时序,断言的是「绝不崩」)。"""
    backend = LocalJobBackend(hb_stale=5.0 * TF)
    job = tmp_path / "job"
    job.mkdir()
    (job / "job.pid").write_text("12345", encoding="utf-8")
    stop = threading.Event()

    def flap():
        while not stop.is_set():
            for name, content in (("job.hb", ""), ("job.rc", "0")):
                f = job / name
                try:
                    f.write_text(content, encoding="utf-8")
                    f.unlink()
                except OSError:
                    pass  # 竞态制造者自身的冲突无关紧要

    t = threading.Thread(target=flap, daemon=True)
    t.start()
    try:
        kinds = set()
        for _ in range(400):
            st = backend.state(job)  # 修复前:窗口命中即 FileNotFoundError 冒泡
            kinds.add(st.kind)
        assert kinds <= {"alive", "finished", "orphaned", "unknown"}
    finally:
        stop.set()
        t.join(timeout=5)


# ================ 场景 9:finished 终读冲刷无换行尾行(#7) ================

def test_read_new_lines_final_mode_contract(tmp_path):
    """后端契约:常规读半行不提交;终读交付余量并把 offset 推进到 EOF;
    终读幂等(再读无新内容)。"""
    backend = LocalJobBackend()
    (tmp_path / "job.log").write_bytes(b"a\nhalf")
    lines, off = backend.read_new_lines(tmp_path, 0)
    assert (lines, off) == (["a"], 2)          # 常规:半行不提交
    lines, off2 = backend.read_new_lines(tmp_path, off, final=True)
    assert (lines, off2) == (["half"], 6)      # 终读:余量交付,offset 到 EOF
    assert backend.read_new_lines(tmp_path, off2, final=True) == ([], off2)


def test_final_drain_flushes_unterminated_tail(tmp_path):
    """作业 finished(rc 落盘)后,无换行的尾部余量必须交付(FOLLOWUPS #7)。

    修复前 read_new_lines 只提交完整行:不少引擎最后一行诊断不带换行
    (如 'Killed'、进度行),rc 落盘后这半行永久丢失。
    """
    backend = LocalJobBackend(hb_stale=5.0 * TF)
    job = tmp_path / "job"
    _fake_alive_job(job, log_text="line-1\n")

    async def scenario():
        lines: list[str] = []
        task = asyncio.create_task(
            follow_job(backend, job, idle_timeout=10 * TF, total_timeout=15 * TF,
                       on_line=lines.append, poll=0.05))
        await _await_until(lambda: lines == ["line-1"], msg="首行未读到")
        with open(job / "job.log", "ab") as f:
            f.write(b"tail-no-newline")        # 结尾没有换行符
        (job / "job.rc").write_text("0", encoding="utf-8")
        return await task, lines

    out, lines = run_async(scenario())
    assert out.kind == "finished" and out.exit_code == 0
    assert lines == ["line-1", "tail-no-newline"]  # 尾行在 rc 落盘后仍被交付
    assert out.offset == (job / "job.log").stat().st_size  # offset 推进到 EOF


def test_final_drain_flushes_tail_on_cancelled_finished(tmp_path):
    """取消与正常退出竞态(rc 先落盘)走 cancel_and_wait 收口:终读同样生效。"""
    backend = LocalJobBackend(hb_stale=5.0 * TF)
    job = tmp_path / "job"
    job.mkdir()
    (job / "job.pid").write_text("99999", encoding="utf-8")
    (job / "job.hb").touch()
    (job / "job.log").write_bytes(b"done-without-newline")
    (job / "job.rc").write_text("0", encoding="utf-8")
    token = CancelToken()
    token.cancel()

    lines: list[str] = []
    out = run_async(follow_job(backend, job, idle_timeout=5 * TF, total_timeout=10 * TF,
                               token=token, on_line=lines.append, poll=0.05))
    assert out.kind == "cancelled" and out.exit_code == 0
    assert lines == ["done-without-newline"]


# ================ 场景 10:取消分支遇不可杀进程(#8) ================

def test_cancel_unkillable_child_finalizes_rc_129(tmp_path, monkeypatch):
    """kill 后 wait 仍超时(进程卡内核态:不可中断 IO / 驱动挂死)→ wrapper
    不再裸崩:诊断行进 job.log、固定 rc=129 落盘、返回 129(FOLLOWUPS #8)。

    修复前 TimeoutExpired 未捕获,wrapper 崩溃不写 rc,上层只能等孤儿判定
    (慢),诊断埋在 wrapper.err 对日志流不可见。真实不可杀进程无法稳定
    构造(需要内核态卡死),用假 Popen 演这个角色。
    """
    from insar_agent.runtime import local_wrapper

    job = tmp_path / "job"
    job.mkdir()
    (job / "cmd.json").write_text(json.dumps(
        {"argv": ["unkillable"], "cwd": str(job), "env": {}}), encoding="utf-8")
    (job / "job.cancel").touch()               # 进循环立即走取消分支

    class _UnkillableProc:
        pid = 4242

        def poll(self):
            return None                        # 永不退出

        def terminate(self):
            pass

        def kill(self):
            pass

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("unkillable", timeout)

    monkeypatch.setattr(local_wrapper.subprocess, "Popen",
                        lambda *a, **kw: _UnkillableProc())
    monkeypatch.setattr(local_wrapper, "GRACE", 0.2)  # 缩短宽限,测试秒级完成

    rc = local_wrapper.main(str(job))
    assert rc == 129
    assert (job / "job.rc").read_text().strip() == "129"
    log_text = (job / "job.log").read_text(encoding="utf-8")
    assert "[wrapper]" in log_text and "129" in log_text  # 诊断随日志流可见


# ================ 场景 11:高成本判活(WSL)自适应节流 ================

class _ScriptedBackend:
    """内存态假后端:按时间线给状态,记录每次 state() 的时刻供节流断言。
    expensive=True 时声明 state_probe_expensive(WslJobBackend 同款标记)。"""

    def __init__(self, outcome_after: float, outcome: str = "finished",
                 expensive: bool = True):
        if expensive:
            self.state_probe_expensive = True
        self._t0 = time.monotonic()
        self._after = outcome_after
        self._outcome = outcome
        self.probe_times: list[float] = []

    def state(self, job_dir) -> JobState:
        self.probe_times.append(time.monotonic() - self._t0)
        if time.monotonic() - self._t0 >= self._after:
            if self._outcome == "finished":
                return JobState("finished", 0)
            return JobState(self._outcome)
        return JobState("alive")

    def cancel(self, job_dir) -> None:
        pass

    def read_new_lines(self, job_dir, offset, *, final: bool = False):
        return [], offset


def test_expensive_probe_throttled_after_dense_window(tmp_path):
    """声明 state_probe_expensive 的后端(WSL 每次判活 spawn 一次 wsl.exe):
    密集窗口过后探测降频;同参数下未声明的后端仍每轮探测。日志 drain 的
    调用节奏不属于 state(),不受影响。"""
    expensive = _ScriptedBackend(outcome_after=999, expensive=True)
    cheap = _ScriptedBackend(outcome_after=999, expensive=False)
    # 窗口整体乘 TF:密集窗口内至少两次探测 / 节流后显著更少的比值断言不变
    common = dict(idle_timeout=30 * TF, total_timeout=1.5 * TF, poll=0.05,
                  cancel_grace=0.05, probe_dense_window=0.25 * TF,
                  probe_idle_interval=10.0 * TF)
    out_e = run_async(follow_job(expensive, tmp_path, **common))
    out_c = run_async(follow_job(cheap, tmp_path, **common))
    assert out_e.kind == "total_timeout" and out_c.kind == "total_timeout"
    assert len(expensive.probe_times) >= 2            # 密集窗口内确实探测过
    # 同样时间线下,节流后端探测显著更少(idle 间隔 10s 远大于总时长)
    assert len(expensive.probe_times) < len(cheap.probe_times) / 2


def test_throttled_probe_still_detects_finished(tmp_path):
    """节流不改变结局:finished 仍被发现(延迟上限为 idle 探测间隔)。"""
    backend = _ScriptedBackend(outcome_after=0.3, outcome="finished")
    out = run_async(follow_job(backend, tmp_path, idle_timeout=30 * TF,
                               total_timeout=30 * TF,
                               poll=0.05, probe_dense_window=0.1,
                               probe_idle_interval=0.5))
    assert out.kind == "finished" and out.exit_code == 0


def test_orphan_confirmation_bypasses_throttle(tmp_path):
    """孤儿双击语义在节流下不放大:一击后的确认探测绕过节流间隔。

    idle 间隔故意设 2s×TF:若确认探测也被节流,末两次探测间隔必然 ≈2s×TF;
    绕过后应保持 ~0.3s(strike 后的短睡重试)—— 判定阈取两者中线随 TF 同步放宽。
    """
    backend = _ScriptedBackend(outcome_after=0.2, outcome="orphaned")
    out = run_async(follow_job(backend, tmp_path, idle_timeout=30 * TF,
                               total_timeout=30 * TF,
                               poll=0.05, probe_dense_window=0.1,
                               probe_idle_interval=2.0 * TF))
    assert out.kind == "orphaned"
    assert len(backend.probe_times) >= 2
    confirm_gap = backend.probe_times[-1] - backend.probe_times[-2]
    assert confirm_gap < 1.0 * TF  # 确认探测未被 2s×TF 节流间隔拖慢
