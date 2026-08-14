"""事件总线:被动监听 + 错误隔离(absorb-E8,pi harness.md:2325-2327)。

事件形状与 prototype/js/backend.mock.js 的契约一致(t 字段区分类型),
UI(SSE)与轨迹(trace)消费同一批事件的不同侧面。

隔离保证:监听器抛错绝不影响主循环 —— 错误被转成一条 handler_error 事件。
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator


class EventBus:
    def __init__(self, maxsize: int = 1000):
        self._subscribers: set[asyncio.Queue] = set()
        self._maxsize = maxsize

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def publish(self, event: dict) -> None:
        dead = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # 消费者积压:丢弃其最旧一条再放(UI 事件容忍丢帧;trace 由 driver 直写)
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    # 预期内的队列竞态闭集(恰被抽干/又被塞满)→ 视作死订阅者剔除;
                    # 其他异常是编程错误,不吞(REVIEW P2-2 收窄)
                    dead.append(q)
        for q in dead:
            self._subscribers.discard(q)

    async def stream(self) -> AsyncIterator[dict]:
        q = self.subscribe()
        try:
            while True:
                yield await q.get()
        finally:
            self.unsubscribe(q)


# ---------------- 事件工厂(形状对齐 mock 契约 + §7.4 四种新条目) ----------------

def thinking(title: str, body: str) -> dict:
    return {"t": "thinking", "title": title, "body": body}


def say(parts: list) -> dict:
    return {"t": "say", "parts": parts}


def say_delta(text: str) -> dict:
    """流式回复增量(0814B 契约 §1.3)。生命周期:say.delta × N → 终帧二选一:
    say(定稿,parts 整体替换)或 say.abort(标废)。

    通道例外(docs/AGENT-LOOP.md §4.1「delta 例外条款」):只走回合 NDJSON
    (driver 裸 yield),不经 _emit —— 不上 EventBus/SSE、不进 trace;
    终帧 say 照旧双通道。前端 app.js 的 liveSay 状态机按 textContent 追加。
    """
    return {"t": "say.delta", "text": text}


def say_abort(reason: str) -> dict:
    """流式回复标废(终帧之一):半截回复不是回复,前端保留已见文本并如实标注。

    reason 闭集:truncated(token 上限截断)| unavailable(供应商失败)|
    stopped(用户停止)。通道例外与 say_delta 同款(不经 _emit)。
    """
    return {"t": "say.abort", "reason": reason}


def plan(items: list[dict]) -> dict:
    return {"t": "plan", "items": items}


def tool_start(tool_id: str, verb: str, cmd: str, label: str, open_: bool = False) -> dict:
    return {"t": "tool.start", "id": tool_id, "verb": verb, "cmd": cmd,
            "label": label, "open": open_}


def tool_log(tool_id: str, line: str, tone: str = "dim") -> dict:
    return {"t": "tool.log", "id": tool_id, "line": line, "tone": tone}


def tool_end(tool_id: str, exit_code: int, summary: str, artifacts: list | None = None) -> dict:
    return {"t": "tool.end", "id": tool_id, "exit": exit_code, "summary": summary,
            "artifacts": artifacts or []}


def step_start(step_id: int) -> dict:
    return {"t": "step.start", "stepId": step_id}


def step_end(step_id: int, exit_code: int) -> dict:
    return {"t": "step.end", "stepId": step_id, "exit": exit_code}


def step_stage(step_id: int, stage: str) -> dict:
    """五阶段执行器的阶段推进(PREPARED/LAUNCHED/COLLECTED/VERIFIED)。

    runtime/executor.py 直发同形状字面量;此工厂登记形状,供契约测试对账
    (tests/test_e2e_contract.py 的事件类型注册表)。前端当前静默忽略该类型。
    """
    return {"t": "step.stage", "stepId": step_id, "stage": stage}


def agent_cycle(n: int, max_cycles: int, action: str) -> dict:
    """自主循环周期账(LOOP-CONTRACT §1,driver.converse_loop 每周期恰好一条)。

    n 从 1 递增;action 是循环动作闭集(search_data/inspect_file/check_env/
    list_data/status/plan/execute/set_params/set_method/thinking)成员。
    app.js 的 consume() 对该类型静默丢弃,由 prototype/js/agentloop.js
    自建 SSE 订阅渲染「自主工作中」进度条与工作记录卡。
    """
    return {"t": "agent.cycle", "n": n, "max": max_cycles, "action": action}


def overall(pct: int) -> dict:
    return {"t": "overall", "pct": pct}


def candidates(step_id: int) -> dict:
    return {"t": "candidates", "stepId": step_id}


def note(tone: str, text: str) -> dict:
    return {"t": "note", "tone": tone, "text": text}


def result() -> dict:
    return {"t": "result"}


def report() -> dict:
    return {"t": "report"}


def ask(prompt: str, fields: list[dict]) -> dict:
    return {"t": "ask", "prompt": prompt, "fields": fields}


# §7.4 需补的四种条目

def reattach(text: str) -> dict:
    return {"t": "reattach", "text": text}


def intervention(text: str, affected: list[int] | None = None,
                 mode: str = "queue") -> dict:
    """干预留痕。mode 对齐前端 interventionEntry 的两种投递语义:
    queue(步间生效,前端缺省)| steer(立即生效)。"""
    return {"t": "intervention", "text": text, "affected": affected or [],
            "mode": mode}


def degrade(text: str, evidence_before: str, evidence_after: str) -> dict:
    return {"t": "degrade", "text": text,
            "evidenceBefore": evidence_before, "evidenceAfter": evidence_after}


def gate_stop(text: str, suggestions: list[str] | None = None,
              step_id: int | None = None) -> dict:
    """质量门拦停。stepId 可选:前端文本形态用它在标题标注「第 N 步」,
    缺省 null 时前端退回通用标题(gateStopEntry 的 stepId 分支)。"""
    return {"t": "gate_stop", "text": text, "suggestions": suggestions or [],
            "stepId": step_id}


def handler_error(source: str, error: str) -> dict:
    return {"t": "handler_error", "source": source, "error": error}
