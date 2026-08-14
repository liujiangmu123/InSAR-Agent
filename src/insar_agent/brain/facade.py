"""Brain 门面:intent / select / triage / narrate / converse 五职责(AGENT-DESIGN §3.5)
+ 自主循环周期决策 cycle(LOOP-CONTRACT §3)。

铁律:全部职责都能降级到无 LLM 路径 —— 把 Brain(provider=None) 传给 driver,
系统退化为手动可插拔流水线,结果完全正确(DESIGN.md:233 的架构约束,有守护测试)。

LLM 的输出永远是「候选集内的枚举索引/闭集标签」,越界直接拒绝:
  - select:{"choice": <int>}   索引越界 → 重问一次 → 仍错则取 recommend(§3.3 约束二)
  - triage:{"class": <闭集>}   不在闭集 → UNKNOWN
  - converse:{"reply", "action"} action.type 是闭集,参数经 registry 校验,
    越界丢动作只留 reply(附系统注记);reply 缺失=坏形状 → BrainUnavailable
  - cycle:{"say", "action"} 动作闭集 = converse 闭集 + search_data/inspect_file/
    thinking;越界丢动作留 say(附注记,source="degraded"),
    缺 say=坏形状 → BrainUnavailable
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable

from insar_agent.brain.provider import BrainTruncated, BrainUnavailable, LLMProvider
from insar_agent.core.failures import FailureClass, classify_log, error_window
from insar_agent.planner.feasibility import MethodFeasibility
from insar_agent.planner.score import pick_method
from insar_agent.registry.model import Capability
from insar_agent.registry.scenarios import Scenario, classify_text


@dataclass
class IntentResult:
    ok: bool
    scenario: Scenario | None = None
    source: str = "rules"  # rules | llm | form
    need_form: bool = False
    fields: dict = field(default_factory=dict)


@dataclass
class SelectResult:
    method_id: str
    reason: str
    source: str  # llm | recommend | only_choice


@dataclass
class TriageResult:
    failure_class: FailureClass
    source: str  # rules | llm | fallback
    detail: str = ""


@dataclass
class ConverseResult:
    reply: str
    action: dict | None = None  # 已过闭集校验的动作;None = 纯聊天
    rejected: str = ""  # 非空 = LLM 给过动作但越界被拦截(原因;reply 已附注记)
    # 可选会话标题(二期):仅会话首条消息时 LLM 给出,≤12 字;是否采纳由
    # driver 按触发条件把关(非首条消息带回来的标题一律忽略)
    session_title: str = ""


@dataclass
class CycleResult:
    """自主循环单周期决策(LOOP-CONTRACT §3)。"""

    action: dict | None  # 已过闭集校验的动作({"type": <CYCLE_ACTION_TYPES>, ...})或 None
    say: str | None      # 面向用户的话:收束汇报或过渡语
    done: bool           # True = 本回合收束(无 action;含越界动作被拦后的安全收束)
    # "llm" = LLM 输出原样通过校验;"degraded" = LLM 给过动作但越界被拦,
    # 丢动作只留 say 安全收束(与干净收束区分,便于驱动器/账本如实记账)
    source: str
    # 契约补充(LOOP-CONTRACT §4):循环驱动器在首周期后取此值作 route_pin 钉死
    # 路由,避免周期间主备漂移;缺省 None 便于调用方合成降级结果时省略
    route_index: int | None = None


#: converse 动作闭集(宁小勿大;§3.4:输出永远是闭集,越界即拦截)。
#: 二期新增 list_data:盘点本地数据集(数据由 driver 按 data/catalog 真实扫描)。
CONVERSE_ACTION_TYPES = ("plan", "execute", "status", "check_env", "list_data",
                         "set_params", "set_method")

#: cycle(自主循环)动作闭集 = converse 闭集 + 循环专属三动作;命名与
#: prototype/js/agentloop.js 的 ACTION_META 十项对齐(事件契约,LOOP-CONTRACT §1)。
CYCLE_ACTION_TYPES = ("search_data", "inspect_file", "check_env", "list_data", "status",
                      "plan", "execute", "set_params", "set_method", "thinking")

#: converse 的 system prompt。保持静态(动态系统状态走 user 消息注入,利于
#: 供应商侧 prompt 缓存);{SCENARIO_KEYS}/{STEP_LINES} 由 converse 按当前
#: 场景包/registry 填充 —— 用 replace 而非 str.format,免 JSON 花括号转义。
CONVERSE_SYSTEM = """\
你是 InSAR 数据处理助手,帮用户完成干涉测量(InSAR)数据处理,也能正常聊天、答疑。

能做:按场景规划处理流水线(plan);触发执行(execute);查运行状态(status);查环境探测(check_env);盘点本地数据集(list_data);修改某步的参数/方法(set_params/set_method,入队后在检查点生效)。
不能做:发明闭集之外的场景/步骤/方法/参数名;绕过系统校验;直接执行任意命令;下载数据或安装引擎(只能口头指引用户操作)。

