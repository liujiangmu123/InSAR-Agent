"""insar_mcp:把 InSAR agent 的 HTTP API 包装成 MCP 工具集(官方 SDK,stdio)。

设计要点(对齐 mcp-builder 技能的最佳实践,FastMCP 风格 —— SDK 2.x 更名 MCPServer):
  - 工具名 insar_ 前缀 + 动词开头,避免与宿主里其他 MCP server 撞名;
  - 全部返回结构化 JSON(dict → structuredContent),错误走 ToolError,
    message 自带下一步指引(启动后端 / 换工具 / 轮询);
  - 回合是 NDJSON 流,但 MCP 工具必须快进快出:plan_run 消费到规划结束
    (规划无重计算,秒级),execute_run 只观察受理窗口即断开 —— 后端契约
    保证断开不取消 run,宿主随后用 insar_run_status 轮询(events_hint 明示);
    insar_converse 消费自主循环回合到收束(say/note),消费上限
    INSAR_MCP_CONVERSE_TIMEOUT(默认 180s),超限断开同样不取消回合。

运行:python -m insar_agent.mcp(需可选依赖组 [mcp];后端须已启动)。
"""

from __future__ import annotations

import logging
import sys
import uuid
from typing import Annotated, Any, Awaitable, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from insar_agent.mcp import backend

T = TypeVar("T")

#: 需要整数步骤号做 target 的动作(与 api/app.py _STEP_ACTIONS 同口径,仅作 scope 推断;
#: 动作合法性不在本层预校验 —— 闭集校验错误由后端 /api/actions 返回并原样透传)
_STEP_ACTIONS = frozenset({"RESET", "SKIP", "SET_METHOD", "SET_PARAMS"})

#: run 的终态集合(轮询停止条件)
_TERMINAL = frozenset({"done", "failed", "interrupted"})

# MCPServer 即官方 SDK 2.x 里 FastMCP 的直接后继(同款 tool 装饰器/注解/运行方式)
mcp = MCPServer(
    "insar_mcp",
    instructions=(
        "InSAR 形变处理代理的 MCP 入口。典型流程:insar_create_session 建会话 → "
        "insar_plan_run 用自然语言发起规划(返回计划摘要)→ insar_execute_run 触发执行"
        "(立即返回受理,不等完成)→ insar_run_status 轮询到终态 → insar_get_provenance /"
        " insar_list_figures 取溯源与图件;执行中可用 insar_intervene 干预。"
        "也可用 insar_converse 让代理在一个回合内自主跑多个周期"
        "(搜数据/查环境/定计划;执行仍需用户确认,不会被循环自动触发)。"
        "前置条件:InSAR 后端已启动(python -m insar_agent.api.app,"
        "基址由环境变量 INSAR_API_BASE 指定,默认 http://127.0.0.1:8873)。"
    ),
)


async def _guarded(coro: Awaitable[T]) -> T:
    """后端异常 → ToolError(message 已含行动指引,原样面向宿主 LLM)。"""
    try:
        return await coro
    except backend.BackendError as exc:
        raise ToolError(str(exc)) from exc


# ---------------- 回合流的事件消化 ----------------

def _say_texts(event: dict) -> list[str]:
    """say 事件的 parts 里可能混对象(前端富卡片),只取文本部分。"""
    return [p for p in event.get("parts", []) if isinstance(p, str)]


def _digest_turn(events: list[dict]) -> dict[str, Any]:
    """规划回合的事件流 → 计划摘要素材(问题/提问/叙述/决策点)。"""
    digest: dict[str, Any] = {
        "problems": [], "warnings": [], "messages": [],
        "questions": [], "plan_items": [], "decision_step": None,
    }
    for e in events:
        t = e.get("t")
        if t == "note":
            bucket = "problems" if e.get("tone") == "bad" else "warnings"
            digest[bucket].append(e.get("text", ""))
        elif t == "say":
            digest["messages"].extend(_say_texts(e))
        elif t == "ask":
            digest["questions"].append(
                {"prompt": e.get("prompt", ""), "fields": e.get("fields", [])})
        elif t == "plan":
            digest["plan_items"] = [i.get("text", "") for i in e.get("items", [])]
        elif t == "candidates":
            digest["decision_step"] = e.get("stepId")
    return digest


