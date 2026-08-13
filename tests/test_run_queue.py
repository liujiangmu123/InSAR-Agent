"""运行队列验收(loop/queue.py + api/queue_router.py)。

覆盖:FIFO 顺序、同 run 去重、取消(pending 可取/running 拒取)、重启恢复
(重开 store 后 pending 仍在,running 残留重新入队)、串行不变量(第二个 run
必须等第一个终态)、会话归属校验。

全部模拟后端,不跑真实 InSAR 计算:
  - 纯队列语义走内存库;
  - 调度器用可控的 Driver 替身(execute 在闸门处等待,模拟慢步骤)—— 事件驱动,
    等待只是上限,无真实时钟判定窗,不标 timing;
  - 另有一条穿 create_app 的全链集成用例驱动真实 simulated 子进程,标 timing
    (与仓库纪律一致:执行回合驱动子进程的用例归 timing 批)。
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import TIME_FACTOR
from insar_agent.api.queue_router import create_queue_router
from insar_agent.core.db import Database
from insar_agent.core.store import Store
from insar_agent.loop.events import EventBus
from insar_agent.loop.queue import QueueScheduler, RunQueue

# ---------------------------------------------------------------------------
# 造数与替身
# ---------------------------------------------------------------------------

HASHES = {"task_hash": "t", "args_hash": "a", "local_hash": "l", "eval_hash": "e"}


def seed_run(store: Store, session_id: str, run_id: str, *, status: str = "ready",
             steps: tuple[int, ...] = (1,)) -> str:
    """直接落一个可排队的 run(不经 planner:队列只关心 run 行、归属与步骤集)。"""
    store.create_session(session_id, session_id)
    store.create_run(run_id, session_id, workspace=f"ws/{run_id}")
    for sid in steps:
        store.upsert_step(run_id, sid, capability=f"cap{sid}", name=f"步骤{sid}",
                          method="m", params={}, hashes=HASHES)
    store.set_run_status(run_id, status)
    return run_id


class StubDriver:
    """可控 Driver 替身:execute 推进到闸门(Semaphore)处等待,模拟慢步骤。

    记录每次 execute 开始时全部 run 的状态快照(串行不变量的证据)与同时在跑
    的最大数量(必须恒为 1)。finish_status 可改为 "running",模拟运行锁被占
    的非终态收场(调度器应重新入队)。
    """

    def __init__(self, store: Store):
        self.store = store
        self.bus = EventBus()
        self.gate = asyncio.Semaphore(0)  # 测试逐次 release,放行一个 run 收尾
        self.started: list[str] = []
        self.at_start: dict[str, dict[str, str]] = {}
        self.running = 0
        self.max_running = 0
        self.finish_status = "done"

    async def execute(self, session_id, run_id=None, step_ids=None):
        self.started.append(run_id)
        self.at_start[run_id] = {r["run_id"]: r["status"]
                                 for r in self.store.list_runs()}
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        self.store.set_run_status(run_id, "running")
        yield {"t": "step.start", "stepId": 1}
        await self.gate.acquire()  # 慢步骤:直到测试放行才收尾
        self.store.set_run_status(run_id, self.finish_status)
        self.running -= 1
        yield {"t": "result"}


async def await_until(cond, timeout: float = 10.0, msg: str = "条件未满足"):
    """等待上限(乘负载系数),轮询节奏固定 —— 只是兜底,不构成时序判定窗。"""
    deadline = time.monotonic() + timeout * TIME_FACTOR
    while time.monotonic() < deadline:
        if cond():
            return
        await asyncio.sleep(0.005)
    pytest.fail(f"{msg}(等待超时)")


def drain(q: asyncio.Queue) -> list[dict]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


# ---------------------------------------------------------------------------
# RunQueue 纯语义(内存库)
# ---------------------------------------------------------------------------

def test_fifo_order_and_position(store):
    q = RunQueue(store)
    for rid in ("rA", "rB", "rC"):
        seed_run(store, "s1", rid)
    assert q.enqueue("rA", "s1") == (1, True)
    assert q.enqueue("rB", "s1") == (2, True)
    assert q.enqueue("rC", "s1") == (3, True)

    head = q.next_pending()
    assert head["run_id"] == "rA"  # FIFO:先入队先出
    assert q.mark_running(head["id"])
    # running 条目恒为第 1 位,pending 依次后排
    assert q.position("rA") == 1 and q.position("rB") == 2 and q.position("rC") == 3
    q.settle(head["id"])
    assert q.position("rA") is None  # 终态离队
    assert q.next_pending()["run_id"] == "rB"
    assert q.position("rB") == 1


def test_enqueue_dedup_same_run(store):
    q = RunQueue(store)
    seed_run(store, "s1", "rA")
    assert q.enqueue("rA", "s1") == (1, True)
    # 同 run 已在队:不重复插入,返回既有位置
    assert q.enqueue("rA", "s1") == (1, False)
    assert len(q.snapshot()) == 1
    # 执行中(running)同样去重
    head = q.next_pending()
    q.mark_running(head["id"])
    assert q.enqueue("rA", "s1") == (1, False)
    # 终态后不再去重:允许再次排队重跑
    q.settle(head["id"])
    assert q.enqueue("rA", "s1") == (1, True)


def test_cancel_pending_only(store):
    q = RunQueue(store)
    seed_run(store, "s1", "rA")
    seed_run(store, "s1", "rB")
    q.enqueue("rA", "s1")
    q.enqueue("rB", "s1")
    assert q.cancel("rB") is True
    assert q.position("rB") is None
    assert q.cancel("rB") is False  # 幂等:已取消无可取消

    head = q.next_pending()
    assert head["run_id"] == "rA"
    # 取队首后、启动前被取消:mark_running 的条件更新输掉,调度器跳过
    assert q.cancel("rA") is True
    assert q.mark_running(head["id"]) is False
    assert q.next_pending() is None

    # running 的不能取消(执行域取消走 KILL)
    seed_run(store, "s1", "rC")
    q.enqueue("rC", "s1")
    entry = q.next_pending()
    q.mark_running(entry["id"])
    assert q.cancel("rC") is False
    assert q.position("rC") == 1


def test_restart_recovery_pending_survives(tmp_path):
    """重启恢复:重开 store 后 pending 仍在;running 残留重置回 pending 且
    保留原 id(仍在队首,FIFO 公平);step_ids 持久化往返不丢。"""
    db_path = tmp_path / "queue.db"
    store = Store(Database(db_path))
    seed_run(store, "s1", "rA")
    seed_run(store, "s1", "rB")
    q = RunQueue(store)
    q.enqueue("rA", "s1")
    q.enqueue("rB", "s1", step_ids=[1])
    head = q.next_pending()
    assert q.mark_running(head["id"])  # 模拟:执行中进程崩溃
    store.close()

    store2 = Store(Database(db_path))  # 服务重启:重开同一库
    try:
        q2 = RunQueue(store2)
        snap = q2.snapshot()
        assert [e["run_id"] for e in snap] == ["rA", "rB"]  # 队列没丢
        assert snap[0]["state"] == "running"                # 崩溃残留可见
        assert snap[1]["step_ids"] == [1]                   # 选集往返一致
        assert q2.recover() == 1  # 调度器 startup 语义:running → pending 重新入队
        assert q2.next_pending()["run_id"] == "rA"          # 原 id 保住队首
        assert q2.position("rA") == 1 and q2.position("rB") == 2
        assert q2.enqueue("rA", "s1") == (1, False)         # 跨重启仍去重
    finally:
        store2.close()


# ---------------------------------------------------------------------------
# QueueScheduler:串行不变量 / 取消跳过 / 非终态重新入队(Driver 替身)
# ---------------------------------------------------------------------------

def test_scheduler_serial_invariant(store):
    """两个 run 入队:第二个必须等第一个终态;全程同时在跑的 run 恒为 1。"""
    seed_run(store, "s1", "rA")
    seed_run(store, "s1", "rB")
    q = RunQueue(store)
    stub = StubDriver(store)
    sched = QueueScheduler(q, lambda sid: stub, poll=0.01, retry_delay=0.01)

    async def scenario():
        bus_q = stub.bus.subscribe()
        q.enqueue("rA", "s1")
        q.enqueue("rB", "s1")
        await sched.start()
        try:
            await await_until(lambda: stub.started == ["rA"], msg="rA 未开始")
            # rA 执行中(慢步骤挂在闸门上):rB 必须仍在排队,未被并行启动
            assert q.position("rB") == 2
            assert store.get_run("rB")["status"] == "ready"
            assert stub.running == 1

            stub.gate.release()  # 放行 rA 收尾
            await await_until(lambda: stub.started == ["rA", "rB"], msg="rB 未接续")
            # 串行不变量的直接证据:rB 开始那一刻 rA 已是终态
            assert stub.at_start["rB"]["rA"] == "done"

            stub.gate.release()  # 放行 rB 收尾
            await await_until(lambda: store.get_run("rB")["status"] == "done",
                              msg="rB 未完成")
            await await_until(lambda: q.snapshot() == [], msg="队列未清空")
        finally:
            await sched.stop()
        assert stub.max_running == 1  # 全局并发恒为 1(本机重计算管控)
        notes = [e for e in drain(bus_q) if e["t"] == "note"]
        assert sum("开始执行" in e["text"] for e in notes) == 2  # 出队事件两次

    asyncio.run(scenario())


def test_scheduler_skips_cancelled_entry(store):
    seed_run(store, "s1", "rA")
    seed_run(store, "s1", "rB")
    q = RunQueue(store)
    stub = StubDriver(store)
    sched = QueueScheduler(q, lambda sid: stub, poll=0.01, retry_delay=0.01)

    async def scenario():
        q.enqueue("rA", "s1")
        q.enqueue("rB", "s1")
        q.cancel("rA")
        stub.gate.release()  # 预放行,execute 不阻塞
        assert await sched.dispatch_next() is True
        assert stub.started == ["rB"]  # 已取消的 rA 不被执行
        assert await sched.dispatch_next() is False  # 队空

    asyncio.run(scenario())


def test_scheduler_requeues_on_non_terminal(store):
    """非终态收场(运行锁被别的回合占用)→ 重新入队退避重试,直到接管成功。

    这就是崩溃恢复的另一半:recover() 把残留 running 条目放回队首,若上个
    进程的租约未过 stale 窗,execute 会被拒(run 停在 running),调度器识别
    后重排队;租约过期(约 60s)后接管成功,循环收敛。
    """
    seed_run(store, "s1", "rA")
    q = RunQueue(store)
    stub = StubDriver(store)
    stub.finish_status = "running"  # 模拟:回合被运行锁拒绝,run 状态没到终态
    sched = QueueScheduler(q, lambda sid: stub, poll=0.01, retry_delay=0.01)

    async def scenario():
        bus_q = stub.bus.subscribe()
        q.enqueue("rA", "s1")
        stub.gate.release()
        assert await sched.dispatch_next() is True
        # 非终态 → 条目回 pending(不是 done),等待下一轮重试
        assert q.next_pending()["run_id"] == "rA"
        assert any(e["t"] == "note" and "重新排队" in e["text"] for e in drain(bus_q))

        stub.finish_status = "done"  # 模拟:租约已过 stale 窗,接管成功
        stub.gate.release()
        assert await sched.dispatch_next() is True
        assert q.snapshot() == [] and store.get_run("rA")["status"] == "done"
        assert stub.started == ["rA", "rA"]

    asyncio.run(scenario())


def test_scheduler_start_recovers_running_leftover(store):
    """调度器 startup 即恢复:崩溃残留的 running 条目重新入队并被执行。"""
    seed_run(store, "s1", "rA")
    q = RunQueue(store)
    q.enqueue("rA", "s1")
    q.mark_running(q.next_pending()["id"])  # 模拟上个进程崩溃在执行中
    stub = StubDriver(store)
    sched = QueueScheduler(q, lambda sid: stub, poll=0.01, retry_delay=0.01)

    async def scenario():
        stub.gate.release()
        await sched.start()  # recover():running → pending,随即被调度
        try:
            await await_until(lambda: store.get_run("rA")["status"] == "done",
                              msg="恢复的条目未被执行")
        finally:
            await sched.stop()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# API:归属校验 / 位置与去重 / 取消语义 / 事件(迷你 app,不起调度器)
# ---------------------------------------------------------------------------

@pytest.fixture()
def api(store):
    """只挂 queue_router 的迷你 app:driver_of 给带 EventBus 的最小替身。
    不经 create_app(不触发 startup)—— 端点语义的确定性验证。"""
    q = RunQueue(store)

    class _D:
        def __init__(self):
            self.bus = EventBus()

    drivers: dict[str, _D] = {}

    def driver_of(sid: str) -> _D:
        return drivers.setdefault(sid, _D())

    app = FastAPI()
    app.include_router(create_queue_router(store, q, driver_of))
    return TestClient(app), q, driver_of


def test_api_session_ownership(api, store):
    client, q, _ = api
    seed_run(store, "s1", "rA")
    seed_run(store, "s2", "rX")
    # 跨会话引用别人的 run:一律 404,不泄露存在性
    r = client.post("/api/queue", json={"session": "s2", "run_id": "rA"})
    assert r.status_code == 404
    r = client.delete("/api/queue/rA", params={"session": "s2"})
    assert r.status_code == 404
    # 没有任何 run 的会话:404 no run
    assert client.post("/api/queue", json={"session": "ghost"}).status_code == 404
    # 归属正确:入队成功;run_id 缺省 = 该会话最近 run
    r = client.post("/api/queue", json={"session": "s1"})
    assert r.status_code == 202
    assert r.json() == {"queued": True, "run_id": "rA", "position": 1,
                        "ahead": 0, "created": True}


def test_api_positions_dedup_and_events(api, store):
    client, q, driver_of = api
    seed_run(store, "s1", "rA")
    seed_run(store, "s1", "rB")
    bus_q = driver_of("s1").bus.subscribe()

    assert client.post("/api/queue", json={"session": "s1", "run_id": "rA"}
                       ).json()["position"] == 1
    body = client.post("/api/queue", json={"session": "s1", "run_id": "rB"}).json()
    assert body["position"] == 2 and body["ahead"] == 1  # 「排队中,前面 1 个」
    # 同 run 去重:位置不变,created=False,不重复发入队事件
    again = client.post("/api/queue", json={"session": "s1", "run_id": "rA"}).json()
    assert again == {"queued": True, "run_id": "rA", "position": 1,
                     "ahead": 0, "created": False}
    notes = [e for e in drain(bus_q) if e["t"] == "note"]
    assert len(notes) == 2 and "第 1 位" in notes[0]["text"] and "第 2 位" in notes[1]["text"]

    items = client.get("/api/queue").json()["items"]
    assert [(e["run_id"], e["position"], e["state"]) for e in items] == \
        [("rA", 1, "pending"), ("rB", 2, "pending")]
    # 会话过滤不失真:position 仍按全局队列计
    assert client.get("/api/queue", params={"session": "s2"}).json()["items"] == []

    # 非法步骤 id 挡在入口(与 /api/pipeline 同规)
    r = client.post("/api/queue", json={"session": "s1", "run_id": "rA",
                                        "step_ids": [99]})
    assert r.status_code == 400 and "99" in r.json()["detail"]


def test_api_cancel_semantics(api, store):
    client, q, _ = api
    seed_run(store, "s1", "rA")
    seed_run(store, "s1", "rB")
    client.post("/api/queue", json={"session": "s1", "run_id": "rA"})
    client.post("/api/queue", json={"session": "s1", "run_id": "rB"})
    q.mark_running(q.next_pending()["id"])  # rA 进入执行

    # running 的不能取消排队:409 且指路 KILL
    r = client.delete("/api/queue/rA", params={"session": "s1"})
    assert r.status_code == 409 and "KILL" in r.json()["detail"]
    # pending 可取消
    assert client.delete("/api/queue/rB", params={"session": "s1"}
                         ).json() == {"cancelled": True, "run_id": "rB"}
    # 已不在队:404
    assert client.delete("/api/queue/rB", params={"session": "s1"}).status_code == 404


# ---------------------------------------------------------------------------
# create_app 接线(不起调度器)+ 全链集成(真实调度器,timing 批)
# ---------------------------------------------------------------------------

def _empty_probe(*args, **kwargs):
    from insar_agent.runtime.probe import ProbeResult

    return ProbeResult(
        engines={k: None for k in ("isce2", "mintpy", "snaphu", "gdal", "snap",
                                   "pystamps", "pyaps")},
        credentials={"earthdata": False, "cds": False, "gacos": False},
        disk_free_gb=100.0, cpu_count=8)


def _stream_events(client: TestClient, url: str, body: dict) -> list[dict]:
    events = []
    with client.stream("POST", url, json=body) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line.strip():
                events.append(json.loads(line))
    return events


def test_create_app_queue_wiring(tmp_path, monkeypatch):
    """真实 create_app:路由已挂上,turn 造的计划可入队并拿到位置。
    不进 TestClient 上下文 → 不触发 startup,调度器不跑(只验接线)。"""
    monkeypatch.setattr("insar_agent.loop.driver.probe_environment", _empty_probe)
    from insar_agent.api.app import create_app

    client = TestClient(create_app(home=tmp_path / "home"))
    assert client.get("/api/queue").json() == {"items": []}
    client.post("/api/sessions", json={"id": "demo"})
    _stream_events(client, "/api/turn", {"session": "demo", "text": "Ridgecrest 地震同震"})
    r = client.post("/api/queue", json={"session": "demo"})
    assert r.status_code == 202 and r.json()["position"] == 1
    items = client.get("/api/queue", params={"session": "demo"}).json()["items"]
    assert len(items) == 1 and items[0]["state"] == "pending"


@pytest.mark.timing  # 队列调度驱动真实 simulated 子进程(执行时长依赖真实时钟)
def test_queue_end_to_end_with_real_driver(tmp_path, monkeypatch):
    """全链:startup 起调度器,入队后由队列(而非 /api/pipeline)把 run 跑到 done。"""
    monkeypatch.setattr("insar_agent.loop.driver.probe_environment", _empty_probe)
    from insar_agent.api.app import create_app

    app = create_app(home=tmp_path / "home")
    with TestClient(app) as client:  # 进上下文:startup 恢复队列并起调度任务
        client.post("/api/sessions", json={"id": "demo"})
        _stream_events(client, "/api/turn",
                       {"session": "demo", "text": "Ridgecrest 地震同震"})
        r = client.post("/api/queue", json={"session": "demo"})
        assert r.status_code == 202 and r.json()["ahead"] == 0

        deadline = time.time() + 120 * TIME_FACTOR
        status = None
        while time.time() < deadline:
            status = client.get("/api/state", params={"session": "demo"}
                                ).json()["run"]["status"]
            if status in ("done", "failed", "interrupted"):
                break
            time.sleep(0.3)
        assert status == "done"  # 队列驱动执行到终态
        assert client.get("/api/queue").json()["items"] == []  # 条目已结算离队
