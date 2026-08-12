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

所有场景用 tmp_path 作业目录 + 真实 LocalJobBackend;子进程一律秒级
python 小脚本(sys.executable),长睡脚本都带自灭上限并在断言后清理。
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from insar_agent.runtime.jobs import CommandPlan, JobState, LocalJobBackend
from insar_agent.runtime.stream import CancelToken, follow_job

WIN = sys.platform == "win32"

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
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(interval)
    pytest.fail(f"{msg}(等待 {timeout}s)")


async def _await_until(cond, timeout: float = 10.0, msg: str = "条件未满足"):
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
    backend = LocalJobBackend(hb_stale=0.8)
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
        out = run_async(follow_job(backend, job, idle_timeout=30, total_timeout=30,
                                   on_line=lines.append, poll=0.1, startup_grace=5))
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
    backend = _OneShotOrphanBackend(LocalJobBackend(hb_stale=5.0))
    job = tmp_path / "job"
    _launch(backend, job, "print('ok', flush=True)\n")
    out = run_async(follow_job(backend, job, idle_timeout=20, total_timeout=30,
                               poll=0.1, startup_grace=30))
    assert out.kind == "finished" and out.exit_code == 0


# ================ 场景 2:心跳停更 / 子进程挂死 ================

def test_heartbeat_stall_marks_orphan(tmp_path):
    """wrapper 名存实亡(心跳文件停更,如休眠恢复/线程僵死)→ 连续命中判孤儿。"""
    backend = LocalJobBackend(hb_stale=0.5)
    job = tmp_path / "job"
    _fake_alive_job(job, log_text="partial output\n")  # hb 此后不再刷新 = 停更
    log_size = (job / "job.log").stat().st_size  # 文本模式落盘为 \r\n,按实际字节数对账

    lines: list[str] = []
    out = run_async(follow_job(backend, job, idle_timeout=30, total_timeout=30,
                               on_line=lines.append, poll=0.1, startup_grace=5))
    assert out.kind == "orphaned" and out.exit_code is None
    assert lines == ["partial output"]  # 判定前既有日志已 drain,offset 不回退
    assert out.offset == log_size


def test_idle_timeout_on_hung_child(tmp_path):
    """子进程挂死(wrapper 心跳正常、无输出)→ idle_timeout 触发协作取消。

    断言:rc=143(wrapper 整组终止)、job.cancel 存在(走文件契约而非宿主强杀)、
    wrapper 与子进程都不残留。
    """
    backend = LocalJobBackend(hb_stale=3.0)
    job = tmp_path / "job"
    _launch(backend, job, "print('start', flush=True)\ntime.sleep(60)\n")
    _wait_wrapper_up(backend, job)

    out = run_async(follow_job(backend, job, idle_timeout=1.0, total_timeout=60,
                               poll=0.1, cancel_grace=10, startup_grace=30))
    assert out.kind == "idle_timeout" and out.exit_code == 143
    assert (job / "job.cancel").exists()
    assert (job / "job.rc").read_text().strip() == "143"
    _assert_no_residue(job)


# ================ 场景 3:total timeout ================

def test_total_timeout_kills_active_child(tmp_path):
    """任务持续产出但超总时长 → total_timeout;有输出不能豁免总超时,进程整组清理。"""
    backend = LocalJobBackend(hb_stale=3.0)
    job = tmp_path / "job"
    body = ("for i in range(300):\n"          # 自灭上限 30s,实际 ~1.5s 被总超时终止
            "    print(f'busy {i}', flush=True)\n"
            "    time.sleep(0.1)\n")
    _launch(backend, job, body)
    _wait_wrapper_up(backend, job)

    lines: list[str] = []
    out = run_async(follow_job(backend, job, idle_timeout=30, total_timeout=1.5,
                               on_line=lines.append, poll=0.1, cancel_grace=10,
                               startup_grace=30))
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
    backend = LocalJobBackend(hb_stale=3.0)
    job = tmp_path / "job"
    _launch(backend, job, "print('quick done', flush=True)\n")
    _wait_until(lambda: (job / "job.rc").exists(), msg="作业未在时限内结束")

    token = CancelToken()
    token.cancel()
    token.cancel()  # 令牌幂等:重复取消无害
    lines: list[str] = []
    out = run_async(follow_job(backend, job, idle_timeout=10, total_timeout=15,
                               token=token, on_line=lines.append, poll=0.05,
                               cancel_grace=5, startup_grace=10))
    assert out.kind == "cancelled"
    assert out.exit_code == 0             # 真实退出码,不被伪造
    assert any("quick done" in ln for ln in lines)  # 返回前日志 drain 到底

    backend.cancel(job)
    backend.cancel(job)                   # 后端取消幂等:对已结束作业无副作用
    st = backend.state(job)
    assert st.kind == "finished" and st.exit_code == 0
    # 残留 cancel 标记不影响 reattach 再跟随:仍按 finished 收口
    out2 = run_async(follow_job(backend, job, idle_timeout=5, total_timeout=5, poll=0.05))
    assert out2.kind == "finished" and out2.exit_code == 0