只输出一个 JSON 对象,契约:{"reply": "<给用户的中文回复>", "action": null 或下列动作之一}
可选字段 "session_title":仅当系统状态里出现「会话命名」提示(会话首条消息)时附上,
值为概括本次会话主题的中文短标题(不超过 12 字);其余时候不要输出该字段。
动作闭集(字段不多不少,值必须来自下方闭集):
- {"type": "plan", "scenario": "<场景key>", "region": "<可选>", "timerange": "<可选>"} 规划流水线
- {"type": "execute"} 开始/继续执行当前计划
- {"type": "status"} 查询当前运行状态
- {"type": "check_env"} 查询环境探测结果
- {"type": "list_data"} 盘点本地已有的数据集(类型/大小/时间范围)
- {"type": "set_params", "step": <步骤号整数>, "params": {"<参数名>": <值>}} 修改某步参数
- {"type": "set_method", "step": <步骤号整数>, "method": "<方法id>"} 修改某步方法

场景闭集:{SCENARIO_KEYS}
步骤闭集(步骤号 名称:方法候选 | 参数名):
{STEP_LINES}

判断规则:
- status/check_env/list_data 的具体数据由系统在你的 reply 之后附上真实状态文本,你不要编造数值。
- 用户问「本地有什么数据 / 数据在哪 / 数据全不全 / 下好了没」这类盘点本地数据的问题 → list_data。
- 用户明确表达了对应意图才给动作;拿不准就 action=null 纯聊天,先向用户确认(宁可多问,不猜)。
- 闲聊、提问、寒暄、讨论 → action=null。
reply 要求:中文、口语化、简洁,不用 markdown 标题;直接回应用户说的话,不要复读系统状态。
"""

#: cycle 的 system prompt。与 CONVERSE_SYSTEM 同款纪律:保持静态(<2KB,利于供应商侧
#: prompt 缓存),{SCENARIO_KEYS}/{STEP_LINES} 用 replace 填充(免 JSON 花括号转义);
#: 动态内容(回合目标/状态/周期摘要)一律走 user 消息。契约:LOOP-CONTRACT §3。
CYCLE_SYSTEM = """\
你是 InSAR 数据处理 Agent 的循环决策器,逐周期推进【回合目标】:每周期只做一个决策,
要么给一个动作继续推进,要么收束汇报,绝不一次串多步。

只输出一个 JSON 对象,契约二选一:
{"say": "<中文收束汇报>"} 收束本回合;
{"action": <下列动作之一>, "say": "<一句话过渡语>"} 继续推进。

动作闭集(字段不多不少;不能发明动作/场景/步骤/方法/参数,值必须来自闭集):
- {"type": "search_data", "query": "<可选检索词>", "region": "<可选,WKT>",
  "timerange": "<可选,如 2019-06-01/2019-08-31>"} 检索数据(本地盘点+卫星归档),联网只出查询词
- {"type": "inspect_file", "name": "<文件名>", "step": <步骤号整数>} 查看文件,name 与
  step 二选一:name 取自摘要里出现过的名字,禁止路径;step 查该步状态/产物/日志
- {"type": "check_env"} 查环境探测结果
- {"type": "list_data"} 盘点本地数据集
- {"type": "status"} 查运行状态
- {"type": "plan", "scenario": "<场景key>", "region": "<可选>",
  "timerange": "<可选>"} 制定计划(不自动开跑)
- {"type": "execute"} 请求执行:只向用户发确认卡,批准才开跑;发出后收束
- {"type": "set_params", "step": <步骤号整数>, "params": {"<参数名>": <值>}} 改某步参数
- {"type": "set_method", "step": <步骤号整数>, "method": "<方法id>"} 换某步方法
- {"type": "thinking"} 本周期只梳理思路,不动工具

场景闭集:{SCENARIO_KEYS}
步骤闭集(步骤号 名称:方法候选 | 参数名):
{STEP_LINES}

纪律:
- 动作结果由系统压成摘要,进下一周期的【已完成周期】;一切以摘要为准,绝不编造数值;
  绝不输出命令、URL、文件路径。
- 不重复已完成的动作;无新进展就换思路或收束,绝不空转。
- 何时收束(只 say 无 action):目标已达成、需要用户决策或批准、或信息已够回答;
  say 给出结论与下一步建议。
