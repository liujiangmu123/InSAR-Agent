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
                except Exception:
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


def intervention(text: str, affected: list[int] | None = None) -> dict:
    return {"t": "intervention", "text": text, "affected": affected or []}


def degrade(text: str, evidence_before: str, evidence_after: str) -> dict:
    return {"t": "degrade", "text": text,
            "evidenceBefore": evidence_before, "evidenceAfter": evidence_after}


def gate_stop(text: str, suggestions: list[str] | None = None) -> dict:
    return {"t": "gate_stop", "text": text, "suggestions": suggestions or []}


def handler_error(source: str, error: str) -> dict:
    return {"t": "handler_error", "source": source, "error": error}
