"""WSL 作业后端(AGENT-DESIGN §4.7/§4.8/§4.9)。

宿主侧三个操作全部退化为文件/命令操作:
  判活   读 job.rc;否则查 job.pid + job.start 二元组(starttime 防 PID 复用)
  取消   touch job.cancel(wrapper 在 Linux 侧执行整组 kill)
  续读   打开 job.log,seek(offset)

路径纪律(§4.9):
  - LLM 只见逻辑句柄;cmd.sh 里只出现 WSL 路径;宿主访问统一走 host_path()。
  - 宿主侧只读小文件(job.log/job.rc/json);遍历目录、读栅格必须在 WSL 内做。
  - 作业契约目录在 Linux fs(<root>/.jobs/...,§4.8 9p 红线);Windows 工作区
    只作为输入源经 /mnt 映射进 cmd.sh 的 cwd(纪律上只读,§4.9 规则 4)。

runner 可注入,单测用假 runner 验证命令构造;生产路由见 runtime/backend_select.py。
"""

from __future__ import annotations

import re
import subprocess
import sys
from importlib import resources
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable

from insar_agent.runtime.jobs import CommandPlan, JobState, shell_quote

_HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")

Runner = Callable[[list[str], float], subprocess.CompletedProcess]


_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _default_runner(argv: list[str], timeout: float) -> subprocess.CompletedProcess:
    # CREATE_NO_WINDOW:wsl.exe 探测/控制命令在后台执行,不弹控制台
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                          creationflags=_NO_WINDOW)


def check_handle(task_id: str) -> str:
    """task_id 唯一句柄校验:拒绝 . / \\ 空格 Unicode(消掉整类路径幻觉,§4.9)。"""
    if not _HANDLE_RE.match(task_id):
        raise ValueError(f"invalid task handle: {task_id!r}")
    return task_id


class WslPaths:
    def __init__(self, distro: str = "Ubuntu", root: str = "/home/insar/work"):
        self.distro = distro
        self.root = PurePosixPath(root)

    def wsl_path(self, task_id: str, *parts: str) -> PurePosixPath:
        check_handle(task_id)
        for p in parts:
            if ".." in PurePosixPath(p).parts:
                raise ValueError(f"path escape: {p}")
        return self.root / task_id / PurePosixPath(*parts) if parts else self.root / task_id

    def host_path(self, task_id: str, *parts: str) -> Path:
        """宿主侧读取用。\\\\wsl.localhost 走 9p,只用于小文件(日志/json),
        绝不用于遍历产物目录或读栅格 —— 那些必须在 WSL 内做(§4.9 性能红线)。"""
        wsl = self.wsl_path(task_id, *parts)
        return Path(rf"\\wsl.localhost\{self.distro}") / str(wsl).lstrip("/")

    def host_root(self, *parts: str) -> Path:
        """root(或其下子路径)的宿主视图,不经过 task_id 句柄校验 —— 只给
        作业契约目录(.jobs/...)这类基础设施路径用;同样受 §4.9 小文件红线约束。"""
        wsl = self.root.joinpath(*parts) if parts else self.root
        return Path(rf"\\wsl.localhost\{self.distro}") / str(wsl).lstrip("/")

    @staticmethod
    def to_wsl(path: str | Path) -> PurePosixPath:
        """宿主路径 → WSL 侧路径(§4.9 三坐标系换算;cmd.sh 里只准出现 WSL 路径)。

        - 已是 POSIX 绝对路径:原样返回(Linux fs 工作区)
        - \\\\wsl.localhost\\<distro>\\home\\... / \\\\wsl$\\... → /home/...
        - 盘符路径 E:\\ws → /mnt/e/ws(Windows 工作区经 9p 挂载;纪律上只读,
          §4.9 规则 4:中间产物应写 Linux fs)
        其余形态显式拒绝,绝不猜。
        """
        s = str(path)
        if s.startswith("/"):
            return PurePosixPath(s)
        p = PureWindowsPath(s)
        drive = p.drive  # 'E:' 或 '\\\\wsl.localhost\\insar'
        if drive.startswith("\\\\"):
            host = drive[2:].split("\\", 1)[0].lower()
            if host in ("wsl.localhost", "wsl$"):
                return PurePosixPath("/", *p.parts[1:])
            raise ValueError(f"无法映射到 WSL 的 UNC 路径:{s}")
        if len(drive) == 2 and drive[1] == ":" and p.root:
            return PurePosixPath("/mnt", drive[0].lower(), *p.parts[1:])
        raise ValueError(f"无法映射到 WSL 的路径(需要绝对路径):{s}")