def _digest_converse(events: list[dict], *, finished: bool = True) -> dict[str, Any]:
    """自主循环回合的事件流 → 返回素材(周期账/结论/工具摘要/确认卡)。

    事件契约(docs/LOOP-CONTRACT.md §1):agent.cycle 每周期一条;say = 正常
    收束,note = 预算耗尽/取消/降级收尾 —— 以最后一个 say/note 为回合终止
    事件(中途的告警 note 不是收尾);tool.end 的 summary 是周期内工具结果。

    finished=False(deadline 截断的流)没有可信的终止事件:最后收到的 say/note
    只是「断开前的最后一条」,把中途告警 note 当收尾会误导宿主(ended_by="note"
    且 truncated=False,P2-9)—— 截断时一律不认终止事件,由调用方如实报 truncated。
    """
    cycles = [{"n": e.get("n"), "action": e.get("action", "")}
              for e in events if e.get("t") == "agent.cycle"]
    says: list[str] = []
    tool_summaries: list[str] = []
    problems: list[str] = []
    questions: list[dict] = []
    for e in events:
        t = e.get("t")
        if t == "say":
            says.extend(_say_texts(e))
        elif t == "tool.end" and e.get("summary"):
            tool_summaries.append(str(e["summary"]))
        elif t == "note" and e.get("tone") == "bad":
            problems.append(e.get("text", ""))
        elif t == "ask":
            questions.append({"prompt": e.get("prompt", ""),
                              "fields": e.get("fields", [])})
    terminal = (next((e for e in reversed(events) if e.get("t") in ("say", "note")),
                     None) if finished else None)
    reply_parts = list(says)
    if terminal is not None and terminal.get("t") == "note":
        reply_parts.append(terminal.get("text", ""))  # 收尾语跟在正文之后
    return {
        "reply": "\n".join(p for p in reply_parts if p),
        "ended_by": terminal.get("t") if terminal else None,
        "cycles": cycles,
        "tool_summaries": tool_summaries,
        "problems": problems,
        "questions": questions,
    }


def _compact_events(events: list[dict], limit: int = 20) -> list[dict]:
    """执行流早期事件的瘦身:去掉日志噪音,只留宿主决策需要的类型。"""
    keep = ("note", "ask", "intervention", "gate_stop", "degrade",
            "step.start", "step.end", "result", "report", "reattach")
    out = [e for e in events if e.get("t") in keep]
    return out[:limit]


async def _state_of(session_id: str, run_id: str | None = None) -> dict:
    params: dict[str, Any] = {"session": session_id}
    if run_id:
        params["run_id"] = run_id
    return await backend.request("GET", "/api/state", params=params)


def _step_row(s: dict) -> dict:
    """/api/state 的步骤行 → 状态矩阵行(去掉 params/指纹等大字段)。"""
    return {
        "id": s["id"], "name": s["name"], "method": s["method"],
        "state": s["state"], "stage": s["stage"], "stale": s["stale"],
        "stale_reason": s.get("staleReason") or "",
        "failure_class": s.get("failureClass") or "",
        "run_ok": s.get("runOk"),
    }


def _run_brief(run: dict) -> dict:
    """runs 表行 → 精简视图(intent/tool_versions 等大字段不出工具面)。"""
    return {
        "run_id": run["run_id"], "session_id": run["session_id"],
        "status": run["status"], "scenario": run.get("scenario"),
        "simulated": bool(run.get("simulated")), "created_at": run.get("created_at"),
        "parent_run_id": run.get("parent_run_id"),
    }


# ---------------- 会话 ----------------

