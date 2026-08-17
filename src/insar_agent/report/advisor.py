"""下一步决策建议引擎:run 终态 → 可点击的建议卡(AI 不只执行,还说下一步)。

纪律:
  - 建议由确定性规则生成,LLM 不参与决策 —— provider 只润色 why 字段的措辞,
    且数字校验失败逐条回退规则文案(防幻觉口径同 brain.facade.narrate,
    这里更严:数字集合必须完全一致,新增或丢失任何数字都不接受);
  - 失败处置与 core/failures.DISPOSITIONS 同源(直接引用,不另造一套);
  - 证据级语义对照 audit/ladder:done 但 runnable/模拟 = 偏低,诚实说明
    差在哪一级、补什么,绝不建议「导出复现包给同行」这类越级动作;
  - 纯读:不改任何 run/step 状态;action 只是「可执行的建议」,执行权在用户。

action 三种形态(按 kind 分派):
  chat_prefill  预填话术进输入框(不发送)      {"kind","text"}
  api_action    调用现成端点(如显式列表续跑)   {"kind","method","endpoint","body"|"params",…}
  open_tab      切换右侧 dock 面板(可带步骤号) {"kind","tab","step"?}
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from insar_agent.audit.contract import load_contract
from insar_agent.audit.ladder import LADDER, compute_evidence
from insar_agent.brain.provider import BrainUnavailable
from insar_agent.core.failures import DISPOSITIONS, FailureClass
from insar_agent.core.store import StepRow, Store

#: run 的终态闭集(loop/driver set_run_status 的收尾语义;其余状态不出建议)
TERMINAL_STATUSES = ("done", "failed", "interrupted")

#: 交叉验证/QA 复核所在步骤(registry PIPELINE 第 11 步「质检」)
QA_STEP_ID = 11

#: 敏感性重跑建议引用的关键参数(按优先序取第一个在计划中出现的;
#: min_coherence 优先 —— 解缠掩膜阈值对覆盖率/可靠性的影响最直观)
_KEY_PARAMS = ("min_coherence", "alpha", "max_temporal_baseline", "corr_threshold")

#: 证据阶梯逐级达成要求(语义与 audit/ladder.compute_evidence 的判定一一对应,
#: 措辞对齐 report/methods._LADDER_REQ —— 两处同源于 ladder,不各说各话)
_LADDER_REQ = {
    "checked": "全部步骤执行完成且 run_ok 通过",
    "audited": "产物指纹齐备且指标全部重解析一致(审计完整)",
    "calibrated": "GNSS/水准外部比对指标(gnss_rmse_mm)入账",
    "validated": "PS/SBAS 双链交叉验证达契约阈值(且阈值完成标定,非 PENDING)",
    "publishable": "跨环境复现记录与证据边界表",
}

#: 待跑步骤口径(与 loop/driver._waiting_steps 一致;failed 单列 —— 只有
#: 显式列表才复位失败步,这是「从断点续跑」必须带上失败步的原因)
_WAITING_STATES = ("pending", "stale", "interrupted", "orphaned", "running")


# ---------------------------------------------------------------------------
# action 构造小工具(闭集形态,前端与测试共同的契约)
# ---------------------------------------------------------------------------

def _chat(text: str) -> dict:
    return {"kind": "chat_prefill", "text": text}


def _api(method: str, endpoint: str, *, body: dict | None = None,
         params: dict | None = None, download: bool = False) -> dict:
    action: dict = {"kind": "api_action", "method": method, "endpoint": endpoint}
    if body is not None:
        action["body"] = body
    if params is not None:
        action["params"] = params
    if download:
        action["download"] = True
    return action


def _tab(tab: str, step: int | None = None) -> dict:
    action: dict = {"kind": "open_tab", "tab": tab}
    if step is not None:
        action["step"] = step
    return action


def _sg(sid: str, title: str, why: str, action: dict) -> dict:
    return {"id": sid, "title": title, "why": why, "action": action}


# ---------------------------------------------------------------------------
# 规则分支:done(达标 / 偏低)、failed、interrupted
# ---------------------------------------------------------------------------

def _find_key_param(steps: list[StepRow]) -> tuple[int, str, object] | None:
    """按 _KEY_PARAMS 优先序找第一个在计划中出现的关键参数(步骤号,名,当前值)。"""
    for name in _KEY_PARAMS:
        for s in steps:
            if name in (s.params or {}):
                return s.step_id, name, s.params[name]
    return None


def _suggest_done(run: dict, steps: list[StepRow],
                  available_routes: set[str]) -> list[dict]:
    """done + 证据级达标:复核 → 成文 → 交付 → 稳健性,四个自然的下一步。"""
    session, run_id = run["session_id"], run["run_id"]
    out: list[dict] = []

    # ① 交叉验证/QA 复核 —— 仅当第 11 步未跑(未进计划,或进了计划没到终态)
    qa = next((s for s in steps if s.step_id == QA_STEP_ID), None)
    if qa is None or qa.state not in ("done", "skipped"):
        why = (f"第 {QA_STEP_ID} 步质检尚未完成:PS/SBAS 双链交叉验证是三种质检里"
               "证据强度最高的一档(crossval > loop_closure > coherence_mask),"
               "不补跑则 validated 级无从谈起")
        if qa is not None:
            action = _api("POST", "/api/pipeline",
                          body={"session": session, "run_id": run_id,
                                "step_ids": [QA_STEP_ID]})
        else:
            action = _chat(f"为当前 run 补跑交叉验证/QA 复核(第 {QA_STEP_ID} 步"
                           "crossval_ps_sbas):当前计划里没有这一步,请规划补充执行")
        out.append(_sg("qa-crossval", "补跑交叉验证 / QA 复核", why, action))

    # ② 论文方法草稿 —— 运行时探测 /api/report/draft 是否存在,不硬依赖
    if "/api/report/draft" in available_routes:
        out.append(_sg(
            "report-draft", "生成论文方法草稿",
            "账本(provenance)已随 done 落盘:方法章节可自动成文,"
            "正文数字均可回溯到产物与命令轨迹,LLM 只润色措辞且有数字反幻觉护栏",
            _api("GET", "/api/report/draft",
                 params={"session": session, "run_id": run_id}, download=True)))

    # ③ 导出复现包 —— run done 是 /api/repro-bundle 的前置(半成品不出包)
    out.append(_sg(
        "repro-bundle", "导出复现包(zip)",
        "provenance / run.sh / methods.md / qa.json / 图件与 sha256 清单打包,"
        "交给同行或审稿人即可脱离本机复现",
        _api("GET", "/api/repro-bundle",
             params={"session": session, "run_id": run_id}, download=True)))

    # ④ 参数敏感性重跑 —— 引用当前关键参数值,fork 复用未受影响步骤
    key = _find_key_param(steps)
    if key is not None:
        sid, name, value = key
        text = (f"基于当前 run 做参数敏感性重跑:fork 一个新 run,调整第 {sid} 步"
                f"的 {name}(当前 {value}),对比关键 QA 指标与最终产物的差异")
        why = (f"单一参数组合的结果无法区分真实信号与处理伪影;当前第 {sid} 步 "
               f"{name}={value},fork 重跑只重算受影响段,是低成本的稳健性检查")
    else:
        text = ("基于当前 run 做参数敏感性重跑:fork 一个新 run,"
                "调整一个关键参数并对比关键 QA 指标的差异")
        why = ("单一参数组合的结果无法区分真实信号与处理伪影;"
               "fork 重跑只重算受影响段,是低成本的稳健性检查")
    out.append(_sg("sensitivity-rerun", "换参数敏感性重跑", why, _chat(text)))
    return out


def _suggest_low_evidence(run: dict, evidence) -> list[dict]:
    """done 但证据级偏低(runnable / 模拟):诚实说明差在哪一级、补什么。"""
    simulated = bool(run.get("simulated"))
    bits: list[str] = []
    if simulated:
        bits.append("本次为模拟执行(引擎缺失下的演示),阶梯封顶 runnable,演示不构成证据")
    bits.extend(str(r) for r in evidence.reasons)
    if evidence.ceiling_reason and evidence.ceiling_reason not in ";".join(bits):
        bits.append(f"封顶原因:{evidence.ceiling_reason}")
    nxt = LADDER[evidence.level_index + 1] if evidence.level_index + 1 < len(LADDER) else None
    gap = ";".join(bits) or "证据判定未给出具体原因"
    why = (f"当前证据级 {evidence.level}(阶梯 {evidence.level_index + 1}/{len(LADDER)})。"
           f"{gap}")
    if nxt:
        why += f"。升到 {nxt} 需:{_LADDER_REQ.get(nxt, nxt)}"
    out = [_sg("evidence-gap", f"证据级仅 {evidence.level}:先补可信度再谈结论",
               why, _tab("audit"))]
    if simulated:
        out.append(_sg(
            "env-real-rerun", "检查引擎环境,换真实链重跑",
            "环境面板列出缺失引擎与候选收窄原因;补齐引擎后按同一计划重跑,"
            "证据级才能进入 checked 及以上",
            _tab("env")))
    else:
        out.append(_sg(
            "evidence-fix", "让 Agent 给出补齐方案",
            "证据缺口已在账本里逐条列出;交给 Agent 逐条对照补齐,不必人工翻账本",
            _chat(f"当前 run 证据级停在 {evidence.level},原因:{gap}。"
                  "请给出逐条补齐方案并执行")))
    return out


def _suggest_failed(run: dict, steps: list[StepRow]) -> tuple[list[dict], str]:
    """failed:按 failure_class 给处置(与 DISPOSITIONS 同源)+ 断点续跑 + 技能文档。"""
    session, run_id = run["session_id"], run["run_id"]
    failed = [s for s in steps if s.state == "failed"]
    primary = failed[0] if failed else None
    fc_raw = (primary.failure_class or "") if primary else ""
    fc = (FailureClass(fc_raw) if fc_raw in FailureClass._value2member_map_
          else FailureClass.UNKNOWN)
    disp = DISPOSITIONS[fc]
    note = str(disp.get("note", "人工介入"))
    sid = primary.step_id if primary else None
    sname = primary.name if primary else ""

    out: list[dict] = []
    # ① 处置建议:文案直接取 DISPOSITIONS(同源守护有测试);可自动重试的类
    #   给现成端点(显式列表 = RESET+续跑),需人工决策的类预填话术交给 Agent
    where = f"第 {sid} 步「{sname}」" if sid is not None else "本次运行"
    why = f"{where}失败,类别 {fc.value};预定义处置:{note}"
    if disp.get("auto") in ("retry", "retry_reduced") and sid is not None:
        action = _api("POST", "/api/pipeline",
                      body={"session": session, "run_id": run_id, "step_ids": [sid]})
    else:
        action = _chat(f"{where}因 {fc.value} 失败,处置建议:{note}。"
                       "请按这个处置推进;需要我决策的先列出选项")
    out.append(_sg("failure-disposition", f"按失败类处置({fc.value})", why, action))

    # ② 从断点续跑:显式列表 = 失败步复位 + 其余待跑步骤,已完成部分不重算
    rerun_ids = sorted({s.step_id for s in failed}
                       | {s.step_id for s in steps if s.state in _WAITING_STATES})
    if rerun_ids:
        out.append(_sg(
            "resume-breakpoint", "从断点续跑",
            f"已完成步骤的产物与指纹保留;显式列表({rerun_ids})会先复位失败步"
            "再继续,不重算未受影响的部分",
            _api("POST", "/api/pipeline",
                 body={"session": session, "run_id": run_id, "step_ids": rerun_ids})))

    # ③ 查看该步技能文档的失败处置节(带步骤号;流水线面板选中该步后,
    #   技能区在失败态自动展开《常见失败与处置》)
    if sid is not None:
        out.append(_sg(
            "skill-failures", f"查看第 {sid} 步技能文档《常见失败与处置》",
            f"第 {sid} 步的技能文档收录该步常见失败模式与处置依据"
            "(数据源 GET /api/skills);失败态下技能区自动展开失败处置节",
            _tab("pipeline", step=sid)))

    ctx = (f"run 失败于{where}(类别 {fc.value})" if sid is not None
           else f"run 失败(未定位到失败步骤,类别 {fc.value})")
    return out, ctx


def _suggest_interrupted(run: dict, steps: list[StepRow]) -> tuple[list[dict], str]:
    """interrupted:续跑 / 查看中断原因。"""
    session = run["session_id"]
    stopped = [s for s in steps if s.state in ("interrupted", "orphaned")]
    orphaned = any(s.state == "orphaned" for s in stopped)
    sid = stopped[0].step_id if stopped else None

    why = "run 被取消/中断:已完成阶段的产物与指纹保留,续跑从断点继续,不重跑已完成部分"
    if orphaned:
        # 与 DISPOSITIONS[WSL_ORPHANED] 同一句式:环境事件,先重启环境再续跑
        why += f";存在环境停止步骤 —— {DISPOSITIONS[FailureClass.WSL_ORPHANED]['note']}"
    out = [_sg("resume-run", "从断点续跑", why,
               _api("POST", "/api/resume", body={"session": session}))]

    if sid is not None:
        out.append(_sg(
            "interrupt-cause", f"查看中断原因(第 {sid} 步日志)",
            f"第 {sid} 步停在中断态:终端面板可读该步日志尾部,"
            "确认是人工取消还是环境停止(轨迹面板另有取消留痕)",
            _tab("term", step=sid)))
    else:
        out.append(_sg(
            "interrupt-cause", "查看中断原因(轨迹面板)",
            "本次中断没有停在具体步骤上(可能在步间检查点被取消);"
            "轨迹面板的干预留痕可还原取消时刻",
            _tab("trace")))

    done_n = sum(1 for s in steps if s.state in ("done", "skipped"))
    ctx = f"run 已中断:{done_n}/{len(steps)} 步完成,可从断点续跑"
    return out, ctx


# ---------------------------------------------------------------------------
# LLM 润色(只动 why 措辞;严格 JSON + 数字校验,失败逐条回退规则文案)
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")

_POLISH_SYSTEM = (
    "你是 InSAR 助手「下一步建议」卡的措辞润色器。输入 JSON:"
    '{"items":[{"id":"...","why":"..."}]}。只润色 why 的中文措辞使其更自然流畅,'
    "绝不改动任何数字、参数名、方法名、步骤号、失败类别,不增删条目、不改 id。"
    '输出严格 JSON:{"items":[{"id":"...","why":"..."}]}。'
)


def _numbers_match(original: str, polished: str) -> bool:
    """润色不许动数字:两边数字集合必须完全一致(比 narrate 的「原文保留」更严:
    建议卡的步骤号/参数值是行动依据,凭空新增的数字同样是幻觉)。"""
    return set(_NUM_RE.findall(original)) == set(_NUM_RE.findall(polished))


def _polish_whys(suggestions: list[dict], provider) -> str:
    """就地润色各条 why;返回来源标记 "llm" | "rules"(部分成功也算 llm)。

    防幻觉纪律(同 report 草稿口径):严格 JSON、id 对表、逐条数字校验;
    任何一条不合格就保留该条规则文案,provider 异常整体无害回退。
    """
    if not suggestions or provider is None or not getattr(provider, "enabled", False):
        return "rules"
    payload = {"items": [{"id": s["id"], "why": s["why"]} for s in suggestions]}
    try:
        data = provider.complete_json(
            system=_POLISH_SYSTEM,
            user=json.dumps(payload, ensure_ascii=False), max_tokens=1024)
    except BrainUnavailable:
        return "rules"
    items = data.get("items")
    if not isinstance(items, list):
        return "rules"
    by_id = {it["id"]: it["why"] for it in items
             if isinstance(it, dict)
             and isinstance(it.get("id"), str) and isinstance(it.get("why"), str)}
    polished_any = False
    for s in suggestions:
        cand = (by_id.get(s["id"]) or "").strip()
        if cand and _numbers_match(s["why"], cand):
            s["why"] = cand
            polished_any = True
    return "llm" if polished_any else "rules"


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def advise(store: Store, run_id: str, provider=None, *,
           available_routes: set[str] | frozenset[str] | None = None) -> dict:
    """run 终态 → {"context", "suggestions": [{"id","title","why","action"}]}。

    - 非终态返回空建议(API 层再补 note:"运行中");
    - available_routes 是运行时路由探测结果(如 {"/api/report/draft", ...}),
      调用方(advisor_router)从 request.app.routes 收集 —— 本模块不依赖 FastAPI;
    - provider 只润色 why,决策路径与它无关(provider=None 结果同样完整)。
    """
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    status = run["status"]
    if status not in TERMINAL_STATUSES:
        return {"run_id": run_id, "status": status,
                "context": f"run 处于 {status},待终态后再生成建议", "suggestions": []}

    steps = store.load_steps(run_id)
    routes = set(available_routes or ())
    evidence_level: str | None = None

    if status == "done":
        contract = load_contract()
        workspace = Path(run["workspace"]) if run.get("workspace") else None
        evidence = compute_evidence(store, run_id, contract, workspace=workspace)
        evidence_level = evidence.level
        done_n = sum(1 for s in steps if s.state in ("done", "skipped"))
        # 「未跑的只剩第 11 步质检」不按证据偏低处理:此时最自然的下一步恰是
        # 补跑 QA(达标分支建议 ①),阶梯 runnable 的原因(未完成步骤 [11])
        # 由该建议的 why 如实交代;其余任何缺口(未完成/run_ok 未过)仍算偏低
        gaps = [s for s in steps
                if s.state != "skipped" and (s.stage != "VERIFIED" or s.run_ok != 1)]
        only_qa_pending = (len(gaps) == 1 and gaps[0].step_id == QA_STEP_ID
                           and gaps[0].state == "pending")
        low = bool(run.get("simulated")) or (
            evidence.level == "runnable" and not only_qa_pending)
        if low:
            suggestions = _suggest_low_evidence(run, evidence)
        else:
            suggestions = _suggest_done(run, steps, routes)
        context = (f"run 已完成:{done_n}/{len(steps)} 步,证据级 {evidence.level}"
                   f"({evidence.level_index + 1}/{len(LADDER)})"
                   + ("(模拟执行)" if run.get("simulated") else ""))
    elif status == "failed":
        suggestions, context = _suggest_failed(run, steps)
    else:  # interrupted
        suggestions, context = _suggest_interrupted(run, steps)

    polish_source = _polish_whys(suggestions, provider)
    return {
        "run_id": run_id,
        "status": status,
        "context": context,
        "suggestions": suggestions,
        "evidence_level": evidence_level,
        "polish_source": polish_source,
    }
