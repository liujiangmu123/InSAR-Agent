"""一键体检 API(GET /api/doctor):执行 insar_agent.doctor.check_all 并返回 JSON。

两道保护(检查项本身秒级只读,保护针对的是异常环境下的极端情形):
  - 并发防抖(单飞):同一时刻只跑一份体检,后到的请求等同一份结果 ——
    前端连点「一键体检」/多窗口同时体检不会放大探测开销;
  - 20s 超时:体检卡死(磁盘/WSL 异常挂起)时请求返回 504 不占死连接;
    后台线程继续收尾,结果仍会被后续请求复用(单飞未失效)。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException

from insar_agent.doctor import check_all, summarize


def create_doctor_router(home: Path | str | None = None, *,
                         timeout: float = 20.0) -> APIRouter:
    """home 不传则由 doctor 按 INSAR_HOME 动态解析(与 create_app 默认一致);
    create_app(home=...) 显式传目录时应把同一 home 传进来。timeout 供测试调小。"""
    router = APIRouter(tags=["doctor"])

    # 单飞状态:执行线程池 1 个工位 + 在飞 Future。用 concurrent.futures 而非
    # asyncio 原语:结果要能被不同事件循环(多请求/TestClient)安全等待
    pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="doctor")
    lock = threading.Lock()
    state: dict = {"future": None}

    def _run() -> dict:
        t0 = time.perf_counter()
        summary = summarize(check_all(home))
        summary["took_ms"] = int((time.perf_counter() - t0) * 1000)
        return summary

    @router.get("/api/doctor")
    async def doctor() -> dict:
        with lock:
            fut = state["future"]
            if fut is None or fut.done():  # 没人在跑(或上一份已出结果)→ 起新体检
                fut = pool.submit(_run)
                state["future"] = fut
        try:
            # wait_for 超时会取消 wrap 出来的 asyncio Future,但运行中的
            # concurrent Future 不可取消 —— 体检继续跑完,后续请求仍复用
            return await asyncio.wait_for(asyncio.wrap_future(fut), timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            raise HTTPException(
                504, f"体检超时(>{timeout:g}s):后台仍在执行,稍后重试可直接复用结果")

    return router