@mcp.tool(
    name="insar_list_sessions",
    annotations=ToolAnnotations(title="列出 InSAR 会话", read_only_hint=True,
                                idempotent_hint=True, open_world_hint=False),
)
async def insar_list_sessions(
    include_archived: Annotated[bool, Field(
        description="是否包含已归档(软删除)的会话,默认不含")] = False,
) -> dict:
    """列出 InSAR 后端的全部会话。

    使用时机:需要找到既有会话继续工作,或确认某个 session_id 是否存在时;
    若要开始全新分析,直接用 insar_create_session。

    返回 JSON:{count, sessions: [{session_id, name, mode, created_at, ...}]}。
    """
    async def impl() -> dict:
        rows = await backend.request("GET", "/api/sessions",
                                     params={"include_archived": include_archived})
        return {"count": len(rows), "sessions": rows}
    return await _guarded(impl())


@mcp.tool(
    name="insar_create_session",
    annotations=ToolAnnotations(title="新建 InSAR 会话", read_only_hint=False,
                                destructive_hint=False, idempotent_hint=False,
                                open_world_hint=False),
)
async def insar_create_session(
    name: Annotated[str, Field(description="会话显示名(1-80 字符,支持中文)",
                               min_length=1, max_length=80)],
    scenario: Annotated[str | None, Field(
        description="可选的场景提示,如 quake(同震)/ permafrost(冻土)/ "
                    "landslide(滑坡)/ stripmap_coseismic(条带同震)。规划时会并入"
                    "意图文本帮助场景识别;意图文本本身已含场景关键词时可省略")] = None,
    session_id: Annotated[str | None, Field(
        description="可选的自定义会话 id(将成为工作区目录名:≤64 字符,"
                    "不含路径分隔符);缺省自动生成 mcp-<hex>")] = None,
) -> dict:
    """在 InSAR 后端新建一个会话(后续规划/执行的容器)。

    使用时机:开始一次新的 InSAR 形变分析之前;一个会话可承载多个 run
    (重规划/fork 都在会话内)。已有会话请用 insar_list_sessions 查找复用。

    返回 JSON:{session: {session_id, name, ...}, scenario_hint, next}。
    下一步:insar_plan_run(session_id, intent_text) 发起规划。
    """
    async def impl() -> dict:
        sid = session_id or f"mcp-{uuid.uuid4().hex[:12]}"
        row = await backend.request("POST", "/api/sessions",
                                    json={"id": sid, "name": name})
        if scenario:
            _session_scenarios[sid] = scenario
        return {
            "session": row, "scenario_hint": scenario,
            "next": "调用 insar_plan_run(session_id, intent_text) 发起规划",
        }
    return await _guarded(impl())


#: 会话的场景提示(create_session 记录,plan_run 并入意图文本;仅本进程内存)
_session_scenarios: dict[str, str] = {}


# ---------------- 规划 / 执行 ----------------

