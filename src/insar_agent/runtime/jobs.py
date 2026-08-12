"""作业目录契约(AGENT-DESIGN §4.7)。

进程身份跨不过宿主/WSL 边界,所以一切进程操作退化为对作业目录内文件的操作:

    job_dir/
    ├── cmd.json        要执行的命令(argv/cwd/env;LocalBackend 用)
    ├── cmd.sh          等价裸命令脚本(渲染产物,可 diff 可复现,§4.7 副产品)
    ├── job.pid         wrapper 写入的执行方 PID
    ├── job.token       启动令牌(防 PID 复用误判;WSL 侧用 /proc starttime)
    ├── job.hb          心跳文件(LocalBackend 判活依据;WSL 侧用 /proc)
    ├── job.log         stdout+stderr 合流(宿主按 offset 增量读)
    ├── job.rc          退出码 —— 只在真正结束时写入,是完成的唯一标志
    └── job.cancel      宿主创建此文件 = 请求取消(wrapper 轮询,整组 kill)

判活三态之外新增 orphaned(§4.7):wrapper 死了但没写 rc(WSL 关机 / OOM / kill -9),
处置是「保留日志与产物、标 FAILED(wsl_orphaned)、可续跑」,与计算失败严格区分。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

HB_STALE_SECONDS = 5.0  # 心跳超过此秒数未更新 → orphaned(wrapper 每 0.3s 刷一次)


@dataclass(frozen=True)
class JobState:
    kind: str  # alive | finished | orphaned | unknown
    exit_code: int | None = None


@dataclass(frozen=True)
class CommandPlan:
    """一次外部命令的完整描述(由 engines 构建,executor 消费)。"""

    argv: list[str]
    cwd: str
    env: dict[str, str]
    files: dict[str, str]  # 需要渲染进工作区的配置文件 {relpath: content}
    shell_line: str = ""  # 等价裸命令(写进 cmd.sh;为空则由 argv 拼接)


class JobBackend(Protocol):
    def prepare(self, job_dir: Path, plan: CommandPlan) -> None: ...
    def launch(self, job_dir: Path) -> None: ...
    def state(self, job_dir: Path) -> JobState: ...
    def cancel(self, job_dir: Path) -> None: ...
    def read_new_lines(self, job_dir: Path, offset: int) -> tuple[list[str], int]: ...


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)  # POSIX/Windows 都原子(§6.1 写入原则)


def shell_quote(arg: str) -> str:
    if not arg or any(c in arg for c in " \t\"'\\$&|;<>()"):
        return "'" + arg.replace("'", "'\\''") + "'"
    return arg


class LocalJobBackend:
    """宿主本地后端:开发/测试/无 WSL 环境。

    wrapper 是独立 Python 进程(detached),父进程死亡不影响作业 —— reattach 可测。
    """

    def __init__(self, hb_stale: float = HB_STALE_SECONDS):
        self.hb_stale = hb_stale

    def prepare(self, job_dir: Path, plan: CommandPlan) -> None:
        job_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(job_dir / "cmd.json", json.dumps(
            {"argv": plan.argv, "cwd": plan.cwd, "env": plan.env}, ensure_ascii=False, indent=1))
        line = plan.shell_line or " ".join(shell_quote(a) for a in plan.argv)
        _atomic_write(job_dir / "cmd.sh",
                      f"#!/usr/bin/env bash\n# cwd: {plan.cwd}\n{line}\n")

    def launch(self, job_dir: Path) -> None:
        kwargs: dict = {}
        if sys.platform == "win32":
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            CREATE_NO_WINDOW = 0x08000000
            kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        # wrapper 自身的启动错误必须可诊断(显式失败原则,§1.4)——
        # 曾发生 spawn 后静默死亡且 DEVNULL 吞掉全部线索的事故
        # 按文件路径启动而非 -m:wrapper 是纯 stdlib 脚本,任何 Python 都能跑,
        # 免疫「sys.executable 被环境污染成没装本包的解释器」这类事故
        from insar_agent.runtime import local_wrapper as _wrapper_module

        wrapper_path = Path(_wrapper_module.__file__).resolve()
        wrapper_err = open(job_dir / "wrapper.err", "ab")
        try:
            subprocess.Popen(
                [sys.executable, str(wrapper_path), str(job_dir)],
                stdout=subprocess.DEVNULL, stderr=wrapper_err, stdin=subprocess.DEVNULL,
                **kwargs)
        finally:
            wrapper_err.close()  # 子进程持有继承的句柄,父进程侧关闭不影响

    def state(self, job_dir: Path) -> JobState:
        rc = job_dir / "job.rc"
        if rc.exists():
            try:
                return JobState("finished", int(rc.read_text().strip() or "-1"))
            except ValueError:
                return JobState("finished", -1)
        pid_f = job_dir / "job.pid"
        if not pid_f.exists():
            return JobState("unknown")  # wrapper 尚未启动(或启动失败)
        hb = job_dir / "job.hb"
        if not hb.exists():
            return JobState("orphaned")
        age = time.time() - hb.stat().st_mtime
        if age > self.hb_stale:
            return JobState("orphaned")  # wrapper 死了但没写 rc:kill -9 / 断电
        return JobState("alive")

    def cancel(self, job_dir: Path) -> None:
        (job_dir / "job.cancel").touch()

    def read_new_lines(self, job_dir: Path, offset: int) -> tuple[list[str], int]:
        """按 offset 增量读完整行;半行留到下次(§4.4:只在读到换行符时才提交)。"""
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
            return [], offset  # 只有半行
        complete = chunk[: last_nl + 1]
        lines = complete.decode("utf-8", errors="replace").splitlines()
        return lines, offset + len(complete)
