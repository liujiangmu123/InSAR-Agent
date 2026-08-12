"""日志流 + 双超时 + 协作式取消(AGENT-DESIGN §4.4)。

规避 InSAR_Agent 的致命缺陷(tools/mintpy.py:219:取消检查嵌在阻塞读循环里,
子进程静默时永远收不到取消):这里读文件不读管道,轮询必定每 poll 秒醒一次,
取消/超时检查不依赖子进程产生输出。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from insar_agent.runtime.jobs import JobBackend, JobState

LineSink = Callable[[str], None] | Callable[[str], Awaitable[None]]


class CancelToken:
    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


@dataclass(frozen=True)
class JobOutcome:
    kind: str  # finished | cancelled | idle_timeout | total_timeout | orphaned
    exit_code: int | None
    offset: int


async def _emit(sink: LineSink | None, line: str) -> None:
    if sink is None:
        return
    result = sink(line)
    if asyncio.iscoroutine(result):
        await result


async def follow_job(
    backend: JobBackend,
    job_dir: Path,
    *,
    offset: int = 0,
    idle_timeout: float = 1800.0,
    total_timeout: float = 21600.0,
    token: CancelToken | None = None,
    on_line: LineSink | None = None,
    on_offset: Callable[[int], None] | None = None,
    poll: float = 0.5,
    cancel_grace: float = 15.0,
    startup_grace: float = 30.0,  # 容忍杀毒软件对新进程/DLL 的实时扫描延迟
) -> JobOutcome:
    """跟随作业直到结束/取消/超时/孤儿。

    - idle_timeout:无输出超时;total_timeout:总时长超时(都按 capability 声明,§1.1)
    - offset 持久化交给 on_offset(供宿主重启后 reattach 续读,§4.3)
    - 返回时日志已 drain 到最后一行
    """
    started = time.monotonic()
    last_output = started
    orphan_strikes = 0  # 孤儿判定需连续两次命中:消除心跳文件的瞬时竞态

    def probe_state() -> JobState | None:
        """判活探测;瞬时 IO 失败返回 None,本轮跳过状态分支,由双超时兜底。

        触发场景(tests/test_chaos_runtime.py):Windows 杀软/索引器短暂独占
        job.hb/job.rc,或作业目录被外部清理 —— 修复前一次 OSError 会让长时间
        运行的 follow 直接崩溃,offset 进度与取消能力一起丢失。
        """
        try:
            return backend.state(job_dir)
        except OSError:
            return None

    async def drain() -> None:
        nonlocal offset, last_output
        try:
            lines, new_offset = backend.read_new_lines(job_dir, offset)
        except OSError:
            # 日志被外部独占/删除竞态:本轮视为无新输出,下一轮重试;
            # 持续不可读时 idle_timeout 兜底,follow 不因瞬时 IO 失败崩溃
            return
        if lines:
            last_output = time.monotonic()
            for line in lines:
                await _emit(on_line, line)
        if new_offset != offset:
            offset = new_offset
            if on_offset:
                on_offset(offset)

    async def cancel_and_wait(kind: str) -> JobOutcome:
        try:
            backend.cancel(job_dir)
        except OSError:
            pass  # 作业目录已被外部清掉:照走宽限观察,超时后按 orphaned 交上层处置
        deadline = time.monotonic() + cancel_grace
        while time.monotonic() < deadline:
            await drain()
            st = probe_state()
            if st is not None and st.kind == "finished":
                await drain()
                return JobOutcome(kind, st.exit_code, offset)
            await asyncio.sleep(min(poll, 0.2))
        return JobOutcome(kind, None, offset)  # wrapper 也没响应:交给上层按 orphaned 处置

    while True:
        await drain()
        now = time.monotonic()

        if token is not None and token.cancelled:
            return await cancel_and_wait("cancelled")

        st = probe_state()
        if st is not None:
            if st.kind == "finished":
                await drain()
                return JobOutcome("finished", st.exit_code, offset)
            if st.kind == "orphaned":
                orphan_strikes += 1
                if orphan_strikes >= 2:
                    await drain()
                    return JobOutcome("orphaned", None, offset)
                await asyncio.sleep(min(poll, 0.3))
                continue
            orphan_strikes = 0
            if st.kind == "unknown" and now - started > startup_grace:
                # wrapper 迟迟没起来:按 orphaned 处置(保留现场,可重试)
                return JobOutcome("orphaned", None, offset)

        if now - last_output > idle_timeout:
            return await cancel_and_wait("idle_timeout")
        if now - started > total_timeout:
            return await cancel_and_wait("total_timeout")

        await asyncio.sleep(poll)