@mcp.tool(
    name="insar_plan_run",
    annotations=ToolAnnotations(title="发起 InSAR 规划回合", read_only_hint=False,
                                destructive_hint=False, idempotent_hint=False,
                                open_world_hint=False),
)
async def insar_plan_run(
    session_id: Annotated[str, Field(description="会话 id(来自 insar_create_session"
                                                 " / insar_list_sessions)")],
    intent_text: Annotated[str, Field(
        description="自然语言的处理意图,建议含场景/区域/时间线索,"
                    "如「Ridgecrest 地震同震形变分析」「青藏高原冻土季节冻融监测」",
        min_length=1)],
) -> dict:
    """向会话发起一个规划回合:意图识别 → 环境探测 → 生成执行计划(不执行)。

    使用时机:执行前必须先规划;改意图/换场景时重新调用即可(同场景会复用
    既有 run)。规划只做探测与选型,没有重计算,通常数秒内返回。

    返回 JSON(计划摘要):
      {run_id, run_status, simulated, scenario, steps: [{id,name,method,state}],
       decision_step, plan_items, messages, problems, warnings, questions,
       estimated_minutes, estimate_basis, next}
    - questions 非空表示意图缺信息(如场景无法识别):按提示补充 intent_text
      后重试,此时 run_id 为 null;
    - problems 非空表示计划存在可行性问题(缺引擎等),不要直接执行;
    - simulated=true 表示引擎缺失、执行将是模拟演示(证据级别封顶);
    - estimated_minutes 无历史依据时为 null(纪律:不编造时长)。
    下一步:insar_execute_run(session_id, run_id) 触发执行。
    """
    async def impl() -> dict:
        text = intent_text
        hint = _session_scenarios.get(session_id)
        if hint and hint.lower() not in text.lower():
            text = f"{text}(场景:{hint})"
        events, finished = await backend.consume_ndjson(
            "/api/turn", {"session": session_id, "text": text},
            deadline_s=backend.plan_deadline())
        digest = _digest_turn(events)

        # ask(缺信息)路径不产生新 run:/api/state 会落到旧 run,不能采信
        run: dict | None = None
        steps: list[dict] = []
        if not digest["questions"]:
            state = await _state_of(session_id)
            run = state.get("run")
            steps = state.get("steps", [])
            if run:
                backend.remember_run(run["run_id"], session_id)

        if digest["questions"]:
            next_hint = ("意图缺信息,后端无法定场景:把场景关键词补进 intent_text "
                         "重新调用 insar_plan_run(或 insar_create_session 时给 scenario)")
        elif digest["problems"]:
            next_hint = "计划存在可行性问题(见 problems),解决后重新规划;不要直接执行"
        else:
            next_hint = "计划就绪:调用 insar_execute_run(session_id, run_id) 触发执行"
        return {
            "session_id": session_id,
            "run_id": run["run_id"] if run else None,
            "run_status": run["status"] if run else None,
            "simulated": bool(run.get("simulated")) if run else None,
            "scenario": run.get("scenario") if run else None,
            "steps": [{"id": s["id"], "name": s["name"], "method": s["method"],
                       "state": s["state"]} for s in steps],
            "decision_step": digest["decision_step"],
            "plan_items": digest["plan_items"],
            "messages": digest["messages"],
            "problems": digest["problems"],
            "warnings": digest["warnings"],
            "questions": digest["questions"],
            # §7.5 纪律:无历史耗时依据不编数;真实时长以 run_status 实测为准
            "estimated_minutes": None,
            "estimate_basis": "no_history",
            "stream_finished": finished,
            "next": next_hint,
        }
    return await _guarded(impl())


@mcp.tool(
    name="insar_execute_run",
    annotations=ToolAnnotations(title="触发 InSAR 执行回合", read_only_hint=False,
                                destructive_hint=False, idempotent_hint=False,
                                open_world_hint=False),
)
async def insar_execute_run(
    session_id: Annotated[str, Field(description="会话 id")],
    run_id: Annotated[str | None, Field(
        description="要执行的 run(来自 insar_plan_run);缺省取该会话最近的 run")] = None,
    step_ids: Annotated[list[int] | None, Field(
        description="可选:只执行这些步骤号(如失败步 RESET 后重跑);"
                    "缺省执行全部待跑步骤")] = None,
) -> dict:
    """触发 run 的执行回合:立即返回受理结果与 run_id,不阻塞等待完成。

    使用时机:insar_plan_run 返回的计划无 problems 之后。执行在后端推进
    (模拟或真实由后端环境决定),本工具只观察一个受理窗口(默认 3s,
    INSAR_MCP_ACCEPT_WINDOW 可调)即断开事件流 —— 断开不取消 run。

    返回 JSON:
      {accepted, run_id, rejected_reason, started_step, run_status,
       early_events, stream_detached, events_hint}
    - accepted=false 时 rejected_reason 给出后端拒绝原因(无计划/计划带病/
      运行锁被占等);
    - early_events 是受理窗口内的关键事件(note/step.start/result 等)。
    下一步:用 insar_run_status(run_id) 轮询步骤矩阵直到终态
    (done/failed/interrupted);中途干预用 insar_intervene。
    """
    async def impl() -> dict:
        state = await _state_of(session_id, run_id)
        run = state.get("run")
        if run is None:
            raise backend.BackendError(
                f"会话 {session_id} 没有可执行的计划:先调用 insar_plan_run 生成计划"
                + (f"(指定的 run {run_id!r} 不存在)" if run_id else ""))
        rid = run["run_id"]
        backend.remember_run(rid, session_id)

        body: dict[str, Any] = {"session": session_id, "run_id": rid}
        if step_ids:
            body["step_ids"] = step_ids

        def stop(e: dict) -> bool:
            # 受理判据:见到首个 step.start(已开跑)或坏消息即可断开;
            # result 覆盖「窗口内就跑完」的模拟链
            return (e.get("t") in ("step.start", "result", "gate_stop")
                    or (e.get("t") == "note" and e.get("tone") == "bad"))

        events, finished = await backend.consume_ndjson(
            "/api/pipeline", body, deadline_s=backend.accept_window(), stop=stop)

        started = next((e.get("stepId") for e in events if e.get("t") == "step.start"),
                       None)
        completed = any(e.get("t") == "result" for e in events)
        bad = next((e.get("text", "") for e in events
                    if e.get("t") == "note" and e.get("tone") == "bad"), None)
        # 拒绝的形态:没开跑就收到坏消息(开跑后的 bad note 属于步骤失败,
        # 那是 run 自己的事,受理本身成立)
        accepted = started is not None or completed or bad is None
        snapshot = await _state_of(session_id, rid)
        status = (snapshot.get("run") or {}).get("status")
        return {
            "accepted": accepted,
            "run_id": rid,
            "rejected_reason": None if accepted else bad,
            "started_step": started,
            "completed_in_window": completed,
            "run_status": status,
            "early_events": _compact_events(events),
            "stream_detached": not finished,
            "events_hint": ("执行在后端继续(断开事件流不会取消 run):"
                            "用 insar_run_status(run_id) 轮询到终态 "
                            "done/failed/interrupted;需要暂停/取消/改参用 insar_intervene"),
        }
    return await _guarded(impl())