- say:中文、口语化、简洁,不用 markdown 标题。
"""


class Brain:
    def __init__(self, provider: LLMProvider | None = None):
        self.provider = provider

    @property
    def enabled(self) -> bool:
        return self.provider is not None and self.provider.enabled

    # ---------------- intent ----------------

    def intent(self, text: str) -> IntentResult:
        sc = classify_text(text)  # 规则优先,零成本零幻觉
        if sc is not None:
            return IntentResult(ok=True, scenario=sc, source="rules")
        if not self.enabled:
            return IntentResult(ok=False, need_form=True, source="form")
        try:
            from insar_agent.registry.scenarios import SCENARIOS, scenario_of

            keys = [s.key for s in SCENARIOS]
            data = self.provider.complete_json(  # type: ignore[union-attr]
                system=("你是 InSAR 处理助手的意图分类器。只输出 JSON:"
                        f'{{"scenario": <{keys} 之一或 "unknown">}}。不要输出其他字段。'),
                user=text, max_tokens=64)
            key = data.get("scenario")
            sc = scenario_of(key) if isinstance(key, str) else None
            if sc is None:
                return IntentResult(ok=False, need_form=True, source="form")
            return IntentResult(ok=True, scenario=sc, source="llm")
        except BrainUnavailable:
            return IntentResult(ok=False, need_form=True, source="form")

    # ---------------- select ----------------

    def select(self, cap: Capability, feasible: list[MethodFeasibility], *,
               env_facts: str = "", upstream_summary: str = "",
               skill_hints: str = "", prefer: str | None = None) -> SelectResult:
        ok_methods = [f for f in feasible if f.ok]
        if not ok_methods:
            raise ValueError(f"步骤 {cap.id} 无可行方法,select 不应被调用")
        if len(ok_methods) == 1:
            return SelectResult(ok_methods[0].method.id, "唯一可行项", "only_choice")
        if not self.enabled:
            picked = pick_method(feasible, prefer=prefer)
            return SelectResult(picked.method.id, picked.method.why or "registry 推荐",
                                "recommend")

        menu = "\n".join(
            f"{i}: {f.method.id} —— {f.method.why}" for i, f in enumerate(ok_methods))
        system = ("你是 InSAR 方法选择器。只能从候选里选一个,输出 JSON:"
                  '{"choice": <候选序号整数>, "reason": "<一句话依据>"}。'
                  "不要发明候选之外的方法。")
        user = (f"步骤:{cap.name}\n环境事实:{env_facts or '无'}\n"
                f"上游摘要:{upstream_summary or '无'}\n候选:\n{menu}")
        if skill_hints:  # 步骤技能《参数启发式》:只附上下文,候选闭集与越界拒绝不变
            user += f"\n技能启发式:\n{skill_hints}"
        for _attempt in range(2):  # 同一决策点最多问 2 次(§3.3 约束二)
            try:
                data = self.provider.complete_json(system=system, user=user, max_tokens=128)  # type: ignore[union-attr]
            except BrainUnavailable:
                break
            choice = data.get("choice")
            # bool 是 int 子类:LLM 返回 {"choice": true} 若不拦会被当索引 1 静默接受,
            # 那不是候选序号而是幻觉输出 —— 一律按越界处理(重问→降级)
            if (isinstance(choice, int) and not isinstance(choice, bool)
                    and 0 <= choice < len(ok_methods)):
                return SelectResult(ok_methods[choice].method.id,
                                    str(data.get("reason", ""))[:200], "llm")
            user += f"\n(上次输出 choice={choice!r} 越界,候选序号 0-{len(ok_methods) - 1})"
        picked = pick_method(feasible, prefer=prefer)  # 降级:registry 推荐
        return SelectResult(picked.method.id, picked.method.why or "registry 推荐", "recommend")

    # ---------------- triage ----------------

    def triage(self, log_text: str, *, skill_notes: str = "") -> TriageResult:
        cls = classify_log(log_text)  # 规则优先:命中不消耗 LLM 也不受幻觉影响
        if cls is not None:
            return TriageResult(cls, "rules")
        if not self.enabled:
            return TriageResult(FailureClass.UNKNOWN, "fallback", "规则未命中且 LLM 未启用")
        window = error_window(log_text)
        valid = [c.value for c in FailureClass]
        user = f"错误窗口(±5 行):\n{window}"
        if skill_notes:  # 步骤技能《常见失败与处置》:只进 LLM 上下文,不进规则分类
            user = f"技能文档《常见失败与处置》(处置参考):\n{skill_notes}\n\n{user}"
        try:
            data = self.provider.complete_json(  # type: ignore[union-attr]
                system=('你是失败分类器。只输出 JSON:{"class": "<闭集之一>"}。'
                        f"闭集:{valid}。无法判断就输出 unknown,绝不发明新类别。"),
                user=user, max_tokens=64)
            raw = data.get("class")
            if isinstance(raw, str) and raw in valid:
                return TriageResult(FailureClass(raw), "llm")
        except BrainUnavailable:
            pass
        return TriageResult(FailureClass.UNKNOWN, "fallback", "LLM 分类失败或越出闭集")

    # ---------------- converse ----------------

    def converse(self, text: str, *, history: list[dict] | None = None,
                 state_summary: str = "",
                 registry: dict[int, Capability] | None = None,
                 on_delta: Callable[[str], None] | None = None) -> ConverseResult:
        """会话职责(第五职责):自然语言回复 + 可选闭集动作。

        边界注记:converse 是「会话角色」,允许携带短滚动历史(最近 8 条,
        每条截 500 字)—— 与 select/triage 的「决策请求不带历史」(§3.3 约束四)
        不同轨:那两个是独立决策点,历史只会引入无关偏置;converse 的任务本身
        就是接住对话上下文。历史仍有硬预算,绝不全量重发。

        kwargs 均有缺省值:评测 harness(tests/eval/run_converse_eval.py)以
        Brain(provider).converse(text) 单参调用,registry 缺省取全流水线闭集,
        闭集校验纪律与 driver 注入时完全一致。

        流式(WAVE-0814B §1.2):on_delta=None(缺省)走既有 complete_json
        路径,行为与历史逐字节一致(金标评测零感知);on_delta 给定且 provider
        有可用 chat_stream → 流式拿增量,_StreamingFieldTap 只把 "reply" 字段值
        逐段转发给 on_delta(纯展示旁路);provider 缺 chat_stream(假 provider/
        旧实现)→ 静默回落 complete_json。终帧仍全量 json.loads + reply 校验 +
        动作闭集校验 —— 抽取器抽错由终帧自愈。

        失败语义:LLM 未启用/调用失败/截断/响应缺 reply(坏形状)→
        BrainUnavailable,调用方(driver.turn)降级到规则路径;
        动作越界不算失败 —— 丢动作留 reply,并在 reply 后追加系统注记。
        """
        if not self.enabled:
            raise BrainUnavailable("LLM 未启用,converse 不可用")
        from insar_agent.registry.scenarios import SCENARIOS

        if registry is None:
            from insar_agent.registry.capabilities import REGISTRY
            registry = REGISTRY
        history = history or []

        keys = [s.key for s in SCENARIOS]
        step_lines = "\n".join(
            f"{sid} {registry[sid].name}:{'/'.join(m.id for m in registry[sid].methods)}"
            f" | {'、'.join(registry[sid].params) or '无参数'}"
            for sid in sorted(registry))
        system = (CONVERSE_SYSTEM
                  .replace("{SCENARIO_KEYS}", "/".join(keys))
                  .replace("{STEP_LINES}", step_lines))
        recent = [f"{'用户' if m.get('role') == 'user' else '助手'}:"
                  f"{str(m.get('content', ''))[:500]}" for m in history[-8:]]
        user = "【系统状态】\n" + state_summary
        if recent:
            user += "\n\n【最近对话】\n" + "\n".join(recent)
        user += "\n\n【用户消息】\n" + text
        # max_tokens 2048:推理型模型需要余量;截断即整体拒绝(complete_json
        # 的 BrainTruncated 语义,absorb-E9),绝不用半截 JSON 装完整回复
        if on_delta is None:
            # 缺省:既有非流式路径,调用形态逐字节不变(金标评测零感知)
            data = self.provider.complete_json(  # type: ignore[union-attr]
                system=system, user=user, max_tokens=2048)
        else:
            data = self._converse_stream_json(system, user, on_delta)
        reply = data.get("reply")
        if not isinstance(reply, str) or not reply.strip():
            # 坏形状与截断同语义:整体拒绝,调用方降级到规则路径
            raise BrainUnavailable(f"converse 响应缺 reply:{str(data)[:200]}")
        reply = reply.strip()
        title = _sanitize_session_title(data.get("session_title"))
        raw_action = data.get("action")
        if raw_action is None:
            return ConverseResult(reply=reply, session_title=title)
        action, why = _validate_converse_action(raw_action, scenario_keys=keys,
                                                registry=registry)
        if action is None:
            # 越界动作只拦不炸:reply 仍可用,注记让用户知道有动作被丢弃
            return ConverseResult(reply=reply + "(动作越界已拦截)", rejected=why,
                                  session_title=title)
        return ConverseResult(reply=reply, action=action, session_title=title)

    def _converse_stream_json(self, system: str, user: str,
                              on_delta: Callable[[str], None]) -> dict:
        """converse 的流式旁路(WAVE-0814B §1.2):chat_stream 拿增量,
        _StreamingFieldTap 只把顶层 "reply" 字段值逐段转发给 on_delta;
        返回终帧解析出的 dict(reply 校验/动作闭集校验与非流式路径共用)。

        防御闸门:provider 缺可用 chat_stream(_stream_callable 回 None)→
        静默回落 complete_json,调用方零感知(既有假 provider/旧实现不受影响)。

        失败语义:截断(BrainTruncated)原样上抛,重试解决不了 prompt 过长
        (absorb-E9);其余 BrainUnavailable 若发生在零外发前缀内(tap 还没
        转发过任何字符)→ 降级非流式重试一次 —— 建流类故障不该把会话直接
        打回规则路径;tap 已外发过 → 原样上抛,绝不重放(重放=用户看到重复
        文本)。终帧不是合法 JSON 对象 → BrainUnavailable(complete_json 的
        坏形状同语义)。
        """
        chat_stream = _stream_callable(self.provider)
        if chat_stream is None:
            return self.provider.complete_json(  # type: ignore[union-attr]
                system=system, user=user, max_tokens=2048)
        tap = _StreamingFieldTap("reply", on_delta)
        try:
            outcome = chat_stream(
                [{"role": "system", "content": system},
                 {"role": "user", "content": user}],
                json_only=True, max_tokens=2048, on_delta=tap.feed)
        except BrainTruncated:
            raise
        except BrainUnavailable:
            if tap.emitted:
                raise
            return self.provider.complete_json(  # type: ignore[union-attr]
                system=system, user=user, max_tokens=2048)
        try:
            data = json.loads(outcome.content)
        except (TypeError, ValueError) as exc:
            raise BrainUnavailable(
                f"converse 流式响应不是合法 JSON:{str(outcome.content)[:200]}") from exc
        if not isinstance(data, dict):
            raise BrainUnavailable(
                f"converse 流式响应顶层不是对象:{str(outcome.content)[:200]}")
        return data

    # ---------------- cycle(自主循环周期决策) ----------------

    def cycle(self, *, goal: str, cycles_summary: list[str], state_summary: str,
              registry: dict[int, Capability],
              route_pin: int | None = None) -> CycleResult:
        """自主循环的单周期决策(LOOP-CONTRACT §3):一个动作,或 say 收束。

        与 converse 的分工:converse 是单步会话角色(带短滚动历史);cycle 是
        回合内循环的周期决策点,**不带对话历史**,只带本回合的逐周期结构化摘要
        (「决策请求不带历史」对循环形态的修订版,红线 §0.1)。

        消息组装:system 是静态循环提示词(动作闭集+纪律,利于供应商侧 prompt
        缓存);user = 回合目标 + 系统状态摘要 + 逐周期摘要(每条硬截 300 字,
        日志绝不整段进 LLM,红线 §0.5)。经 provider.chat(json_only=True) 调用
        (B2 契约原语,LOOP-CONTRACT §2)。

        失败语义与 converse 相同:未启用/调用失败/响应坏形状(非 JSON、顶层非
        对象、缺 say)→ BrainUnavailable;截断由 provider.chat 抛 BrainTruncated
        (其子类,整体拒绝,absorb-E9)。动作越界不算失败 —— 丢动作留 say
        (附系统注记),done=True 让循环安全收束(fail-closed),source 标
        "degraded" 与干净收束区分。

        route_pin:循环驱动器在首周期后取 CycleResult.route_index 钉死路由,
        后续周期原样传回,避免周期间主备路由漂移(LOOP-CONTRACT §4)。
        """
        if not self.enabled:
            raise BrainUnavailable("LLM 未启用,cycle 不可用")
        from insar_agent.registry.scenarios import SCENARIOS

        keys = [s.key for s in SCENARIOS]
        step_lines = "\n".join(
            f"{sid} {registry[sid].name}:{'/'.join(m.id for m in registry[sid].methods)}"
            f" | {'、'.join(registry[sid].params) or '无参数'}"
            for sid in sorted(registry))
        system = (CYCLE_SYSTEM
                  .replace("{SCENARIO_KEYS}", "/".join(keys))
                  .replace("{STEP_LINES}", step_lines))
        entries = [str(s)[:300] for s in cycles_summary]  # 每条硬预算 300 字
        user = f"【回合目标】\n{goal}\n\n【系统状态】\n{state_summary}\n\n【已完成周期】\n"
        if entries:
            user += "\n".join(f"{i}. {s}" for i, s in enumerate(entries, 1))
        else:
            user += "(无:这是本回合第 1 个周期)"
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
        # max_tokens 2048 与 converse 同源:推理型模型要余量;截断即整体拒绝
        # (provider.chat 的 BrainTruncated 语义,absorb-E9),绝不用半截 JSON
        outcome = self.provider.chat(  # type: ignore[union-attr]
            messages, json_only=True, max_tokens=2048, route_pin=route_pin)
        try:
            data = json.loads(outcome.content)
        except (TypeError, ValueError) as exc:
            raise BrainUnavailable(
                f"cycle 响应不是合法 JSON:{str(outcome.content)[:200]}") from exc
        if not isinstance(data, dict):
            raise BrainUnavailable(f"cycle 响应顶层不是对象:{str(outcome.content)[:200]}")
        say = data.get("say")
        if not isinstance(say, str) or not say.strip():
            # 坏形状与截断同语义:整体拒绝,调用方(循环驱动器)note 收尾降级
            raise BrainUnavailable(f"cycle 响应缺 say:{str(data)[:200]}")
        say = say.strip()
        raw_action = data.get("action")
        if raw_action is None:
            return CycleResult(action=None, say=say, done=True, source="llm",
                               route_index=outcome.route_index)
        action, _why = _validate_cycle_action(raw_action, scenario_keys=keys,
                                              registry=registry)
        if action is None:
            # 越界动作只拦不炸(converse 同款):丢动作留 say(附注记)安全收束,
            # source 标 degraded 与干净收束区分
            return CycleResult(action=None, say=say + "(动作越界已拦截)", done=True,
                               source="degraded", route_index=outcome.route_index)
        return CycleResult(action=action, say=say, done=False, source="llm",
                           route_index=outcome.route_index)

    # ---------------- narrate ----------------

    def narrate(self, provenance: dict) -> tuple[str, str]:
        """返回 (markdown, source)。模板是保底,LLM 只做润色且失败无害。"""
        from insar_agent.report.methods import methods_markdown

        template = methods_markdown(provenance)
        if not self.enabled:
            return template, "template"
        try:
            data = self.provider.complete_json(  # type: ignore[union-attr]
                system=('你是论文方法章节润色器。输入是自动生成的方法草稿,'
                        '输出 JSON:{"markdown": "<润色后的 markdown>"}。'
                        "只润色措辞,绝不改动任何数字、方法名、参数值。"),
                user=template[:6000], max_tokens=2048)
            polished = data.get("markdown")
            if isinstance(polished, str) and _numbers_preserved(template, polished):
                return polished, "llm"
        except BrainUnavailable:
            pass
        return template, "template"


def _numbers_preserved(original: str, polished: str) -> bool:
    """润色不许动数字:原文全部数值必须原样出现在润色稿里(反幻觉护栏)。"""
    nums = set(re.findall(r"\d+\.\d+|\d{2,}", original))
    return all(n in polished for n in nums)


def _sanitize_session_title(raw) -> str:
    """会话标题的护栏:非字符串一律弃用;压掉换行/连续空白;硬截 12 字。

    截断而非拒绝:标题只是展示标签,超长是小瑕疵不是越界动作,
    砍到契约上限比整条丢弃更符合用户利益。
    """
    if not isinstance(raw, str):
        return ""
    return re.sub(r"\s+", " ", raw).strip()[:12]


def _stream_callable(provider) -> Callable | None:
    """provider 上可信的 chat_stream,不可用回 None(converse 流式防御闸门,§1.2)。

    「缺 chat_stream」的判定比 callable 检查更严一档:大量测试假 provider 是
    LLMProvider 的子类、只改写 complete_json(脚本应答),chat_stream 沿继承链
    会解析到真实现 —— 对着占位路由("http://fake")发真网络请求,离线测试出网
    还可能吊死在 60s 超时上。判据:
      - 实例级注入的 chat_stream(测试桩)视为最具体实现,直接信任;
      - 类级则要求定义 chat_stream 的类不比定义 complete_json 的类更靠祖先:
        complete_json 被子类改写而 chat_stream 只是继承时,该 provider 的真实
        行为都在 complete_json 里,按「缺 chat_stream」处置,静默回落。
    """
    fn = getattr(provider, "chat_stream", None)
    if not callable(fn):
        return None
    if "chat_stream" in getattr(provider, "__dict__", {}):
        return fn
    mro = type(provider).__mro__
    stream_owner = next((c for c in mro if "chat_stream" in vars(c)), None)
    json_owner = next((c for c in mro if "complete_json" in vars(c)), None)
    if (stream_owner is not None and json_owner is not None
            and stream_owner is not json_owner and issubclass(json_owner, stream_owner)):
        return None  # 子类只改写了 complete_json:继承来的 chat_stream 不可信
    return fn


#: 已配对的代理项在 json.loads 阶段即合并成星面字符;解码文本里残留的任何
#: 代理码点都是孤代理 —— 直接送编码层会炸(UTF-8 编不了孤代理,NDJSON 响应
#: 层裸断连,项目有前科)→ 外发前一律替换为 U+FFFD,终帧全量结果自愈
_LONE_SURROGATE_RE = re.compile("[\ud800-\udfff]")

#: 结尾不完整的 \uXXXX 转义(\u 后不足 4 个十六进制位)
_PARTIAL_UNICODE_ESCAPE_RE = re.compile(r"\\u[0-9a-fA-F]{0,3}$")


class _StreamingFieldTap:
    """从流式 JSON 文本增量里抽取顶层 field("reply")字符串字段值,逐段外发。

    定位(WAVE-0814B §1.2):纯展示旁路 —— 抽取只影响打字机效果,抽错/漏抽由
    终帧全量 json.loads 自愈;因此前缀解码失败一律静默跳过,绝不炸主链路。

    机制:字符级扫描 JSON 文本,转义感知地跟踪字符串边界与嵌套深度,只认
    顶层(depth==1)且后跟 ':' 的 field 键;值必须是字符串,进入捕获态后按
    chunk 外发。每次外发前对原始转义前缀做尾部安全裁剪(三条,契约硬要求):
      1. 结尾奇数个反斜杠(转义符断在 chunk 边界)→ 裁掉最后一个;
      2. 结尾不完整的 \\uXXXX → 从 \\u 起整段裁掉;
      3. 解码后结尾是高位代理(等下一 chunk 的低位配对)→ 暂扣该字符。
    前缀经 json.loads('"'+prefix+'"') 精确反转义,与已外发长度做差得新段。
    字段缺失/值不是字符串 → 永不外发。
    """

    def __init__(self, field: str, forward: Callable[[str], None]):
        self._field = field
        self._forward = forward
        self._state = "scan"          # scan(找键)| capture(收值)| done
        self._depth = 0
        self._in_string = False
        self._escape = False
        self._token: list[str] = []   # 扫描态:当前字符串 token 的原始内容
        self._pending_key = False     # 刚闭合的顶层字符串 == field,待 ':' 定性
        self._await_value = False     # 已见 ':',待值的首个非空白字符
        self._raw: list[str] = []     # 捕获态:值的原始(仍转义)文本
        self._sent = 0                # 已消费的解码字符数

    @property
    def emitted(self) -> bool:
        """是否已向 forward 外发过内容(零外发前缀判定,§1.2 失败语义用)。"""
        return self._sent > 0

    def feed(self, chunk: str) -> None:
        if self._state == "done" or not isinstance(chunk, str):
            return
        for ch in chunk:
            if self._state == "done":
                return
            if self._state == "capture":
                self._feed_capture(ch)
            else:
                self._feed_scan(ch)
        if self._state == "capture":
            self._flush(final=False)

    # ---- 扫描态:转义感知的极简 JSON 走查,只为定位顶层 field 键 ----

    def _feed_scan(self, ch: str) -> None:
        if self._in_string:
            if self._escape:
                self._escape = False
                self._token.append(ch)
            elif ch == "\\":
                self._escape = True
                self._token.append(ch)
            elif ch == '"':
                self._in_string = False
                if self._depth == 1 and "".join(self._token) == self._field:
                    self._pending_key = True  # 是键还是恰好同名的值,看下个非空白
            else:
                self._token.append(ch)
            return
        if self._pending_key:
            if ch in " \t\r\n":
                return
            self._pending_key = False
            if ch == ":":
                self._await_value = True
                return
            # 不是键(顶层某个值恰好等于字段名):当普通结构字符继续走下方处理
        if self._await_value:
            if ch in " \t\r\n":
                return
            self._await_value = False
            if ch == '"':
                self._state = "capture"
                self._escape = False
                return
            self._state = "done"  # 字段值不是字符串:永不外发(终帧自愈)
            return
        if ch == '"':
            self._in_string = True
            self._escape = False
            self._token = []
        elif ch in "{[":
            self._depth += 1
        elif ch in "}]":
            self._depth -= 1

    # ---- 捕获态:收集值的原始转义文本,闭合引号时定稿 ----

    def _feed_capture(self, ch: str) -> None:
        if self._escape:
            self._escape = False
            self._raw.append(ch)
        elif ch == "\\":
            self._escape = True
            self._raw.append(ch)
        elif ch == '"':
            self._flush(final=True)
            self._state = "done"
        else:
            self._raw.append(ch)

    def _flush(self, *, final: bool) -> None:
        raw = "".join(self._raw)
        if not final:
            raw = self._safe_prefix(raw)
        try:
            text = json.loads('"' + raw + '"')
        except ValueError:
            return  # 前缀暂不可解码(畸形转义等):本轮跳过,终帧自愈
        if not final and text and 0xD800 <= ord(text[-1]) <= 0xDBFF:
            text = text[:-1]  # 裁剪三:结尾高位代理暂扣,等下一 chunk 的低位配对
        if len(text) <= self._sent:
            return
        seg = _LONE_SURROGATE_RE.sub("\ufffd", text[self._sent:])
        self._sent = len(text)  # 先记账再外发:forward 抛错也绝不重发同段
        self._forward(seg)

    @staticmethod
    def _safe_prefix(raw: str) -> str:
        m = _PARTIAL_UNICODE_ESCAPE_RE.search(raw)
        if m is not None:
            i = m.start()  # \u 的反斜杠位置
            j = i
            while j > 0 and raw[j - 1] == "\\":
                j -= 1
            if (i - j) % 2 == 0:  # 前面偶数个反斜杠 → 它是转义起始,\u 不完整
                raw = raw[:i]
        stripped = raw.rstrip("\\")
        if (len(raw) - len(stripped)) % 2 == 1:  # 裁剪一:结尾奇数个反斜杠
            raw = raw[:-1]
        return raw


def _validate_converse_action(action, *, scenario_keys: list[str],
                              registry: dict[int, Capability]
                              ) -> tuple[dict | None, str]:
    """converse 动作的闭集校验。返回 (归一化动作, "") 或 (None, 拦截原因)。

    归一化 = 只保留白名单字段,调用方(driver)拿到的动作可直接信任;
    校验复用 registry 的既有闭集(cap.method / cap.validate_params),
    与 /api/actions、fork_run 的「只选不造」纪律同源。
    step 的 bool 拦截同 select 的 choice:{"step": true} 不是步骤号,是幻觉。
    """
    if not isinstance(action, dict):
        return None, f"action 不是对象:{type(action).__name__}"
    kind = action.get("type")
    if kind not in CONVERSE_ACTION_TYPES:
        return None, f"type 越界:{kind!r}"
    if kind == "plan":
        sc = action.get("scenario")
        if not isinstance(sc, str) or sc not in scenario_keys:
            return None, f"scenario 越界:{sc!r}(闭集:{scenario_keys})"
        out: dict = {"type": "plan", "scenario": sc}
        for key in ("region", "timerange"):  # 可选自由文本:非字符串静默丢弃
            v = action.get(key)
            if isinstance(v, str) and v.strip():
                out[key] = v.strip()
        return out, ""
    if kind in ("execute", "status", "check_env", "list_data"):
        return {"type": kind}, ""  # 无参数动作:多余字段一律不透传
    step = action.get("step")
    if isinstance(step, bool) or not isinstance(step, int) or step not in registry:
        return None, f"step 越界:{step!r}(闭集:{sorted(registry)})"
    cap = registry[step]
    if kind == "set_method":
        method = action.get("method")
        if not isinstance(method, str) or cap.method(method) is None:
            return None, (f"method 越界:{method!r}"
                          f"(候选:{[m.id for m in cap.methods]})")
        return {"type": kind, "step": step, "method": method}, ""
    params = action.get("params")
    if not isinstance(params, dict) or not params:
        return None, "params 缺失或为空"
    errors = cap.validate_params(params)
    if errors:
        return None, f"参数校验失败:{errors}"
    return {"type": kind, "step": step, "params": params}, ""


def _validate_cycle_action(action, *, scenario_keys: list[str],
                           registry: dict[int, Capability]
                           ) -> tuple[dict | None, str]:
    """cycle 动作的闭集校验(_validate_converse_action 同款纪律)。

    converse 七动作原样委托既有校验器(同源同纪律,零漂移);循环专属三动作:
      - thinking:无参数,多余字段不透传(同 execute/status);
      - search_data:query/region/timerange 均为可选自由文本(同 plan 的可选字段),
        联网层只拿到查询词(红线 §0.5);
      - inspect_file:step(当前 run 的步骤号)与 name(数据集/文件名)二选一,
        step 优先(P2-10:step 支持使 driver 的步骤日志/产物分支经真实校验可达);
        name 不得含路径成分(红线 §0.3「LLM 零命令零路径」),只接受之前周期
        结果里出现过的文件/数据集名,由 driver 在数据目录内解析。
    返回 (归一化动作, "") 或 (None, 拦截原因)。
    """
    if not isinstance(action, dict):
        return None, f"action 不是对象:{type(action).__name__}"
    kind = action.get("type")
    if kind not in CYCLE_ACTION_TYPES:
        return None, f"type 越界:{kind!r}"
    if kind in CONVERSE_ACTION_TYPES:
        return _validate_converse_action(action, scenario_keys=scenario_keys,
                                         registry=registry)
    if kind == "thinking":
        return {"type": "thinking"}, ""
    if kind == "search_data":
        out: dict = {"type": "search_data"}
        for key in ("query", "region", "timerange"):  # 可选自由文本:非字符串静默丢弃
            v = action.get(key)
            if isinstance(v, str) and v.strip():
                out[key] = v.strip()
        return out, ""
    # inspect_file:step 给了就按 step 走(显式坏 step 一律拦截,不静默退回 name;
    # bool 拦截同 set_* 的 step:{"step": true} 不是步骤号,是幻觉)
    if action.get("step") is not None:
        step = action.get("step")
        if isinstance(step, bool) or not isinstance(step, int) or step not in registry:
            return None, f"inspect_file 的 step 越界:{step!r}(闭集:{sorted(registry)})"
        return {"type": "inspect_file", "step": step}, ""
    # name 是文件名不是路径 —— 分隔符/盘符/父级穿越一律越界
    name = action.get("name")
    if not isinstance(name, str) or not name.strip():
        return None, f"name 缺失或非字符串:{name!r}"
    name = name.strip()
    if any(tok in name for tok in ("/", "\\", "..", ":")):
        return None, f"name 含路径成分:{name!r}(只接受文件名,不接受路径)"
    return {"type": "inspect_file", "name": name}, ""
