"""B8 验收(契约 §6 子任务并行池):限并发扇出、异常/超时隔离、键序、无孤儿协程。

async 用例沿用项目既有模式(tests/test_loop.py):同步测试函数 + asyncio.run 包场景;
并发峰值用计数器+事件会合判定,不依赖真实时钟;仅超时用例依赖真实时钟,判定窗乘
conftest.TIME_FACTOR 并打 @pytest.mark.timing。
"""

from __future__ import annotations

import asyncio
import inspect
import threading

import pytest

from conftest import TIME_FACTOR
from insar_agent.loop.events import EventBus
from insar_agent.loop.subtasks import gather_limited, run_sync_limited

# ---------------- ① 正常并行:结果、值、键序 ----------------

def test_results_follow_input_key_order_not_completion_order():
    """结果字典按入参键序返回,与完成先后无关;value/elapsed 如实。"""
    async def scenario():
        ev = asyncio.Event()

        async def slow():
            await ev.wait()   # 等 fast 放行:强制完成顺序 = fast 先、slow 后
            return {"hits": 3}

        async def fast():
            ev.set()
            return "快"

        async def none_value():
            return None       # ok=True 且 value=None 是合法结果,须原样保留

        named = {"z_slow": slow(), "a_fast": fast(), "n_none": none_value()}
        return await gather_limited(named, limit=3)

    res = asyncio.run(scenario())
    assert list(res.keys()) == ["z_slow", "a_fast", "n_none"]  # 键序=入参序
    assert res["z_slow"].ok and res["z_slow"].value == {"hits": 3}
    assert res["a_fast"].ok and res["a_fast"].value == "快"
    assert res["n_none"].ok and res["n_none"].value is None
    assert all(r.error is None for r in res.values())
    assert all(r.elapsed_ms >= 0 for r in res.values())
    assert res["z_slow"].name == "z_slow"


# ---------------- ② limit 生效:并发峰值恰为上限 ----------------

def test_limit_caps_peak_concurrency():
    """计数器判定:峰值恰等于 limit(确有并行、且从未超限),不用真实时钟。"""
    async def scenario():
        current = 0
        peak = 0
        both_in_flight = asyncio.Event()

        async def worker():
            nonlocal current, peak
            current += 1
            peak = max(peak, current)
            if current >= 2:
                both_in_flight.set()
            await both_in_flight.wait()   # 前两名互相等到齐:证明确实并行过
            current -= 1                  # 在释放并发额度之前退账,保证计数如实

        named = {f"t{i}": worker() for i in range(5)}
        res = await gather_limited(named, limit=2)
        return peak, res

    # 30s 仅是挂死保险(信号量若失效则会合永不达成),不属于时序判定窗
    peak, res = asyncio.run(asyncio.wait_for(scenario(), timeout=30 * TIME_FACTOR))
    assert peak == 2                       # 恰为上限:并行发生了,且没超
    assert all(r.ok for r in res.values())
    assert list(res.keys()) == [f"t{i}" for i in range(5)]


# ---------------- ③ 单任务异常不炸整批 ----------------

def test_single_exception_isolated_with_type_and_message():
    async def scenario():
        async def boom():
            raise ValueError("bad input")

        async def okay():
            return 42

        return await gather_limited({"boom": boom(), "okay": okay()}, limit=2)

    res = asyncio.run(scenario())
    assert res["boom"].ok is False
    assert res["boom"].error == "ValueError: bad input"   # 类型名+消息
    assert "\n" not in res["boom"].error                  # 不带堆栈
    assert res["boom"].value is None
    assert res["okay"].ok and res["okay"].value == 42 and res["okay"].error is None


# ---------------- ④ 单任务超时:真正取消 + 如实标注 ----------------

@pytest.mark.timing  # 判定窗 = 整批 timeout(区分「秒回」与「挂死」),乘 TIME_FACTOR
def test_single_task_timeout_truly_cancelled_and_labeled():
    deadline = 0.2 * TIME_FACTOR

    async def scenario():
        got_cancel = asyncio.Event()

        async def hang():
            try:
                await asyncio.sleep(3600)   # 挂死,等池在 deadline 处取消(不会真睡)
            except asyncio.CancelledError:
                got_cancel.set()            # 留下「确实收到取消」的证据
                raise

        async def quick():
            return "ok"

        hang_coro = hang()
        res = await gather_limited({"hang": hang_coro, "quick": quick()},
                                   limit=2, timeout=deadline)
        return res, got_cancel.is_set(), inspect.getcoroutinestate(hang_coro)

    res, was_cancelled, hang_state = asyncio.run(scenario())
    assert was_cancelled and hang_state == inspect.CORO_CLOSED   # 真取消,无孤儿
    assert res["hang"].ok is False
    assert res["hang"].error.startswith("TimeoutError")          # 如实标注超时
    assert res["hang"].elapsed_ms >= int(deadline * 1000 / 2)    # 确实跑到了 deadline 附近
    assert res["quick"].ok and res["quick"].value == "ok"        # 同批任务不受连累
    assert list(res.keys()) == ["hang", "quick"]