@mcp.tool(
    name="insar_run_status",
    annotations=ToolAnnotations(title="查询 InSAR run 状态", read_only_hint=True,
                                idempotent_hint=True, open_world_hint=False),
)
async def insar_run_status(
    run_id: Annotated[str, Field(description="run id(来自 insar_plan_run /"
                                             " insar_execute_run)")],
    session_id: Annotated[str | None, Field(
        description="可选:run 的归属会话;缺省自动解析(本进程发起的 run 免查)")] = None,
) -> dict:
    """查询 run 的步骤状态矩阵、当前阶段与失败摘要(执行的轮询入口)。

    使用时机:insar_execute_run 受理之后周期性调用,直到 terminal=true;
    也可用于恢复上下文(会话里最近的 run 进展到哪了)。

    返回 JSON:
      {run: {run_id,status,scenario,simulated,...}, terminal, counts,
       current: {id,name,stage}|null, failures: [{id,name,failure_class}],
       steps: [{id,name,method,state,stage,stale,failure_class,run_ok}], hint}
    - status:ready/running/paused/done/failed/interrupted;
    - counts 是各 state 的步骤数;current 是正在跑的步骤(含五阶段 stage);
    - failures 列出失败步骤与失败类别(处置建议见 hint)。
    """
    async def impl() -> dict:
        sid = await backend.session_of_run(run_id, session_id)
        state = await _state_of(sid, run_id)
        run = state.get("run")
        if run is None:
            raise backend.BackendError(
                f"run {run_id!r} 在会话 {sid} 中不存在;用 insar_plan_run 重新生成计划")
        steps = [_step_row(s) for s in state.get("steps", [])]
        counts: dict[str, int] = {}
        for s in steps:
            counts[s["state"]] = counts.get(s["state"], 0) + 1
        current = next(({"id": s["id"], "name": s["name"], "stage": s["stage"]}
                        for s in steps if s["state"] == "running"), None)
        failures = [{"id": s["id"], "name": s["name"],
                     "failure_class": s["failure_class"]}
                    for s in steps if s["state"] == "failed"]
        status = run["status"]
        terminal = status in _TERMINAL
        if not terminal:
            hint = "run 仍在推进:稍后再次调用 insar_run_status 轮询"
        elif status == "done":
            hint = ("run 已完成:insar_get_provenance 取溯源文档,"
                    "insar_list_figures 取图件清单")
        elif status == "failed":
            hint = ("run 失败:看 failures 的失败类别;可 insar_intervene "
                    "RESET/SET_PARAMS/SET_METHOD 处置后 insar_execute_run 重跑")
        else:
            hint = ("run 已中断(取消/环境停止):insar_execute_run 可从断点续跑,"
                    "已完成步骤保留")
        return {"run": _run_brief(run), "terminal": terminal, "counts": counts,
                "current": current, "failures": failures, "steps": steps,
                "hint": hint}
    return await _guarded(impl())