def test_cancel_midrun_terminates_group(tmp_path):
    """基线取消路径:运行中取消 → rc=143,wrapper/子进程双双退出,重复取消无害。"""
    backend = LocalJobBackend(hb_stale=3.0)
    job = tmp_path / "job"
    body = ("for i in range(200):\n"          # 自灭上限 20s
            "    print(f'line {i}', flush=True)\n"
            "    time.sleep(0.1)\n")
    _launch(backend, job, body)
    _wait_wrapper_up(backend, job)

    token = CancelToken()

    async def scenario():
        task = asyncio.create_task(
            follow_job(backend, job, idle_timeout=30, total_timeout=60, token=token,
                       poll=0.1, cancel_grace=10, startup_grace=30))
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
    backend = LocalJobBackend(hb_stale=5.0)
    job = tmp_path / "job"
    _fake_alive_job(job, log_text="some output\n")

    async def scenario():
        task = asyncio.create_task(
            follow_job(backend, job, idle_timeout=10, total_timeout=15, poll=0.05))
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
    backend = LocalJobBackend(hb_stale=5.0)
    job = tmp_path / "job"
    _fake_alive_job(job, log_text="line-1\nline-2\n")

    async def scenario():
        lines: list[str] = []
        task = asyncio.create_task(
            follow_job(backend, job, idle_timeout=10, total_timeout=15,
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
    backend = LocalJobBackend(hb_stale=5.0)
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
            follow_job(backend, job, idle_timeout=10, total_timeout=15,
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
    backend = _FlakyStateBackend(LocalJobBackend(hb_stale=5.0))
    job = tmp_path / "job"
    _launch(backend, job, "print('ok', flush=True)\n")
    out = run_async(follow_job(backend, job, idle_timeout=20, total_timeout=30,
                               poll=0.1, startup_grace=30))
    assert out.kind == "finished" and out.exit_code == 0


# ================ 场景 7:wrapper 启动即失败 ================

def test_spawn_failure_missing_executable(tmp_path):
    """目标可执行文件不存在 → rc=127 快速失败,诊断行随日志流可读。"""
    backend = LocalJobBackend(hb_stale=3.0)
    job = tmp_path / "job"
    plan = CommandPlan(argv=[str(tmp_path / "no_such_tool.exe"), "--run"],
                       cwd=str(tmp_path), env={}, files={})
    backend.prepare(job, plan)
    backend.launch(job)

    lines: list[str] = []
    t0 = time.monotonic()
    out = run_async(follow_job(backend, job, idle_timeout=20, total_timeout=40,
                               on_line=lines.append, poll=0.1, startup_grace=30))
    assert out.kind == "finished" and out.exit_code == 127
    assert time.monotonic() - t0 < 15         # 快速失败,不等超时/孤儿宽限
    assert any("[wrapper] spawn failed" in ln for ln in lines)  # 诊断可读


@pytest.mark.parametrize("mode", ["missing", "corrupt"])
def test_bad_cmd_json_fails_fast(tmp_path, mode):
    """cmd.json 缺失/损坏(渲染中断、外部篡改)→ rc=127 快速失败 + 诊断进日志流。

    修复前的真实 bug:wrapper 未捕获读取/解析异常,静默死亡(无 pid 无 rc),
    follow 只能等 startup_grace 耗尽后误判 orphaned,诊断埋在 wrapper.err
    里不进日志流。
    """
    backend = LocalJobBackend(hb_stale=3.0)
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
    out = run_async(follow_job(backend, job, idle_timeout=20, total_timeout=40,
                               on_line=lines.append, poll=0.1, startup_grace=30))
    assert out.kind == "finished" and out.exit_code == 127
    assert time.monotonic() - t0 < 15         # 不吃满 startup_grace
    assert any("[wrapper] bad cmd.json" in ln for ln in lines)