# ---------------- ⑤ 整批超时:全部如实标注,排队者不留孤儿 ----------------

@pytest.mark.timing
def test_batch_timeout_marks_running_and_queued_tasks():
    deadline = 0.15 * TIME_FACTOR

    async def scenario():
        async def hang():
            await asyncio.sleep(3600)

        c1, c2, c3 = hang(), hang(), hang()
        # limit=2:c3 始终排队拿不到额度,deadline 时从未启动
        res = await gather_limited({"h1": c1, "h2": c2, "h3": c3},
                                   limit=2, timeout=deadline)
        states = [inspect.getcoroutinestate(c) for c in (c1, c2, c3)]
        return res, states

    res, states = asyncio.run(scenario())
    assert list(res.keys()) == ["h1", "h2", "h3"]
    assert all(not r.ok for r in res.values())
    assert all(r.error.startswith("TimeoutError") for r in res.values())
    # 跑了一半被取消 vs 从未启动:标注与 elapsed 都要如实区分
    assert "超时被取消" in res["h1"].error and "超时被取消" in res["h2"].error
    assert "未开始" in res["h3"].error and res["h3"].elapsed_ms == 0
    assert res["h1"].elapsed_ms >= int(deadline * 1000 / 2)
    # 三个协程全部善终:两个被取消、一个被 close(),都不留孤儿
    assert states == [inspect.CORO_CLOSED] * 3


# ---------------- ⑥ run_sync_limited:to_thread 包装同步函数 ----------------

def test_run_sync_limited_wraps_blocking_calls_in_threads():
    main_ident = threading.get_ident()
    seen: dict[str, int] = {}

    def work_ok():
        seen["a"] = threading.get_ident()
        return "A"

    def work_bad():
        raise RuntimeError("sync boom")

    res = asyncio.run(run_sync_limited({"a": work_ok, "b": work_bad}, limit=2))
    assert list(res.keys()) == ["a", "b"]
    assert res["a"].ok and res["a"].value == "A"
    assert seen["a"] != main_ident                        # 确在工作线程执行
    assert res["b"].ok is False
    assert res["b"].error == "RuntimeError: sync boom"    # 同步异常同样进账目


# ---------------- ⑦ 空任务表 ----------------

def test_empty_task_table_returns_empty_dict():
    assert asyncio.run(gather_limited({})) == {}
    assert asyncio.run(run_sync_limited({})) == {}


# ---------------- 附加:bus 进度上报(tool.log 形状)与总线故障隔离 ----------------

def test_bus_publishes_tool_log_progress_and_survives_broken_bus():
    async def scenario():
        bus = EventBus()
        q = bus.subscribe()

        async def okay():
            return 1

        async def boom():
            raise RuntimeError("x")

        await gather_limited({"a": okay(), "b": boom()}, limit=2,
                             bus=bus, tool_id="fanout-7")
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        return events

    events = asyncio.run(scenario())
    assert events, "开了 bus 就应有进度事件"
    # 形状与 loop/events.py 的 tool_log 工厂逐字段一致
    assert all(set(e) == {"t", "id", "line", "tone"} for e in events)
    assert all(e["t"] == "tool.log" and e["id"] == "fanout-7" for e in events)
    assert any("a 开始" in e["line"] for e in events)
    assert any("a 完成" in e["line"] for e in events)
    assert any("b 失败" in e["line"] and e["tone"] == "warn" for e in events)

    # 总线故障不影响结果(与 EventBus 监听器隔离同一精神)
    class BrokenBus:
        def publish(self, event):
            raise RuntimeError("bus down")

    async def scenario2():
        async def okay():
            return 7

        return await gather_limited({"a": okay()}, limit=1, bus=BrokenBus())

    res = asyncio.run(scenario2())
    assert res["a"].ok and res["a"].value == 7


# ---------------- 附加:limit 参数护栏(防信号量死锁),且不留孤儿 ----------------

def test_limit_below_one_raises_and_closes_coroutines():
    async def scenario():
        async def never_runs():
            return 1

        coro = never_runs()
        with pytest.raises(ValueError):
            await gather_limited({"a": coro}, limit=0)
        return inspect.getcoroutinestate(coro)

    assert asyncio.run(scenario()) == inspect.CORO_CLOSED
