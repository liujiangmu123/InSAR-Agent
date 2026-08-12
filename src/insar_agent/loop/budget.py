"""上下文预算(AGENT-DESIGN §3.3 约束四)。

决策请求本来就不带历史(结构上消除膨胀);这里只管 intent/narrate 用的对话历史:
字符预算裁剪(agentic-swmm prompts.py:182-200 的做法)+ 消息边界切割
(吸收 pi compaction 切点纪律:绝不从消息中间切,absorb 自 compaction.md:109-117)。
"""

from __future__ import annotations


def trim_history(messages: list[dict], *, max_chars: int = 8000) -> list[dict]:
    """保留最新消息,总字符数不超预算;整条消息为最小单位,绝不截半条。"""
    kept: list[dict] = []
    used = 0
    for msg in reversed(messages):
        size = len(str(msg.get("content", "")))
        if used + size > max_chars and kept:
            break
        kept.append(msg)
        used += size
    return list(reversed(kept))