# ---------------- 自主循环回合(契约 §10) ----------------

@mcp.tool(
    name="insar_converse",
    annotations=ToolAnnotations(title="发起 InSAR 自主循环回合", read_only_hint=False,
                                destructive_hint=False, idempotent_hint=False,
                                open_world_hint=False),
)
async def insar_converse(
    session_id: Annotated[str, Field(description="会话 id(来自 insar_create_session"
                                                 " / insar_list_sessions)")],
    text: Annotated[str, Field(
        description="自然语言的回合目标,如「检查 Ridgecrest 的数据情况并把处理计划准备好」",
        min_length=1)],
    max_cycles: Annotated[int, Field(
        description="本回合的周期上限(1-12,默认 6):每周期一个白名单动作,"
                    "耗尽则如实收尾(note)",
        ge=1, le=12)] = 6,
) -> dict:
    """发起自主循环回合:代理逐周期自主选动作(搜数据/查环境/定计划/调参…)直到收束。

    使用时机:想让代理自主推进多步侦察/筹备工作(盘点数据 → 探测环境 → 生成
    计划)而不想逐工具编排时;单步规划直接用 insar_plan_run 即可。执行绝不会
    被循环自动触发:execute 动作只产生确认卡(见 questions),真正执行仍走
    insar_execute_run。需要后端 2026-08-14 及以上版本(缺端点会报错并给升级指引)。

    返回 JSON:
      {session_id, reply, ended_by, cycles: [{n, action}], tool_summaries,
       problems, questions, truncated, stream_finished, next}
    - reply 是回合结论(say 正常收束 / note 收尾语);ended_by = say|note|null;
    - cycles 是逐周期账目(第 n 周期做了什么动作),tool_summaries 是周期内
      工具执行的结果摘要,problems 是坏消息(tone=bad 的 note),
      questions 是确认卡/提问(把 prompt 转述给用户决策);
    - truncated=true 表示消费达上限(INSAR_MCP_CONVERSE_TIMEOUT,默认 180s)
      被打断且未见结论 —— 回合在后端继续推进(断开不取消),按 next 指引轮询。
    """
    async def impl() -> dict:
        events, finished = await backend.converse(session_id, text, max_cycles)
        digest = _digest_converse(events, finished=finished)
        truncated = not finished and digest["ended_by"] is None
        if truncated:
            next_hint = ("回合超出消费上限被断开,但仍在后端继续推进(断开不取消):"
                         "稍后用 insar_run_status(run_id) 轮询执行进展(run_id 见 "
                         "insar_plan_run / insar_execute_run 的返回),或调大环境变量 "
                         "INSAR_MCP_CONVERSE_TIMEOUT 后重试")
        elif digest["questions"]:
            next_hint = ("回合产生了确认卡/提问(见 questions):把内容转述给用户决策;"
                         "执行永远不会未经用户确认自动开始")
        elif digest["ended_by"] == "note":
            next_hint = ("回合以 note 收尾(预算耗尽/取消/降级,见 reply):"
                         "可拆小目标或提高 max_cycles 后重试")
        elif digest["ended_by"] == "say":
            next_hint = ("回合正常收束(结论见 reply);需要执行计划时走 "
                         "insar_execute_run,查看运行进展用 insar_run_status")
        else:
            next_hint = ("回合结束但未见 say/note 终止事件(后端异常?):"
                         "检查后端日志,或改用 insar_plan_run 的分步流程")
        return {
            "session_id": session_id,
            **digest,
            "truncated": truncated,
            "stream_finished": finished,
            "next": next_hint,
        }
    return await _guarded(impl())


# ---------------- 干预 ----------------

