"""turn() 流式线程桥验收(0814B 契约 §1.3,W2):say.delta/say.abort 生命周期。

覆盖:
  ① delta 序列 → 终帧 say 全文一致;delta 只进回合流,不上 EventBus(订阅者对账);
  ② 外发过 delta 后失败(截断/供应商)→ say.abort(reason 闭集)+ 既有降级 note,
     半截回复不落聊天历史、不驱动动作;
  ③ 未外发即失败 → 无 say.abort,事件序列与改造前逐字节同轨(note + 规则路径);
  ④ facade.converse 无 on_delta 形参(W1 未落地形态)→ 防御闸门退回零流式同步语义;
  ⑤ 回合生成器在 delta 中途被 close(客户端断连)→ 线程任务不被 GC 掐死、正常收尾;
  ⑥ Brain(None) 规则路径零 delta 零回归。
另:排空合帧(消费慢自动合并)、工厂形状、防御闸门单元断言。

假 Brain 桩带可编程 on_delta 行为(在 driver 的线程桥线程里执行,完全模拟 W1
facade 的外发时序);密封零网络:非 converse 职责经 DegradedProvider 一律降级
(tests/test_converse.py 的打桩哲学),HOME/数据环境变量全部隔离。
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from conftest import TIME_FACTOR
from insar_agent.brain.facade import Brain, ConverseResult
from insar_agent.brain.provider import BrainTruncated, BrainUnavailable, LLMProvider, LLMRoute
from insar_agent.loop import events as ev
from insar_agent.loop.driver import Driver, _supports_on_delta
from insar_agent.runtime.probe import ProbeResult


@pytest.fixture(autouse=True)
def sealed_home(tmp_path, monkeypatch):
    """密封:HOME 指向空目录,清掉数据源/数据目录环境变量(状态摘要会读)。"""
    monkeypatch.setenv("INSAR_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("INSAR_DATA_DIR", raising=False)
    monkeypatch.delenv("INSAR_HYP3_SOURCE", raising=False)


def empty_probe():
    return ProbeResult(engines={k: None for k in ("isce2", "mintpy", "snaphu", "gdal",
                                                  "snap", "pystamps", "pyaps")},
                       credentials={"earthdata": False, "cds": False, "gacos": False},
                       disk_free_gb=100.0, cpu_count=8)


def make_driver(store, workspace, brain) -> Driver:
    return Driver(store, workspace=workspace, probe=empty_probe(), poll=0.05,
                  allow_simulated=True, brain=brain)


class DegradedProvider(LLMProvider):
    """非 converse 职责(intent/select/triage)一律降级,绝不发网络请求。"""

    def __init__(self):
        super().__init__(routes=[LLMRoute("http://fake", "", "fake-model")])

    def complete_json(self, **kwargs):
        raise BrainUnavailable("stub:非 converse 职责走降级")

    def chat(self, *args, **kwargs):
        raise BrainUnavailable("stub:非 converse 职责走降级")


class StreamBrain(Brain):
    """converse 可编程的假 Brain:behavior(on_delta) 在线程桥线程里执行 ——
    推增量 → 返回 ConverseResult 或抛异常,模拟 W1 facade 的流式外发时序。"""

    def __init__(self, behavior):
        super().__init__(DegradedProvider())
        self._behavior = behavior

    def converse(self, text, *, history=None, state_summary="", registry=None,
                 on_delta=None):
        return self._behavior(on_delta)


class LegacyBrain(Brain):
    """W1 未落地形态:converse 签名没有 on_delta(防御闸门必须退回零流式)。
    误传关键字会直接 TypeError —— 本替身同时兼任「传了必炸」的哨兵。"""

    def __init__(self, outcome):
        super().__init__(DegradedProvider())
        self._outcome = outcome

    def converse(self, text, *, history=None, state_summary="", registry=None):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def scripted(chunks, result=None, error=None):
    """顺序推送 chunks 后返回 result 或抛 error 的 behavior。"""

    def behavior(on_delta):
        for c in chunks:
            on_delta(c)
        if error is not None:
            raise error
        return result

    return behavior


def run_turn(driver, text, session="s1"):
    """驱动一个 turn 回合,返回 (回合事件, 总线事件) —— 订阅者对账的数据源。"""

    async def _run():
        q = driver.bus.subscribe()
        try:
            events = [e async for e in driver.turn(session, text)]
        finally:
            driver.bus.unsubscribe(q)
        bus = []
        while not q.empty():
            bus.append(q.get_nowait())
        return events, bus

    return asyncio.run(_run())


# ---------------- 工厂形状与防御闸门 ----------------

def test_stream_frame_factory_shapes():
    assert ev.say_delta("增量") == {"t": "say.delta", "text": "增量"}
    for reason in ("truncated", "unavailable", "stopped"):  # reason 闭集
        assert ev.say_abort(reason) == {"t": "say.abort", "reason": reason}


def test_supports_on_delta_gate():
    assert _supports_on_delta(lambda text, *, on_delta=None: None)
    assert not _supports_on_delta(lambda text: None)
    # **kwargs 不算支持:替身收下 on_delta 也不会外发,按不支持处置最诚实
    assert not _supports_on_delta(lambda *args, **kwargs: None)
    assert not _supports_on_delta(min)  # 签名探不出(C 实现)→ 按不支持


# ---------------- ① delta 序列 → 终帧 say 一致;delta 不上总线 ----------------

def test_delta_stream_then_final_say_and_bus_reconciliation(store, workspace):
    reply = "你好!想处理哪批数据?"
    brain = StreamBrain(scripted(["你好!", "想处理", "哪批数据?"],
                                 result=ConverseResult(reply=reply)))
    driver = make_driver(store, workspace, brain)
    events, bus = run_turn(driver, "你好")

    kinds = [e["t"] for e in events]
    deltas = [e for e in events if e["t"] == "say.delta"]
    assert deltas, "on_delta 已支持,必须外发 say.delta"
    # 增量拼接 = 终帧全文(合帧允许改变帧数,绝不丢字/重放)
    assert "".join(d["text"] for d in deltas) == reply
    final = next(e for e in events if e["t"] == "say")
    assert final["parts"] == [reply]  # 终帧定稿:形状与既有 say 完全一致
    assert kinds == ["say.delta"] * len(deltas) + ["say"]
    # 通道例外:delta 只进回合流;终帧 say 照旧 _emit 双通道
    assert not any(e["t"] in ("say.delta", "say.abort") for e in bus)
    assert final in bus
    # 聊天历史只落定稿(与改造前逐字节一致)
    hist = store.chat_history("s1")
    assert [m["role"] for m in hist] == ["user", "agent"]
    assert hist[-1]["content"] == reply


# ---------------- ② 外发过 delta 后失败 → say.abort + 降级,三消费点干净 ----------------

@pytest.mark.parametrize("exc_cls,reason", [
    (BrainTruncated, "truncated"),
    (BrainUnavailable, "unavailable"),
])
def test_failure_after_deltas_aborts_then_degrades(store, workspace, exc_cls, reason):
    brain = StreamBrain(scripted(["分析", "中……"], error=exc_cls("boom")))
    driver = make_driver(store, workspace, brain)
    events, bus = run_turn(driver, "你好")

    kinds = [e["t"] for e in events]
    n = kinds.count("say.delta")
    # 序列:delta × N → say.abort 标废 → 既有降级 note → 规则路径(表单)
    assert n >= 1 and kinds == ["say.delta"] * n + ["say.abort", "note", "ask"]
    assert events[n] == {"t": "say.abort", "reason": reason}
    assert "LLM 暂不可用" in events[n + 1]["text"]
    # 半截回复不落聊天历史(只有用户消息)、不驱动动作、不碰 run/plan 状态
    assert [m["role"] for m in store.chat_history("s1")] == ["user"]
    assert store.due_actions("steer") == []
    assert store.latest_run("s1") is None
    # abort 与 delta 都不上总线;降级 note 照旧双通道
    assert not any(e["t"] in ("say.delta", "say.abort") for e in bus)
    assert any(e["t"] == "note" and "LLM 暂不可用" in e["text"] for e in bus)


# ---------------- ③ 未外发即失败 → 无 say.abort,与改造前同轨 ----------------

def test_failure_before_first_delta_has_no_abort(store, workspace):
    brain = StreamBrain(scripted([], error=BrainUnavailable("建流失败")))
    driver = make_driver(store, workspace, brain)
    events, bus = run_turn(driver, "你好")
    # 零外发无废可标:事件序列与改造前逐字节同轨(降级 note + 规则路径表单)
    assert [e["t"] for e in events] == ["note", "ask"]
    assert "LLM 暂不可用" in events[0]["text"]
    assert not any(e["t"] in ("say.delta", "say.abort") for e in events + bus)
    assert [m["role"] for m in store.chat_history("s1")] == ["user"]


# ---------------- ④ facade 无 on_delta 形参 → 防御闸门退回零流式 ----------------

def test_legacy_converse_without_on_delta_is_pure_sync(store, workspace):
    reply = "你好!需要什么?"
    driver = make_driver(store, workspace, LegacyBrain(ConverseResult(reply=reply)))
    events, bus = run_turn(driver, "你好")
    assert [e["t"] for e in events] == ["say"]  # 零 delta,事件序列与改造前一致
    assert events[0]["parts"] == [reply]
    assert driver._llm_tasks == set()  # 零流式任务遗留
    assert [m["role"] for m in store.chat_history("s1")] == ["user", "agent"]
    assert not any(e["t"] in ("say.delta", "say.abort") for e in bus)


def test_legacy_converse_failure_degrades_like_before(store, workspace):
    driver = make_driver(store, workspace, LegacyBrain(BrainUnavailable("boom")))
    events, _bus = run_turn(driver, "你好")
    assert [e["t"] for e in events] == ["note", "ask"]
    assert "LLM 暂不可用" in events[0]["text"]


# ---------------- ⑤ 生成器在 delta 中途被 close(客户端断连) ----------------

@pytest.mark.timing  # gate.wait 判定窗依赖真实时钟
@pytest.mark.parametrize("fail_after_close", [False, True])
def test_generator_close_mid_delta_thread_survives(store, workspace, fail_after_close):
    """断连后线程任务不被 GC 掐死:强引用在位、正常收尾、无异常泄漏。"""
    gate = threading.Event()

    def behavior(on_delta):
        on_delta("第一段")
        assert gate.wait(timeout=10 * TIME_FACTOR), "测试应在断连后放行线程"
        on_delta("第二段(断连后)")
        if fail_after_close:
            raise BrainUnavailable("断连后失败:必须被收割,不留告警")
        return ConverseResult(reply="第一段第二段(断连后)")

    driver = make_driver(store, workspace, StreamBrain(behavior))

    async def scenario():
        agen = driver.turn("s1", "你好")
        first = await agen.__anext__()
        assert first == {"t": "say.delta", "text": "第一段"}
        assert len(driver._llm_tasks) == 1  # 强引用在位(防 GC 掐断)
        task = next(iter(driver._llm_tasks))
        await agen.aclose()      # 模拟客户端断连:回合生成器在 delta 中途被关闭
        assert not task.done()   # 线程仍被 gate 挡着:任务没被取消/掐死
        gate.set()
        if fail_after_close:
            with pytest.raises(BrainUnavailable):
                await task       # 异常仍可取回;收割回调兜底防未取回告警
        else:
            outcome = await task  # 正常跑完:结果完好
            assert outcome.reply == "第一段第二段(断连后)"
        await asyncio.sleep(0)   # 让 done 回调(收割 + 哨兵)执行完
        assert driver._llm_tasks == set()  # 强引用已释放,不泄漏
        assert not task.cancelled()

    asyncio.run(scenario())
    # 断连的半截回复不落聊天历史(回合没走到定稿/降级)
    assert [m["role"] for m in store.chat_history("s1")] == ["user"]


# ---------------- 排空合帧:消费慢时积压增量合成一帧 ----------------

@pytest.mark.timing  # gate.wait 判定窗依赖真实时钟
def test_slow_consumer_merges_backlogged_deltas(store, workspace):
    gate = threading.Event()

    def behavior(on_delta):
        on_delta("c1")
        assert gate.wait(timeout=10 * TIME_FACTOR)
        on_delta("c2")
        on_delta("c3")
        return ConverseResult(reply="c1c2c3")

    driver = make_driver(store, workspace, StreamBrain(behavior))

    async def scenario():
        agen = driver.turn("s1", "你好")
        assert await agen.__anext__() == {"t": "say.delta", "text": "c1"}
        # 生成器挂起在 yield(消费慢):放行线程,让 c2/c3 与哨兵先积压进队列
        task = next(iter(driver._llm_tasks))
        gate.set()
        await task
        assert await agen.__anext__() == {"t": "say.delta", "text": "c2c3"}  # 合帧
        rest = [e async for e in agen]
        assert [e["t"] for e in rest] == ["say"]
        assert rest[0]["parts"] == ["c1c2c3"]  # 合帧不丢字:终帧仍是全文

    asyncio.run(scenario())


# ---------------- ⑥ Brain(None) 规则路径零 delta 零回归 ----------------

def test_brain_none_rules_path_zero_delta_zero_regression(store, workspace):
    driver = make_driver(store, workspace, Brain(None))
    events, bus = run_turn(driver, "随便帮我搞一下")
    assert [e["t"] for e in events] == ["ask"]  # 无 converse say、无降级 note

    events2, bus2 = run_turn(driver, "Ridgecrest 地震同震形变分析", session="s2")
    kinds = [e["t"] for e in events2]
    assert kinds[0] == "thinking"  # 首事件仍是 thinking,前面没有多出任何事件
    assert "plan" in kinds and "candidates" in kinds
    assert not any(e["t"] in ("say.delta", "say.abort")
                   for e in events + events2 + bus + bus2)
    assert driver._llm_tasks == set()  # 规则路径从不建流式任务
