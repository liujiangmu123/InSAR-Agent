"""运行队列:SQLite 持久 FIFO + 全局串行调度(本机重计算管控:并发=1)。

背景:并发 execute 会被 run 级租约直接拒绝(driver.execute,§4.11),用户体感是
「被拒」。改为排队:POST /api/queue 入队,app 生命周期内唯一的调度任务串行取
队首执行,前端经会话 EventBus 看到「排队第 N 位」「开始执行」。

设计取舍:
  - 持久化进 SQLite(run_queue 表,建表走 core/db.py 的迁移语句):服务重启后
    pending 队列自动恢复调度,不丢用户的排队意图;
  - 全局并发 = 1(跨会话统一排):对齐「一次只跑一个计算任务」的本机管控;
  - 崩溃恢复:残留 running 条目重置回 pending(保留原 id → 仍在队首),真正的
    执行接管交给 driver 的租约语义 —— 崩溃回合的租约约 60s 后可被接管,未到期
    时 execute 被拒(run 状态停在 running),调度器识别为「非终态收场」重新入队
    退避重试,收敛到接管成功;
  - 事件不重复转发:driver._emit 已把每个事件发布到会话 EventBus,调度器只负责
    排干迭代器;队列自身的入队/出队/开始执行以 note 事件另行发布。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager

from insar_agent.core.store import Store
from insar_agent.loop import events as ev

log = logging.getLogger(__name__)

#: 活跃条目按「执行中优先、其余按入队序」排;position 即该序下的 1-based 序号
_ACTIVE_ORDER = (" AND state IN ('pending','running')"
                 " ORDER BY (state='running') DESC, id ASC")


class RunQueue:
    """SQLite 持久队列(表 run_queue,见 core/db.py 的迁移语句)。

    语义:enqueue 去重(同 run 已在队则返回既有位置)、position 1-based
    (running 条目恒为第 1 位)、cancel 仅限 pending、next_pending 按 FIFO。
    写路径全部走 store.db.tx()(与 store 共用一把 RLock,单进程内线程安全)。
    """

    def __init__(self, store: Store):
        self.store = store

    def enqueue(self, run_id: str, session_id: str,
                step_ids: list[int] | None = None) -> tuple[int, bool]:
        """入队;同 run 已有 pending/running 条目时不重复插入(去重)。

        返回 (position, created):position 为 1-based 队列位置,created=False
        表示复用既有条目(此时 step_ids 不覆写 —— 首次入队的选集生效)。
        """
        with self.store.db.tx() as cur:
            row = cur.execute(
                "SELECT id FROM run_queue WHERE run_id=?"
                " AND state IN ('pending','running')", (run_id,)).fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO run_queue(run_id,session_id,step_ids_json,enqueued_at,state)"
                    " VALUES (?,?,?,?,'pending')",
                    (run_id, session_id,
                     None if step_ids is None else json.dumps(list(step_ids)),
                     time.time()))
                entry_id, created = int(cur.lastrowid), True
            else:
                entry_id, created = int(row["id"]), False
            rows = cur.execute("SELECT id FROM run_queue WHERE 1=1" + _ACTIVE_ORDER).fetchall()
            position = next(i + 1 for i, r in enumerate(rows) if r["id"] == entry_id)
            return position, created

    def position(self, run_id: str) -> int | None:
        """1-based 队列位置(running 恒为第 1 位);不在队(含已终态)返回 None。"""
        rows = self.store.db.query(
            "SELECT run_id FROM run_queue WHERE 1=1" + _ACTIVE_ORDER)
        for i, r in enumerate(rows):
            if r["run_id"] == run_id:
                return i + 1
        return None

    def cancel(self, run_id: str) -> bool:
        """取消排队。仅限 pending:running 属于执行域,取消要走既有 KILL 干预。"""
        with self.store.db.tx() as cur:
            cur.execute("UPDATE run_queue SET state='cancelled'"
                        " WHERE run_id=? AND state='pending'", (run_id,))
            return cur.rowcount > 0

    def next_pending(self) -> dict | None:
        """FIFO 队首(按 id;崩溃恢复的条目保留原 id,天然回到队首)。"""
        r = self.store.db.query_one(
            "SELECT * FROM run_queue WHERE state='pending' ORDER BY id LIMIT 1")
        return _entry(r) if r else None

    def mark_running(self, entry_id: int) -> bool:
        """pending → running。条件更新:取队首后、启动前被 cancel 的条目返回
        False,调度器直接跳过(不执行已取消的排队)。"""
        with self.store.db.tx() as cur:
            cur.execute("UPDATE run_queue SET state='running', started_at=?"
                        " WHERE id=? AND state='pending'", (time.time(), entry_id))
            return cur.rowcount > 0

    def settle(self, entry_id: int, *, requeue: bool = False) -> None:
        """执行回合收场后的结算:终态 → done;非终态(运行锁被占)→ 回 pending。"""
        with self.store.db.tx() as cur:
            if requeue:
                cur.execute("UPDATE run_queue SET state='pending', started_at=NULL"
                            " WHERE id=?", (entry_id,))
            else:
                cur.execute("UPDATE run_queue SET state='done' WHERE id=?", (entry_id,))

    def recover(self) -> int:
        """启动恢复:残留 running(上个进程崩溃在执行中)重置回 pending。

        重新入队而不是直接判死:run 自身的推进状态归 driver/store 所有,重跑由
        execute 的待跑集合(含 running 步骤的接回)与租约接管语义安全推进。"""
        with self.store.db.tx() as cur:
            cur.execute("UPDATE run_queue SET state='pending', started_at=NULL"
                        " WHERE state='running'")
            return cur.rowcount

    def snapshot(self, session_id: str | None = None) -> list[dict]:
        """活跃队列视图(running+pending,含 1-based position)。

        session_id 过滤在序号计算之后:position 必须按全局队列算,先过滤会失真。
        """
        rows = self.store.db.query("SELECT * FROM run_queue WHERE 1=1" + _ACTIVE_ORDER)
        out = []
        for i, r in enumerate(rows):
            e = _entry(r)
            e["position"] = i + 1
            if session_id is None or e["session_id"] == session_id:
                out.append(e)
        return out


def _entry(row) -> dict:
    e = dict(row)
    raw = e.pop("step_ids_json")
    e["step_ids"] = json.loads(raw) if raw else None
    return e


class QueueScheduler:
    """app 生命周期内唯一的调度任务:串行取队首 → driver.execute → 终态后取下一个。

    全程一次只有一个 run 在执行(全局并发=1)。driver 的事件已在 _emit 里发布到
    会话 EventBus(SSE 消费同一总线),这里只排干迭代器,不重复 publish。
    """

    def __init__(self, queue: RunQueue, driver_of, *, poll: float = 0.5,
                 retry_delay: float = 5.0):
        self.queue = queue
        self.driver_of = driver_of      # session_id -> Driver(app.py 的会话级工厂)
        self.poll = poll                # 空队轮询间隔
        self.retry_delay = retry_delay  # 非终态收场重新入队后的退避
        self._task: asyncio.Task | None = None

    def install(self, app) -> None:
        """挂到 FastAPI 生命周期:startup 恢复队列并起任务,shutdown 收尾。

        以包裹既有 lifespan_context 的方式接入(FastAPI 0.141/Starlette 1.x
        已移除 app 级事件处理器机制,on_event 也已废弃)。"""
        inner = app.router.lifespan_context

        @asynccontextmanager
        async def _lifespan(app_):
            async with inner(app_):
                await self.start()
                try:
                    yield
                finally:
                    await self.stop()

        app.router.lifespan_context = _lifespan

    async def start(self) -> None:
        recovered = self.queue.recover()
        if recovered:
            log.info("run_queue:重启恢复 %d 个执行中断的条目(重新入队)", recovered)
        self._task = asyncio.create_task(self._run(), name="run-queue-scheduler")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                busy = await self.dispatch_next()
            except Exception:  # noqa: BLE001 —— 调度器绝不因单个条目而死
                log.exception("run_queue:调度条目时异常(条目已结算或重新入队)")
                busy = True
            if not busy:
                await asyncio.sleep(self.poll)

    async def dispatch_next(self) -> bool:
        """取队首执行到结算;返回是否处理了条目(False=队空,调用方退避)。

        独立成方法而不内联进 _run:测试可逐条驱动,断言串行与结算语义,
        不必与轮询节奏赛跑。
        """
        entry = self.queue.next_pending()
        if entry is None:
            return False
        if not self.queue.mark_running(entry["id"]):
            return True  # 取队首后被取消 —— 跳过,下一轮取新队首
        driver = self.driver_of(entry["session_id"])
        waited = max(0.0, time.time() - entry["enqueued_at"])
        driver.bus.publish(ev.note(
            "ok", f"运行队列:run {entry['run_id']} 出队,开始执行"
                  f"(排队等待 {waited:.0f} 秒)"))
        try:
            async for _ in driver.execute(entry["session_id"], entry["run_id"],
                                          entry["step_ids"]):
                pass  # 事件已由 driver._emit 进会话 EventBus,这里只推进流
        except Exception:  # noqa: BLE001
            # driver.execute 已把意外异常 reconcile 成 run failed(留痕后上抛);
            # 调度器吞掉以保住循环,细节进服务端日志
            log.exception("run_queue:run %s 执行异常", entry["run_id"])
        status = (self.queue.store.get_run(entry["run_id"]) or {}).get("status")
        if status == "running":
            # 非终态收场 = 运行锁被别的回合占着(直接执行抢跑,或崩溃租约未过
            # stale 窗)→ 重新入队(原 id 保住队首),退避后重试;崩溃租约约
            # 60s 后过 stale 窗即可接管,循环收敛
            self.queue.settle(entry["id"], requeue=True)
            driver.bus.publish(ev.note(
                "warn", f"运行队列:run {entry['run_id']} 的运行锁被其他回合占用,"
                        f"已重新排队,{self.retry_delay:.0f} 秒后重试"))
            await asyncio.sleep(self.retry_delay)
        else:
            self.queue.settle(entry["id"])
        return True