@mcp.tool(
    name="insar_intervene",
    annotations=ToolAnnotations(title="干预 InSAR run", read_only_hint=False,
                                destructive_hint=True, idempotent_hint=False,
                                open_world_hint=False),
)
async def insar_intervene(
    run_id: Annotated[str, Field(description="目标 run id")],
    action: Annotated[str, Field(
        description="动作(后端闭集):PAUSE(暂停)/ PLAY 或 RESUME(恢复)/ "
                    "KILL(取消,即时)/ RESET(复位步骤,下游标脏)/ SKIP(跳过步骤)/ "
                    "SET_METHOD(换方法)/ SET_PARAMS(改参数)。"
                    "闭集外的动作会被后端 400 拒绝并返回可用清单")],
    target: Annotated[int | str | None, Field(
        description="步骤级动作(RESET/SKIP/SET_METHOD/SET_PARAMS)必填:步骤号;"
                    "run 级动作(PAUSE/PLAY/KILL)忽略")] = None,
    payload: Annotated[dict | None, Field(
        description="动作参数:SET_METHOD 需 {\"method\": \"<方法 id>\"};"
                    "SET_PARAMS 需 {\"params\": {参数名: 值}}(直接给参数字典也可,"
                    "会自动包一层);其余动作无 payload")] = None,
    deliver_as: Annotated[str, Field(
        description="投递语义:steer(当前步结束后生效,默认)/ follow_up"
                    "(run 结束后生效)/ next_run(下次规划时生效)")] = "steer",
    session_id: Annotated[str | None, Field(
        description="可选:run 的归属会话;缺省自动解析")] = None,
) -> dict:
    """向 run 的干预队列投递一个动作(暂停/恢复/取消/复位/跳过/换方法/改参数)。

    使用时机:执行中需要改变行为时 —— 参数不合适(SET_PARAMS)、方法要换
    (SET_METHOD)、失败步骤要重跑(RESET 后 insar_execute_run)、要停
    (PAUSE/KILL)。动作入队即校验:未知动作/未知方法/参数越界都会被后端
    400 拒绝,错误信息原样透传(含可用候选清单)。

    返回 JSON:{accepted, action_id, action, deliver_as, hint}。
    生效时机:KILL 即时;steer 在步骤间隙;follow_up 在 run 收尾;
    next_run 在下次规划 —— 之后用 insar_run_status 确认生效结果。
    """
    async def impl() -> dict:
        sid = await backend.session_of_run(run_id, session_id)
        act = action.strip().upper()
        if act == "RESUME":  # 常用别名:后端闭集里恢复叫 PLAY
            act = "PLAY"
        body_payload = dict(payload or {})
        if act == "SET_PARAMS" and body_payload and "params" not in body_payload:
            body_payload = {"params": body_payload}  # 容错:直接给参数字典的形态
        resp = await backend.request("POST", "/api/actions", json={
            "session": sid, "run_id": run_id,
            "scope": "step" if act in _STEP_ACTIONS else "run",
            "target": str(target) if target is not None else run_id,
            "action": act, "payload": body_payload, "deliver_as": deliver_as,
        })
        return {
            "accepted": bool(resp.get("accepted")),
            "action_id": resp.get("id"),
            "action": act, "deliver_as": deliver_as,
            "hint": ("动作已入队(KILL 即时,steer 步间生效,follow_up run 收尾,"
                     "next_run 下次规划);用 insar_run_status 确认生效结果"),
        }
    return await _guarded(impl())


# ---------------- 溯源 / 图件 ----------------

def _truncate_doc(node: Any, max_chars: int, path: str,
                  hits: list[str]) -> Any:
    """递归截断超长字符串与超长列表,记录被截断字段的路径。"""
    if isinstance(node, str):
        if len(node) > max_chars:
            hits.append(path)
            return node[:max_chars] + f"…[已截断:原长 {len(node)} 字符]"
        return node
    if isinstance(node, list):
        items = node
        if len(items) > 100:
            hits.append(f"{path}[](原 {len(items)} 项,保留前 100)")
            items = items[:100]
        return [_truncate_doc(v, max_chars, f"{path}[{i}]", hits)
                for i, v in enumerate(items)]
    if isinstance(node, dict):
        return {k: _truncate_doc(v, max_chars, f"{path}.{k}" if path else str(k), hits)
                for k, v in node.items()}
    return node


