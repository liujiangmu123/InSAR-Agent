"""WSL 作业后端(AGENT-DESIGN §4.7/§4.8/§4.9)。

宿主侧三个操作全部退化为文件/命令操作:
  判活   读 job.rc;否则查 job.pid + job.start 二元组(starttime 防 PID 复用)
  取消   touch job.cancel(wrapper 在 Linux 侧执行整组 kill)
  续读   打开 job.log,seek(offset)

路径纪律(§4.9):
  - LLM 只见逻辑句柄;cmd.sh 里只出现 WSL 路径;宿主访问统一走 host_path()。
  - 宿主侧只读小文件(job.log/job.rc/json);遍历目录、读栅格必须在 WSL 内做。

本机 WSL 尚未安装(§0.5.1),本模块的 runner 可注入,单测用假 runner 验证命令构造。
"""

from __future__ import annotations

import re
import subprocess
import sys
from importlib import resources
from pathlib import Path, PurePosixPath
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


class WslJobBackend:
    """作业目录在 WSL 的 Linux 文件系统上;宿主通过 \\\\wsl.localhost 读小文件。"""

    def __init__(self, paths: WslPaths | None = None, runner: Runner | None = None,
                 keepalive: bool = True):
        self.paths = paths or WslPaths()
        self.runner = runner or _default_runner
        self._keepalive_proc: subprocess.Popen | None = None
        self.keepalive = keepalive

    # -- WSL 生命周期(§4.8:空闲 8 秒回收 VM,run 期间必须保活) --

    def ensure_keepalive(self) -> None:
        if not self.keepalive:
            return
        if self._keepalive_proc is None or self._keepalive_proc.poll() is not None:
            self._keepalive_proc = subprocess.Popen(
                ["wsl.exe", "-d", self.paths.distro, "--exec", "sleep", "infinity"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def release_keepalive(self) -> None:
        if self._keepalive_proc is not None and self._keepalive_proc.poll() is None:
            self._keepalive_proc.terminate()
        self._keepalive_proc = None

    def _wsl(self, script: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
        return self.runner(
            ["wsl.exe", "-d", self.paths.distro, "--exec", "bash", "-lc", script], timeout)

    # -- 作业目录契约 --

    def prepare(self, job_dir: Path, plan: CommandPlan) -> None:
        """job_dir 传 WSL 侧 POSIX 路径的宿主映射;文件经 host_path 写入。"""
        job_dir.mkdir(parents=True, exist_ok=True)
        line = plan.shell_line or " ".join(shell_quote(a) for a in plan.argv)
        env_lines = "\n".join(f"export {k}={shell_quote(v)}" for k, v in plan.env.items())
        (job_dir / "cmd.sh").write_text(
            f"#!/usr/bin/env bash\nset -uo pipefail\ncd {shell_quote(plan.cwd)}\n"
            f"{env_lines}\n{line}\n",
            encoding="utf-8", newline="\n")
        wrapper = (resources.files("insar_agent.runtime") / "wsl_wrapper.sh").read_text("utf-8")
        (job_dir / "wrapper.sh").write_text(wrapper, encoding="utf-8", newline="\n")

    def launch(self, job_dir: Path, *, wsl_job_dir: str | None = None) -> None:
        self.ensure_keepalive()
        posix = wsl_job_dir or self._to_posix(job_dir)
        # nohup + & :wrapper 独立于本次 wsl.exe 调用存活
        self._wsl(f"nohup bash {shell_quote(posix + '/wrapper.sh')} {shell_quote(posix)} "
                  f">/dev/null 2>&1 & disown", timeout=15.0)

    def state(self, job_dir: Path, *, wsl_job_dir: str | None = None) -> JobState:
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

    def read_new_lines(self, job_dir: Path, offset: int) -> tuple[list[str], int]:
        log = job_dir / "job.log"
        if not log.exists():
            return [], offset
        with open(log, "rb") as f:
            f.seek(offset)
            chunk = f.read()
        if not chunk:
            return [], offset
        last_nl = chunk.rfind(b"\n")
        if last_nl == -1:
            return [], offset
        complete = chunk[: last_nl + 1]
        return complete.decode("utf-8", errors="replace").splitlines(), offset + len(complete)

    @staticmethod
    def _to_posix(host_job_dir: Path) -> str:
        r"""\\wsl.localhost\Ubuntu\home\insar\... → /home/insar/..."""
        parts = host_job_dir.parts
        for i, p in enumerate(parts):
            if p.lower().startswith("wsl.localhost"):
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