class WslJobBackend:
    """作业目录在 WSL 的 Linux 文件系统上;宿主通过 \\\\wsl.localhost 读小文件。"""

    # 判活探测要 spawn 一次 wsl.exe(百毫秒级)—— follow_job 据此启用自适应
    # 节流(runtime/stream.py:启动初期密集、稳定运行后拉大间隔;WSL P2)
    state_probe_expensive = True

    def __init__(self, paths: WslPaths | None = None, runner: Runner | None = None,
                 keepalive: bool = True, user: str = "root"):
        self.paths = paths or WslPaths()
        self.runner = runner or _default_runner
        self._keepalive_proc: subprocess.Popen | None = None
        self.keepalive = keepalive
        # 实测教训(docs/VALIDATION-isce2-wsl.md):发行版名 ≠ 发行版内用户名
        # (distro insar 的默认用户是 ubuntu/uid 1000),作业统一以 root 跑
        self.user = user

    # -- WSL 生命周期(§4.8:空闲 8 秒回收 VM,run 期间必须保活) --

    def ensure_keepalive(self) -> None:
        if not self.keepalive:
            return
        if self._keepalive_proc is None or self._keepalive_proc.poll() is not None:
            self._keepalive_proc = subprocess.Popen(
                ["wsl.exe", "-d", self.paths.distro, "--exec", "sleep", "infinity"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def release_keepalive(self) -> None:
        """释放本实例拉起的保活进程(wsl.exe sleep infinity;WSL P2 释放钩子)。

        幂等:重复调用、从未拉起、进程已死都安全。只终结自己 spawn 的进程句柄,
        绝不影响其他实例/其他 run 的保活;VM 是否随之空闲回收由 WSL 自决
        (§4.8:作业进程活着时 VM 不算空闲)。run 收尾由 driver 统一调用,
        防止 sleep infinity 随 run 数量堆积泄漏。
        """
        proc, self._keepalive_proc = self._keepalive_proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5.0)  # 回收句柄,不留僵尸 Popen
        except subprocess.TimeoutExpired:
            proc.kill()  # terminate 未生效的极端情形:强杀兜底,交 OS 回收

    def _wsl(self, script: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
        # bash -l 登录 shell:加载 /etc/profile.d/insar.sh(PATH/PROJ_DATA/GDAL_DATA,
        # scripts/wsl_setup.sh 固化;实测缺 PROJ_DATA 时 geocode 直接失败)
        return self.runner(
            ["wsl.exe", "-d", self.paths.distro, "-u", self.user,
             "--exec", "bash", "-lc", script], timeout)

    # -- 作业目录契约 --

    def job_root(self, workspace: Path) -> Path:
        """作业契约目录根(宿主视图)。刻意无视 workspace:job.log/job.hb 这类
        高频小写入必须落 Linux fs(§4.8 9p 红线),不能跟着 Windows 工作区走。"""
        del workspace  # 签名与 executor 的调用点对齐,本后端不使用
        return self.paths.host_root(".jobs")

    def prepare(self, job_dir: Path, plan: CommandPlan, *,
                wsl_job_dir: str | None = None) -> None:
        """job_dir 传 WSL 侧 POSIX 路径的宿主映射;目录先在 WSL 内建好
        (顺带拉起 VM),契约小文件经 9p 写入。"""
        posix = wsl_job_dir or self._to_posix(job_dir)
        self.ensure_keepalive()
        made = self._wsl(f"mkdir -p {shell_quote(posix)}")
        if made.returncode != 0:
            raise RuntimeError(
                f"WSL 侧创建作业目录失败(rc={made.returncode}):"
                f"{(made.stderr or made.stdout or '').strip()[:300]}")
        job_dir.mkdir(parents=True, exist_ok=True)
        # cwd 换算成 WSL 坐标系(Windows 工作区 → /mnt 只读输入,§4.9);
        # 保留原始宿主路径为注释,cmd.sh 仍可 diff 可复现
        cwd = str(self.paths.to_wsl(plan.cwd))
        line = plan.shell_line or " ".join(shell_quote(a) for a in plan.argv)
        env_lines = "\n".join(f"export {k}={shell_quote(v)}" for k, v in plan.env.items())
        (job_dir / "cmd.sh").write_text(
            f"#!/usr/bin/env bash\nset -uo pipefail\n# host cwd: {plan.cwd}\n"
            f"cd {shell_quote(cwd)}\n{env_lines}\n{line}\n",
            encoding="utf-8", newline="\n")
        wrapper = (resources.files("insar_agent.runtime") / "wsl_wrapper.sh").read_text("utf-8")
        (job_dir / "wrapper.sh").write_text(wrapper, encoding="utf-8", newline="\n")

    def launch(self, job_dir: Path, *, wsl_job_dir: str | None = None) -> None:
        self.ensure_keepalive()
        posix = wsl_job_dir or self._to_posix(job_dir)
        # 2026-08-12 实测:nohup+&+disown 的后台进程在 wsl.exe 退出时被 WSL
        # 会话回收连坐杀掉,作业根本起不来;setsid --fork 把 wrapper 派生进
        # 独立会话才真正脱离本次调用存活(VM 空闲回收由 keepalive/轮询兜住,§4.8)
        started = self._wsl(
            f"setsid --fork bash {shell_quote(posix + '/wrapper.sh')} {shell_quote(posix)} "
            f">/dev/null 2>&1 </dev/null", timeout=15.0)
        if started.returncode != 0:
            raise RuntimeError(
                f"WSL 侧启动 wrapper 失败(rc={started.returncode}):"
                f"{(started.stderr or started.stdout or '').strip()[:300]}")

    def state(self, job_dir: Path, *, wsl_job_dir: str | None = None) -> JobState:
        # 与 LocalJobBackend.state 同款 TOCTOU 内聚防护(FOLLOWUPS #6),且更宽:
        # 契约小文件经 \\wsl.localhost(9p)读取,除删除/独占竞态外还有整类
        # 网络性 OSError(VM 正在关闭、9p 会话断开)。统一按「瞬时不可读 →
        # unknown」处置:上层单次 unknown 不改变结局,VM 真没了由后续轮询的
        # orphaned/startup_grace/双超时兜底。
        try:
            rc = job_dir / "job.rc"
            if rc.exists():
                try:
                    return JobState("finished", int(rc.read_text().strip() or "-1"))
                except ValueError:
                    return JobState("finished", -1)
            pid_f = job_dir / "job.pid"
            if not pid_f.exists():
                return JobState("unknown")
            pid = pid_f.read_text().strip()
            want = ""
            start_f = job_dir / "job.start"
            if start_f.exists():
                want = start_f.read_text().strip()
        except OSError:
            return JobState("unknown")
        # 在 WSL 内核实,绝不在宿主 os.kill(§0.5.2:宿主没有 POSIX 信号 API)
        try:
            got = self._wsl(f"awk '{{print $22}}' /proc/{pid}/stat 2>/dev/null || true",
                            timeout=10.0).stdout.strip()
        except (subprocess.TimeoutExpired, OSError):
            return JobState("unknown")  # WSL 本身不可达:保守返回 unknown,上层重试
        if not got:
            return JobState("orphaned")  # 进程没了但没写 rc(wsl --shutdown / OOM)
        if want and got != want:
            return JobState("orphaned")  # PID 被复用,原进程已死
        return JobState("alive")

    def cancel(self, job_dir: Path) -> None:
        (job_dir / "job.cancel").touch()

    def read_new_lines(self, job_dir: Path, offset: int, *,
                       final: bool = False) -> tuple[list[str], int]:
        # 语义与 LocalJobBackend 一致:增量只提交完整行;final=True 终读模式
        # 冲刷无换行的尾部余量(作业 finished 后日志不会再写,FOLLOWUPS #7)
        log = job_dir / "job.log"
        if not log.exists():
            return [], offset
        with open(log, "rb") as f:
            f.seek(offset)
            chunk = f.read()
        if not chunk:
            return [], offset
        if final:
            return chunk.decode("utf-8", errors="replace").splitlines(), offset + len(chunk)
        last_nl = chunk.rfind(b"\n")
        if last_nl == -1:
            return [], offset
        complete = chunk[: last_nl + 1]
        return complete.decode("utf-8", errors="replace").splitlines(), offset + len(complete)

    @staticmethod
    def _to_posix(host_job_dir: Path) -> str:
        r"""\\wsl.localhost\<distro>\home\insar\... → /home/insar/...

        作业目录必须在 Linux fs(§4.8):盘符路径进到这里说明接线有错,显式拒绝。
        Windows 下 UNC 前缀整体是 parts[0] 锚(如 '\\wsl.localhost\insar\'),
        旧实现按 POSIX 拆分假设逐段找 'wsl.localhost',在宿主上永远匹配不到。
        """
        p = PureWindowsPath(str(host_job_dir))
        if p.drive.startswith("\\\\"):
            host = p.drive[2:].split("\\", 1)[0].lower()
            if host in ("wsl.localhost", "wsl$"):
                return "/" + "/".join(p.parts[1:])
        # POSIX 形态字符串(如 //wsl.localhost/<distro>/home/...)的兼容拆分
        parts = host_job_dir.parts
        for i, q in enumerate(parts):
            if q.lower().startswith(("wsl.localhost", "wsl$")):
                return "/" + "/".join(parts[i + 2:])
        raise ValueError(f"not a wsl.localhost path: {host_job_dir}")


def wsl_status(runner: Runner | None = None) -> dict:
    """wsl.exe 存在性与已装发行版(§0.5.1 的机器可读版)。"""
    run = runner or _default_runner
    try:
        cp = run(["wsl.exe", "-l", "-q"], 10.0)
        # wsl.exe 输出常为 UTF-16;runner text=True 时已解码,残留 NUL 需剔除
        distros = [d.strip().replace("\x00", "") for d in (cp.stdout or "").splitlines()]
        distros = [d for d in distros if d]
        return {"installed": cp.returncode == 0 and bool(distros), "distros": distros}
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return {"installed": False, "distros": []}