@mcp.tool(
    name="insar_get_provenance",
    annotations=ToolAnnotations(title="导出 InSAR 溯源文档", read_only_hint=True,
                                idempotent_hint=True, open_world_hint=False),
)
async def insar_get_provenance(
    run_id: Annotated[str, Field(description="run id")],
    session_id: Annotated[str | None, Field(
        description="可选:run 的归属会话;缺省自动解析")] = None,
    max_field_chars: Annotated[int, Field(
        description="单个字符串字段的长度上限(超出截断并注明),默认 2000",
        ge=200, le=20000)] = 2000,
) -> dict:
    """导出 run 的 provenance(溯源)文档:方法/参数/指纹/QA/工具版本的完整台账。

    使用时机:run 到达终态后取可复现记录;向用户汇报「这次处理到底做了什么」。
    超长字段会被截断并在 truncated_fields 里注明(防止撑爆宿主上下文);
    需要完整原文时直接访问后端 GET /api/provenance。

    返回 JSON:{run_id, provenance: {...}, truncated, truncated_fields, note}。
    """
    async def impl() -> dict:
        sid = await backend.session_of_run(run_id, session_id)
        doc = await backend.request("GET", "/api/provenance",
                                    params={"session": sid, "run_id": run_id})
        hits: list[str] = []
        safe = _truncate_doc(doc, max_field_chars, "", hits)
        return {
            "run_id": run_id,
            "provenance": safe,
            "truncated": bool(hits),
            "truncated_fields": hits[:20],
            "note": ("超长字段已截断(见 truncated_fields);完整原文见后端 "
                     "GET /api/provenance") if hits else "",
        }
    return await _guarded(impl())


@mcp.tool(
    name="insar_list_figures",
    annotations=ToolAnnotations(title="列出 InSAR 图件", read_only_hint=True,
                                idempotent_hint=True, open_world_hint=False),
)
async def insar_list_figures(
    run_id: Annotated[str, Field(description="run id")],
    session_id: Annotated[str | None, Field(
        description="可选:run 的归属会话;缺省自动解析")] = None,
) -> dict:
    """列出 run 产出的图像产物清单(速度场/直方图等 PNG/JPG)。

    使用时机:run 完成(或部分完成)后要看结果图、要给用户贴图时。
    返回的 url 是后端 HTTP 地址(已拼上 INSAR_API_BASE),可直接 GET 取
    图像本体;thumb_url/full_url 分别是缩略档与原图。

    返回 JSON:{run_id, count, figures: [{name, step, kind, size, mtime,
    url, thumb_url, full_url, meta?}], hint}。模拟 run 可能没有图件(count=0)。
    """
    async def impl() -> dict:
        sid = await backend.session_of_run(run_id, session_id)
        data = await backend.request("GET", "/api/figures",
                                     params={"session": sid, "run_id": run_id})
        base = backend.api_base()
        figures = [{
            "name": f["name"], "step": f["step"], "kind": f.get("kind"),
            "size": f.get("size"), "mtime": f.get("mtime"),
            "url": base + f["url"],
            "thumb_url": base + f["thumbUrl"],
            "full_url": base + f["fullUrl"],
            **({"meta": f["meta"]} if "meta" in f else {}),
        } for f in data.get("figures", [])]
        return {
            "run_id": data.get("run"), "count": len(figures), "figures": figures,
            "hint": "url 可直接 HTTP GET 获取图像本体(后端须对宿主可达)",
        }
    return await _guarded(impl())


# ---------------- 入口 ----------------

def main() -> None:
    """stdio 运行入口(python -m insar_agent.mcp / insar-agent-mcp)。

    stdio 纪律:stdout 只属于 JSON-RPC 帧,日志一律走 stderr。
    """
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    mcp.run()  # 默认 stdio transport


if __name__ == "__main__":
    main()
