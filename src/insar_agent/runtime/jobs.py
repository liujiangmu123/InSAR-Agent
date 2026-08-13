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
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from insar_agent.core.fsio import atomic_write_text

HB_STALE_SECONDS = 5.0  # 心跳超过此秒数未更新 → orphaned(wrapper 每 0.3s 刷一次)


def wrapper_python() -> str:
    """本地作业的 Python 解释器(wrapper 与 python 型脚本共用)。

    冻结态(PyInstaller)sys.executable 是后端 exe 本身 —— 引导器不解释脚本
    参数,直接用会**误派生第二个后端实例**且作业永无心跳(DESKTOP-PARITY
    GAP-1 实测)。解析序:INSAR_PYTHON 显式指定 > 引擎前缀(显式/隐式发现)
    的 python > PATH;全部落空按显式失败抛错(§1.4),绝不静默派生。
    源码运行零行为变化(直接返回 sys.executable)。
    """
    if not getattr(sys, "frozen", False):
        return sys.executable
    explicit = os.environ.get("INSAR_PYTHON", "").strip()
    if explicit:
        if Path(explicit).exists():
            return explicit
        raise RuntimeError(f"INSAR_PYTHON 指向不存在的解释器:{explicit}")
    prefix = os.environ.get("INSAR_ENGINE_PREFIX", "").strip()
    if not prefix:
        try:  # 隐式 conda 前缀发现(与 probe 同源;函数内 import 防环)
            from insar_agent.runtime.probe import _implicit_engine_prefix
            prefix = str(_implicit_engine_prefix() or "")
        except Exception:
            prefix = ""
    if prefix:
        for cand in (Path(prefix) / "python.exe", Path(prefix) / "python",
                     Path(prefix) / "bin" / "python"):
            if cand.exists():
                return str(cand)
    found = shutil.which("python") or shutil.which("python3")
    if found:
        return found
    raise RuntimeError(
        "冻结包执行本地作业需要外部 Python 解释器:请设置 INSAR_PYTHON 指向"
        " python.exe,或配置 INSAR_ENGINE_PREFIX / 安装 Miniforge(见"
        " docs/DESKTOP-PARITY.md GAP-1)")


@dataclass(frozen=True)
class JobState:
    kind: str  # alive | finished | orphaned | unknown
    exit_code: int | None = None


@dataclass(frozen=True)
class CommandPlan:
    """一次外部命令的完整描述(由 engines 构建,executor 消费)。

    注入面纪律(REVIEW 2026-08-12 P2-8 不变量,勿破):shell_line 会被逐字
    写进 cmd.sh 交给 bash 执行 —— 只允许由引擎内部常量拼接;任何外部可控值
    (路径、用户参数)进入 shell_line 前必须经 shell_quote,数值参数必须先
    转型收窄。argv 通道无此约束(不经 shell 解释)。env 的键名须匹配
    [A-Z_][A-Z0-9_]*(WslJobBackend.prepare 强制断言),值由 prepare 统一
    shell_quote。
    """

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
    def read_new_lines(self, job_dir: Path, offset: int, *,
                       final: bool = False) -> tuple[list[str], int]: ...


def _atomic_write(path: Path, content: str) -> None:
    atomic_write_text(path, content)  # 统一原子写:异常清理 + 随机 tmp 名(core/fsio)


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
            # 关键教训:CREATE_NO_WINDOW 与 DETACHED_PROCESS 同用时会被系统忽略
            # (MSDN CreateProcess 文档)。venv 的 python.exe 是启动器,会再拉起
            # 真解释器;DETACHED 让启动器"无控制台",真解释器无可继承的控制台
            # 就会自己分配一个可见新窗 —— 之前"测试不断弹窗"的根因。
            # 改为只用 CREATE_NO_WINDOW:wrapper 拿到一个隐藏控制台,整条子孙链
            # (启动器→真解释器→作业子进程)继承同一个隐藏控制台,全程不可见;
            # 子进程存活性与控制台标志无关,父进程退出后 wrapper 照常存活(reattach 不受影响)。
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            CREATE_NO_WINDOW = 0x08000000
            kwargs["creationflags"] = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # SW_HIDE 双保险
            si.wShowWindow = 0  # SW_HIDE
            kwargs["startupinfo"] = si
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
                [wrapper_python(), str(wrapper_path), str(job_dir)],
                stdout=subprocess.DEVNULL, stderr=wrapper_err, stdin=subprocess.DEVNULL,
                **kwargs)
        finally:
            wrapper_err.close()  # 子进程持有继承的句柄,父进程侧关闭不影响

    def state(self, job_dir: Path) -> JobState:
        # TOCTOU 内聚防护(FOLLOWUPS 2026-08-12 #6):exists() 与 stat()/read_text()
        # 之间文件可能被外部删除(作业目录清理)或短暂独占(杀软/索引器零共享句柄,
        # Windows 上表现为 PermissionError)。语义统一为「瞬时不可读 → unknown,
        # 本轮不判定」:单次 unknown 不改变结局 —— 孤儿判定需连续两次命中,启动期
        # 由 startup_grace 兜底,僵死由双超时兜底;下一轮轮询自然重试。
        # 修复前该窗口直接抛异常,只有 stream 侧兜着;其他调用方(executor 认领、
        # admin 判活)会被判活探测本身打崩。
        try:
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
        except (FileNotFoundError, PermissionError):
            return JobState("unknown")
        if age > self.hb_stale:
            return JobState("orphaned")  # wrapper 死了但没写 rc:kill -9 / 断电
        return JobState("alive")

    def cancel(self, job_dir: Path) -> None:
        (job_dir / "job.cancel").touch()

    def read_new_lines(self, job_dir: Path, offset: int, *,
                       final: bool = False) -> tuple[list[str], int]:
        """按 offset 增量读完整行;半行留到下次(§4.4:只在读到换行符时才提交)。

        final=True 为终读模式(FOLLOWUPS 2026-08-12 #7):作业已 finished、
        日志不会再有写入,无换行的尾部余量必须一并交付(否则永久丢失 ——
        不少引擎最后一行诊断不带换行)。半行语义只对「还在写」的日志成立。
        """
        log = job_dir / "job.log"
        if not log.exists():
            return [], offset
        with open(log, "rb") as f:
            f.seek(offset)
            chunk = f.read()
        if not chunk:
            return [], offset
        if final:
            # splitlines 顺带兼容裸 \r 结尾的进度行;offset 前进到文件末尾
            return chunk.decode("utf-8", errors="replace").splitlines(), offset + len(chunk)
        last_nl = chunk.rfind(b"\n")
        if last_nl == -1:
            return [], offset  # 只有半行
        complete = chunk[: last_nl + 1]
        lines = complete.decode("utf-8", errors="replace").splitlines()
        return lines, offset + len(complete)
