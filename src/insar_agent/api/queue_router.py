"""运行队列端点(/api/queue):排队执行,替代「并发被运行锁拒绝」的体验。

  - POST   /api/queue           入队(校验会话归属;返回 1-based 位置;同 run 去重)
  - GET    /api/queue           当前活跃队列(running+pending,含位置;可按会话过滤)
  - DELETE /api/queue/{run_id}  取消排队(仅 pending;running 的取消走既有 KILL 干预)

直接执行端点(/api/pipeline)保持不动 —— 直接执行是抢跑语义,与队列共存:
同 run 冲突由 driver 的运行锁(租约)仲裁,调度器对被拒条目重新入队退避重试。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from insar_agent.core.store import Store
from insar_agent.loop import events as ev
from insar_agent.loop.queue import RunQueue


class QueueBody(BaseModel):
    session: str
    run_id: str | None = None    # 缺省 = 该会话最近一个 run(对齐 /api/pipeline)
    step_ids: list[int] | None = None


def create_queue_router(store: Store, queue: RunQueue, driver_of) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["queue"])

    def owned_run(session_id: str, run_id: str | None) -> dict:
        """会话归属校验(语义对齐 app.resolve_run):缺省取最近 run;
        不存在或不属于该会话一律 404,不泄露其他会话的 run 是否存在。"""
        run = store.get_run(run_id) if run_id else store.latest_run(session_id)
        if run is None:
            raise HTTPException(404, "no run")
        if run["session_id"] != session_id:
            raise HTTPException(404, f"run {run['run_id']} 不存在或不属于会话 {session_id}")
        return run

    @router.post("/queue")
    def enqueue(body: QueueBody):
        run = owned_run(body.session, body.run_id)
        run_id = run["run_id"]
        if body.step_ids:
            # 与 /api/pipeline 同规:非法步骤 id 挡在入口,不让它在调度时引爆
            planned = {s.step_id for s in store.load_steps(run_id)}
            bad = [sid for sid in body.step_ids if sid not in planned]
            if bad:
                raise HTTPException(400, f"步骤 id 不在该 run 的计划内:{bad}")
        position, created = queue.enqueue(run_id, body.session, body.step_ids)
        if created:
            # 入队事件进会话 EventBus(前端聊天流看到「排队第 N 位」)
            driver_of(body.session).bus.publish(ev.note(
                "ok", f"run {run_id} 已加入运行队列:第 {position} 位"
                      f"(前面 {position - 1} 个),空闲后自动开始"))
        return JSONResponse({"queued": True, "run_id": run_id, "position": position,
                             "ahead": position - 1, "created": created},
                            status_code=202)

    @router.get("/queue")
    def list_queue(session: str | None = None):
        items = queue.snapshot(session_id=session)
        return {"items": [
            {"run_id": e["run_id"], "session_id": e["session_id"],
             "state": e["state"], "position": e["position"],
             "step_ids": e["step_ids"], "enqueued_at": e["enqueued_at"],
             "started_at": e["started_at"]} for e in items]}

    @router.delete("/queue/{run_id}")
    def cancel(run_id: str, session: str):
        owned_run(session, run_id)
        if queue.cancel(run_id):
            driver_of(session).bus.publish(ev.note(
                "warn", f"run {run_id} 已取消排队(未开始执行)"))
            return {"cancelled": True, "run_id": run_id}
        if queue.position(run_id) is not None:
            # 仍在队但取消失败 = 已在执行:取消执行走既有 KILL(/api/actions)
            raise HTTPException(409, f"run {run_id} 已在执行,不能取消排队;"
                                     f"终止执行请用 KILL 干预(/api/actions)")
        raise HTTPException(404, f"run {run_id} 不在队列中")

    return router
