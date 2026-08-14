"""子任务并行池:只读检索/探测类任务的限并发扇出(契约 §6,B8)。

服务对象是 search_data 这类可并行的只读子任务(本地目录盘点 / ASF 检索 / 网页搜索):

- 信号量限并发(默认 3);结果按入参键序返回;
- 单任务异常/超时绝不炸整批,如实计入 SubResult.error(类型名+消息,不带堆栈);
- timeout 是整批的墙钟预算(秒):到点时仍在跑的任务被真正取消(协程收到
  CancelledError),还在排队、从未启动的协程被 close() —— 不留孤儿协程;
- 执行类(io=heavy)任务不进此池:运行锁与 FIFO 队列语义不变(红线 §0.6)。

同步阻塞函数走 run_sync_limited(内部 asyncio.to_thread 包装)。注意 stdlib 限制:
线程里已开跑的同步函数无法被强行中断,超时只取消协程侧的等待,线程会自行跑完后由
线程池回收 —— 因此只适合放短阻塞的检索/探测调用,不放长任务。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Callable, Coroutine

from insar_agent.loop.events import tool_log


@dataclass
class SubResult:
    """单个子任务的结果账目(如实记录,不美化)。"""

    name: str
    ok: bool
    value: object | None
    error: str | None   # "类型名: 消息";池级超时统一以 "TimeoutError: " 开头
    elapsed_ms: int     # 从真正开始执行起计(不含排队等待);从未启动 = 0


def _ms_since(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def _publish(bus, tool_id: str, line: str, tone: str = "dim") -> None:
    """可选进度上报(tool.log 形状,复用 events.tool_log 工厂)。

    总线故障绝不影响子任务结果 —— 与 EventBus 的监听器错误隔离同一精神(absorb-E8)。
    """
    if bus is None:
        return
    try:
        bus.publish(tool_log(tool_id, line, tone))
    except Exception:
        pass


async def gather_limited(named_tasks: dict[str, Coroutine], *, limit: int = 3,
                         timeout: float | None = None, bus=None,
                         tool_id: str = "subtasks") -> dict[str, SubResult]:
    """限并发地并行执行一组命名协程,结果按入参键序返回。

    timeout(秒)是整批预算:每个任务的超时时钟都从批次启动对齐 —— 单任务拖垮
    与整批全数超时是同一机制的两种表现,均如实标注进各自的 SubResult.error。
    bus 可选(duck type:publish(dict)),传入时以 tool.log 形状发布进度。
    """
    if limit < 1:
        for coro in named_tasks.values():
            coro.close()  # 参数错误也不留孤儿协程
        raise ValueError(f"limit 必须 >= 1,得到 {limit}")
    if not named_tasks:
        return {}

    sem = asyncio.Semaphore(limit)
    # 只有真正开始执行(拿到并发额度)的任务才登记 —— 超时标注据此区分「跑了一半
    # 被取消」与「排队中从未启动」,elapsed_ms 也据此如实计算
    started_at: dict[str, float] = {}

    async def _run_one(name: str, coro: Coroutine) -> SubResult:
        try:
            async with sem:
                t0 = time.perf_counter()
                started_at[name] = t0
                _publish(bus, tool_id, f"{name} 开始")
                try:
                    value = await coro
                # CancelledError 是 BaseException,不会被这里吞掉:整批超时的取消
                # 原样上抛,交由 _guard 如实标注;其余 BaseException(如中断)也放行
                except Exception as exc:
                    err = f"{type(exc).__name__}: {exc}"
                    _publish(bus, tool_id, f"{name} 失败:{err}", tone="warn")
                    return SubResult(name, False, None, err, _ms_since(t0))
                elapsed = _ms_since(t0)
                _publish(bus, tool_id, f"{name} 完成({elapsed}ms)")
                return SubResult(name, True, value, None, elapsed)
        except asyncio.CancelledError:
            if name not in started_at:
                coro.close()  # 从未 await 过的排队协程:关闭,防孤儿协程警告
            raise

    async def _guard(name: str, coro: Coroutine) -> SubResult:
        try:
            return await asyncio.wait_for(_run_one(name, coro), timeout)
        except asyncio.TimeoutError:
            # wait_for 已取消内层任务并等其收尾完成 —— 此刻不存在仍在跑的协程
            t0 = started_at.get(name)
            if t0 is None:
                err = f"TimeoutError: 整批超时({timeout}s),任务未开始"
                elapsed = 0
            else:
                err = f"TimeoutError: 超时被取消({timeout}s)"
                elapsed = _ms_since(t0)
            _publish(bus, tool_id, f"{name} 超时", tone="warn")
            return SubResult(name, False, None, err, elapsed)

    results = await asyncio.gather(*(_guard(n, c) for n, c in named_tasks.items()))
    return {r.name: r for r in results}


async def run_sync_limited(named_calls: dict[str, Callable], *, limit: int = 3,
                           timeout: float | None = None, bus=None,
                           tool_id: str = "subtasks") -> dict[str, SubResult]:
    """同步阻塞函数的便捷入口:asyncio.to_thread 包成协程后走 gather_limited。

    named_calls 的值是零参可调用(带参先用 functools.partial 绑好)。超时语义见
    模块 docstring:只能取消协程侧,线程内已开跑的函数无法中断,会自行跑完。
    """
    named = {name: asyncio.to_thread(fn) for name, fn in named_calls.items()}
    return await gather_limited(named, limit=limit, timeout=timeout,
                                bus=bus, tool_id=tool_id)
