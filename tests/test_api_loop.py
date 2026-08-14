"""B5 验收:/api/converse 端点协议 + skills 挂载守护 + llm.json 热替换(LOOP-CONTRACT §7)。

driver.converse_loop 由 B1 并行开发 —— 本文件一律把它 monkeypatch 成 B1 落地
签名(session_id 先行,turn 同形;契约 §4 的 converse_loop(text) 是省写)的
假异步生成器,只验端点协议:开流前校验(400)/ NDJSON 逐周期透传 /
生成器异常转 note 事件(绝不裸断连)/ abort 控制位可被循环观察 /
无 run 会话 abort 置会话级取消(202,循环周期边界消费)/
路由指纹变化时 driver.brain 原地替换(Driver 与 EventBus 绝不重建)。
密封口径与 tests/test_api.py 一致:空 probe + 清空 INSAR_LLM_* 环境变量。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

import insar_agent.api.app as app_module
from conftest import TIME_FACTOR
from insar_agent.api.app import create_app
from insar_agent.loop.driver import Driver


@pytest.fixture(autouse=True)
def sealed_env(monkeypatch):
    """密封:probe 一律空引擎(同 tests/test_api.py);LLM 环境变量路由清空,
    路由指纹只由测试自己写的 llm.json 决定,不受宿主环境影响。"""
    from insar_agent.runtime.probe import ProbeResult

    def empty_probe(*args, **kwargs):
        return ProbeResult(
            engines={k: None for k in ("isce2", "mintpy", "snaphu", "gdal", "snap",
                                       "pystamps", "pyaps")},
            credentials={"earthdata": False, "cds": False, "gacos": False},
            disk_free_gb=100.0, cpu_count=8)

    monkeypatch.setattr("insar_agent.loop.driver.probe_environment", empty_probe)
    for var in ("INSAR_LLM_BASE_URL", "INSAR_LLM_MODEL", "INSAR_LLM_API_KEY",
                "INSAR_LLM_FALLBACK_BASE_URL", "INSAR_LLM_FALLBACK_MODEL",
                "INSAR_LLM_FALLBACK_API_KEY"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture()
def client(tmp_path):
    with TestClient(create_app(home=tmp_path / "home")) as c:
        yield c


def install_fake_loop(monkeypatch, calls: list, *, fail_at: int | None = None,
                      cycles: int = 2) -> None:
    """把 B1 实际签名的假异步生成器挂上 Driver.converse_loop。

    签名对齐 B1 落地形状(契约 §4 的 converse_loop(text) 是省写):与 turn
    同形,首参 session_id(driver.py 签名注记)—— 桩按真实签名收参,端点的
    调用形状(session 位置参数 + text 关键字)由 calls 账本逐项对账。
    raising=False:B1 并行开发期属性尚不存在也能挂;
    fail_at=n 表示第 n 周期抛异常(验证 ndjson 泵把异常转 note 事件)。
    """

    async def converse_loop(self, session_id, text, *, max_cycles=6):
        calls.append({"session": session_id, "text": text, "max_cycles": max_cycles})
        for i in range(1, cycles + 1):
            if fail_at is not None and i >= fail_at:
                raise RuntimeError("打桩:循环内部爆炸")
            yield {"t": "agent.cycle", "n": i, "max": max_cycles, "action": "status"}
        yield {"t": "say", "text": f"完成:{text}"}

    monkeypatch.setattr(Driver, "converse_loop", converse_loop, raising=False)


def _stream_events(client: TestClient, url: str, body: dict) -> list[dict]:
    """NDJSON 流逐行解析(同 tests/test_api.py 助手,多守一个媒体类型)。"""
    events = []
    with client.stream("POST", url, json=body) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/x-ndjson")
        for line in resp.iter_lines():
            if line.strip():
                events.append(json.loads(line))
    return events


# ---------------- ① 打桩后事件透传(NDJSON 逐行可解析) ----------------

def test_converse_events_passthrough_ndjson(client, monkeypatch):
    calls: list = []
    install_fake_loop(monkeypatch, calls)
    client.post("/api/sessions", json={"id": "demo"})
    events = _stream_events(client, "/api/converse", {"session": "demo", "text": "查询数据"})
    cyc = [e for e in events if e["t"] == "agent.cycle"]
    assert [c["n"] for c in cyc] == [1, 2]           # 逐周期流出,n 递增
    assert {c["action"] for c in cyc} == {"status"}  # 动作字段原样透传
    assert all(c["max"] == 24 for c in cyc)          # 缺省 max_cycles = 24
    assert events[-1] == {"t": "say", "text": "完成:查询数据"}
    # session/text 各就其位:B1 签名 session_id 先行,端点传错位会在此显形
    assert calls == [{"session": "demo", "text": "查询数据", "max_cycles": 24}]


def test_converse_max_cycles_default_sources(client, monkeypatch):
    """缺省值层级:显式 body > llm_config.agent_loop_settings(B10)> 契约默认 24。"""
    calls: list = []
    install_fake_loop(monkeypatch, calls)
    client.post("/api/sessions", json={"id": "demo"})

    _stream_events(client, "/api/converse",
                   {"session": "demo", "text": "x", "max_cycles": 3})
    assert calls[-1]["max_cycles"] == 3  # 显式值直达打桩生成器

    # B10 并行开发中:函数可能尚不存在,raising=False 直接种上契约形状
    from insar_agent.brain import llm_config
    monkeypatch.setattr(llm_config, "agent_loop_settings",
                        lambda home: {"enabled": True, "max_cycles": 4}, raising=False)
    _stream_events(client, "/api/converse", {"session": "demo", "text": "x"})
    assert calls[-1]["max_cycles"] == 4

    # 配置通道给出越界值:缺省侧自我复核回 24,绝不让端点反过来 400 拒绝缺省请求
    monkeypatch.setattr(llm_config, "agent_loop_settings",
                        lambda home: {"max_cycles": 99}, raising=False)
    _stream_events(client, "/api/converse", {"session": "demo", "text": "x"})
    assert calls[-1]["max_cycles"] == 24

    # 函数缺失(惰性 import 的 ImportError/AttributeError 路径)→ 契约默认 24
    monkeypatch.delattr(llm_config, "agent_loop_settings", raising=False)
    _stream_events(client, "/api/converse", {"session": "demo", "text": "x"})
    assert calls[-1]["max_cycles"] == 24


def test_converse_agent_loop_disabled_delegates_to_turn(client, monkeypatch):
    """开关守护(P1-2):agent_loop=False 时 /api/converse 退化为单步 turn 语义
    (面板承诺「关闭后回合退化单步问答」)—— 无 agent.cycle 事件,
    converse_loop 从未被调用,turn 收到原样的 session/text。"""
    calls: list = []
    install_fake_loop(monkeypatch, calls)  # 若被调用会吐 agent.cycle,在此显形
    turns: list = []

    async def fake_turn(self, session_id, text):
        turns.append({"session": session_id, "text": text})
        yield {"t": "say", "parts": [f"单步:{text}"]}

    monkeypatch.setattr(Driver, "turn", fake_turn)
    from insar_agent.brain import llm_config
    monkeypatch.setattr(llm_config, "agent_loop_settings",
                        lambda home: {"enabled": False, "max_cycles": 6}, raising=False)
    client.post("/api/sessions", json={"id": "demo"})
    events = _stream_events(client, "/api/converse", {"session": "demo", "text": "推进"})
    assert not any(e["t"] == "agent.cycle" for e in events)
    assert calls == []  # 循环生成器从未被触达
    assert turns == [{"session": "demo", "text": "推进"}]
    assert events == [{"t": "say", "parts": ["单步:推进"]}]

    # 开关恢复 True:同一请求形状重新走循环(退化不是粘性的)
    monkeypatch.setattr(llm_config, "agent_loop_settings",
                        lambda home: {"enabled": True, "max_cycles": 6}, raising=False)
    events = _stream_events(client, "/api/converse", {"session": "demo", "text": "推进"})
    assert any(e["t"] == "agent.cycle" for e in events)
    assert len(calls) == 1


# ---------------- ② max_cycles 越界 400,挡在开流前 ----------------

@pytest.mark.parametrize("bad", [0, 49, -3, 2.5, "六", True, False])
def test_converse_max_cycles_out_of_range_400(client, monkeypatch, bad):
    calls: list = []
    install_fake_loop(monkeypatch, calls)
    client.post("/api/sessions", json={"id": "demo"})
    r = client.post("/api/converse",
                    json={"session": "demo", "text": "x", "max_cycles": bad})
    assert r.status_code == 400          # 统一 400(非 422/500,更不是开流后的 200)
    assert "1-48" in r.json()["detail"]  # 带候选说明
    assert calls == []                   # 挡在开流前:打桩生成器从未被调用


# ---------------- ③ 非法 session 400(口径同 /api/turn) ----------------

def test_converse_invalid_session_400_same_as_turn(client, monkeypatch):
    calls: list = []
    install_fake_loop(monkeypatch, calls)
    r = client.post("/api/converse", json={"session": "a/b", "text": "x"})
    assert r.status_code == 400
    assert "session 不合法" in r.json()["detail"]
    r_turn = client.post("/api/turn", json={"session": "a/b", "text": "x"})
    assert r_turn.status_code == 400                       # 同一 check_session_id
    assert r_turn.json()["detail"] == r.json()["detail"]   # 错误口径逐字一致
    assert calls == []


# ---------------- ④ 生成器异常 → 流内 note 事件,绝不裸断连 ----------------

def test_converse_generator_error_becomes_note(client, monkeypatch):
    calls: list = []
    install_fake_loop(monkeypatch, calls, fail_at=2)  # 第 2 周期爆炸
    client.post("/api/sessions", json={"id": "demo"})
    # _stream_events 消费到 EOF 未抛 = 连接正常收尾,错误是事件不是断连
    events = _stream_events(client, "/api/converse", {"session": "demo", "text": "x"})
    assert events[0]["t"] == "agent.cycle" and events[0]["n"] == 1
    assert events[-1]["t"] == "note" and events[-1]["tone"] == "bad"


# ---------------- ⑤ /api/skills 挂载守护(FEATURES 缺口 1) ----------------

def test_skills_router_mounted(client, monkeypatch, tmp_path):
    """GET /api/skills 必须 200(skills 信封),不再 404。
    指到空目录保持密封:本测试守挂载,不守仓库技能树内容。"""
    monkeypatch.setenv("INSAR_SKILLS_DIR", str(tmp_path / "no-skills"))
    r = client.get("/api/skills")
    assert r.status_code == 200
    assert r.json() == {"skills": []}


# ---------------- ⑥ llm.json 变化 → brain 原地替换,Driver/bus 不重建 ----------------

def test_llm_config_change_swaps_brain_in_place(tmp_path, monkeypatch):
    created: list = []

    class RecordingDriver(Driver):
        """记录实例:驱动器身份断言需要拿到 driver_of 闭包里的对象。"""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(app_module, "Driver", RecordingDriver)
    calls: list = []
    install_fake_loop(monkeypatch, calls)

    home = tmp_path / "home"
    cfg = {"base_url": "http://127.0.0.1:9/v1", "api_key": "sk-old", "chat_model": "m1"}
    with TestClient(create_app(home=home)) as client:
        client.post("/api/sessions", json={"id": "demo"})
        assert len(created) == 1
        d = created[0]
        brain0, bus0 = d.brain, d.bus
        assert not brain0.enabled  # 未配置 = brain 禁用(手动流水线形态)

        # 界面保存配置的落盘形态:写 llm.json → 下一次 driver_of 即生效
        (home / "llm.json").write_text(json.dumps(cfg), encoding="utf-8")
        _stream_events(client, "/api/converse", {"session": "demo", "text": "x"})
        assert created == [d]         # 绝不重建 Driver
        assert d.bus is bus0          # EventBus 同一对象,SSE 订阅者无感
        assert d.brain is not brain0  # brain 已原地替换
        assert d.brain.enabled        # 新路由生效
        brain1 = d.brain

        # 指纹未变:不反复替换(每请求都换会白扔 provider,也搅动降级语义)
        _stream_events(client, "/api/converse", {"session": "demo", "text": "x"})
        assert d.brain is brain1

        # 只换 api_key 也必须刷新:指纹含密钥,旧 provider 持旧密钥打不通
        (home / "llm.json").write_text(json.dumps({**cfg, "api_key": "sk-new"}),
                                       encoding="utf-8")
        _stream_events(client, "/api/converse", {"session": "demo", "text": "x"})
        assert d.brain is not brain1 and d.brain.enabled
        assert created == [d]


# ---------------- ⑦ abort 语义:控制位可被循环观察 ----------------

def test_abort_control_bit_observable_by_loop(client, monkeypatch):
    """POST /api/abort 置 control 位(202)后,循环生成器在周期边界经既有
    持久化位(store,absorb-E3)可观察到取消意图 —— 契约 §4 周期顺序第 1 条
    依赖的接口面,这里按简化断言验证。"""
    client.post("/api/sessions", json={"id": "demo"})
    # 规则路径真实 turn 造出 run:abort 的 resolve_run 要求会话有 run
    _stream_events(client, "/api/turn", {"session": "demo", "text": "Ridgecrest 地震同震"})
    run_id = client.get("/api/state", params={"session": "demo"}).json()["run"]["run_id"]

    observed: dict = {}

    async def spy_loop(self, session_id, text, *, max_cycles=6):
        # 与真实现同款观察通道:store 落盘的 control 位
        observed["control"] = (self.store.get_run(run_id) or {}).get("control")
        yield {"t": "note", "tone": "info", "text": "打桩:已读控制位"}

    monkeypatch.setattr(Driver, "converse_loop", spy_loop, raising=False)

    r = client.post("/api/abort", json={"session": "demo"})
    assert r.status_code == 202 and r.json()["accepted"] is True
    events = _stream_events(client, "/api/converse", {"session": "demo", "text": "停"})
    assert events == [{"t": "note", "tone": "info", "text": "打桩:已读控制位"}]
    assert observed["control"] == "cancel_requested"


def test_abort_unknown_session_404_and_never_materializes(client):
    """未知会话的 /api/abort 直接 404(P2-12):此前 driver_of 会为它物化
    会话行 + 工作区目录 ——「取消一个不存在的会话」不该有任何副作用。"""
    r = client.post("/api/abort", json={"session": "ghost-sess"})
    assert r.status_code == 404
    assert "不存在" in r.json()["detail"]
    rows = client.get("/api/sessions", params={"include_archived": True}).json()
    assert all(row["session_id"] != "ghost-sess" for row in rows)  # 未被物化


# ---------------- ⑧ 无 run 会话的 converse 回合可取消(202,不再 404) ----------------

@pytest.mark.timing
def test_abort_without_run_cancels_converse(client, monkeypatch):
    """会话从未产生 run 时:/api/abort 不再 404「no run」,而是置 driver 的
    会话级取消 token(202);converse 循环在周期边界消费它,流以 note 收尾。

    实现注记:TestClient 会把流式响应整体缓冲,故 converse 由工作线程发出,
    主线程凭 entered 信号(桩吐出首事件后置位)确认回合确实在途再 abort
    (test_api_loop_robustness 的闸门同法)。桩消费取消的通道与真实现同款
    (_loop_cancel_requested,真实方法未打桩)。"""
    entered = threading.Event()

    async def slow_cancellable_loop(self, session_id, text, *, max_cycles=6):
        yield {"t": "agent.cycle", "n": 1, "max": max_cycles, "action": "status"}
        entered.set()  # 首事件已产出:回合确实在途
        deadline = time.monotonic() + 20 * TIME_FACTOR
        while time.monotonic() < deadline:
            if self._loop_cancel_requested(session_id):
                yield {"t": "note", "tone": "warn",
                       "text": "自主循环已取消(第 2 周期边界);已完成周期的结果保留"}
                return
            await asyncio.sleep(0.02)  # 固定轮询间隔不乘系数(conftest 纪律)
        yield {"t": "say", "text": "判定窗内未观察到取消(兜底收尾)"}

    monkeypatch.setattr(Driver, "converse_loop", slow_cancellable_loop, raising=False)
    client.post("/api/sessions", json={"id": "norun"})
    # 前置对照:会话确实没有任何 run(abort 走会话级取消,不走 control 位)
    assert client.get("/api/state", params={"session": "norun"}).json()["run"] is None

    holder: list[list[dict]] = []
    worker = threading.Thread(
        target=lambda: holder.append(
            _stream_events(client, "/api/converse", {"session": "norun", "text": "跑"})),
        daemon=True)
    worker.start()
    assert entered.wait(20 * TIME_FACTOR), "converse 回合未在判定窗内开流"
    r = client.post("/api/abort", json={"session": "norun"})
    assert r.status_code == 202 and r.json() == {"accepted": True}  # 不再 404
    worker.join(timeout=30 * TIME_FACTOR)
    assert not worker.is_alive(), "converse 回合未在判定窗内收尾"
    events = holder[0]
    assert events[0]["t"] == "agent.cycle"
    assert events[-1]["t"] == "note" and "取消" in events[-1]["text"]
