"""目标驱动的周期预算:轻量探索可自动延长,重型执行仍等人批准。

与 Codex / Claude Code 的差别不在「放开任意命令」,而在终止条件:
停机依据是目标是否满足 / 是否碰到审批门 / 是否死锁,不是计数器到 6。
"""

from __future__ import annotations

#: 轻量探索层:只读、检索、受控安装/探针。可自动加预算。
LIGHT_ACTIONS = frozenset({
    "search_data", "inspect_file", "check_env", "list_data", "list_files",
    "status", "thinking", "install_engine", "learn_tool", "search_docs",
    "probe_scratch",
})

#: 重型执行层:只出确认卡,绝不因预算延长而自启流水线。
HEAVY_ACTIONS = frozenset({"execute"})

#: 每次目标未完成自动追加的周期数。
ADAPTIVE_CHUNK = 8

_WORK_HINTS = ("处理", "分析", "形变", "地震", "滑坡", "计划", "规划", "检索",
               "同震", "时序", "干涉")
_ENV_HINTS = ("环境", "安装", "缺什么", "引擎", "依赖")


def goal_is_work(goal: str) -> bool:
    return any(k in (goal or "") for k in _WORK_HINTS)


def goal_is_env_only(goal: str) -> bool:
    t = (goal or "").strip()
    return any(k in t for k in _ENV_HINTS) and not goal_is_work(t)


def cycle_kinds(cycles_summary: list[str]) -> list[str]:
    kinds: list[str] = []
    for line in cycles_summary:
        body = line.split("]", 1)[-1].strip()
        kinds.append(body.split()[0].split(":")[0] if body else "")
    return kinds


def should_extend_budget(goal: str, cycles_summary: list[str], *,
                         budget: int, hard_cap: int) -> str | None:
    """预算用尽时是否再给一段时间。返回延长原因,或 None=按上限收束。"""
    if budget >= hard_cap:
        return None
    kinds = [k for k in cycle_kinds(cycles_summary) if k and k != "收束被拒"]
    if not kinds:
        return None
    if any(k in HEAVY_ACTIONS for k in kinds):
        return None
    if not goal_is_work(goal):
        return None
    if "plan" not in kinds:
        return "分析任务尚未完成规划"
    last = kinds[-1]
    if last in LIGHT_ACTIONS or last in {"plan", "set_params", "set_method"}:
        return "仍在筹备/探索,尚未请求执行"
    return None
