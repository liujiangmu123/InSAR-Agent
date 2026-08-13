"""Brain 门面:intent / select / triage / narrate / converse 五职责(AGENT-DESIGN §3.5)。

铁律:全部职责都能降级到无 LLM 路径 —— 把 Brain(provider=None) 传给 driver,
系统退化为手动可插拔流水线,结果完全正确(DESIGN.md:233 的架构约束,有守护测试)。

LLM 的输出永远是「候选集内的枚举索引/闭集标签」,越界直接拒绝:
  - select:{"choice": <int>}   索引越界 → 重问一次 → 仍错则取 recommend(§3.3 约束二)
  - triage:{"class": <闭集>}   不在闭集 → UNKNOWN
  - converse:{"reply", "action"} action.type 是闭集,参数经 registry 校验,
    越界丢动作只留 reply(附系统注记);reply 缺失=坏形状 → BrainUnavailable
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from insar_agent.brain.provider import BrainUnavailable, LLMProvider
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


#: converse 动作闭集(第一期,宁小勿大;§3.4:输出永远是闭集,越界即拦截)
CONVERSE_ACTION_TYPES = ("plan", "execute", "status", "check_env",
                         "set_params", "set_method")

#: converse 的 system prompt。保持静态(动态系统状态走 user 消息注入,利于
#: 供应商侧 prompt 缓存);{SCENARIO_KEYS}/{STEP_LINES} 由 converse 按当前
#: 场景包/registry 填充 —— 用 replace 而非 str.format,免 JSON 花括号转义。
CONVERSE_SYSTEM = """\
你是 InSAR 数据处理助手,帮用户完成干涉测量(InSAR)数据处理,也能正常聊天、答疑。

能做:按场景规划处理流水线(plan);触发执行(execute);查运行状态(status);查环境探测(check_env);修改某步的参数/方法(set_params/set_method,入队后在检查点生效)。
不能做:发明闭集之外的场景/步骤/方法/参数名;绕过系统校验;直接执行任意命令;下载数据或安装引擎(只能口头指引用户操作)。

只输出一个 JSON 对象,契约:{"reply": "<给用户的中文回复>", "action": null 或下列动作之一}
动作闭集(字段不多不少,值必须来自下方闭集):
- {"type": "plan", "scenario": "<场景key>", "region": "<可选>", "timerange": "<可选>"} 规划流水线
- {"type": "execute"} 开始/继续执行当前计划
- {"type": "status"} 查询当前运行状态
- {"type": "check_env"} 查询环境探测结果
- {"type": "set_params", "step": <步骤号整数>, "params": {"<参数名>": <值>}} 修改某步参数
- {"type": "set_method", "step": <步骤号整数>, "method": "<方法id>"} 修改某步方法

场景闭集:{SCENARIO_KEYS}
步骤闭集(步骤号 名称:方法候选 | 参数名):
{STEP_LINES}

判断规则:
- status/check_env 的具体数据由系统在你的 reply 之后附上真实状态文本,你不要编造数值。
- 用户明确表达了对应意图才给动作;拿不准就 action=null 纯聊天,先向用户确认(宁可多问,不猜)。
- 闲聊、提问、寒暄、讨论 → action=null。
reply 要求:中文、口语化、简洁,不用 markdown 标题;直接回应用户说的话,不要复读系统状态。
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

    def converse(self, text: str, *, history: list[dict], state_summary: str,
                 registry: dict[int, Capability]) -> ConverseResult:
        """会话职责(第五职责):自然语言回复 + 可选闭集动作。

        边界注记:converse 是「会话角色」,允许携带短滚动历史(最近 8 条,
        每条截 500 字)—— 与 select/triage 的「决策请求不带历史」(§3.3 约束四)
        不同轨:那两个是独立决策点,历史只会引入无关偏置;converse 的任务本身
        就是接住对话上下文。历史仍有硬预算,绝不全量重发。

        失败语义:LLM 未启用/调用失败/截断/响应缺 reply(坏形状)→
        BrainUnavailable,调用方(driver.turn)降级到规则路径;
        动作越界不算失败 —— 丢动作留 reply,并在 reply 后追加系统注记。
        """
        if not self.enabled:
            raise BrainUnavailable("LLM 未启用,converse 不可用")
        from insar_agent.registry.scenarios import SCENARIOS

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
        data = self.provider.complete_json(  # type: ignore[union-attr]
            system=system, user=user, max_tokens=2048)
        reply = data.get("reply")
        if not isinstance(reply, str) or not reply.strip():
            # 坏形状与截断同语义:整体拒绝,调用方降级到规则路径
            raise BrainUnavailable(f"converse 响应缺 reply:{str(data)[:200]}")
        reply = reply.strip()
        raw_action = data.get("action")
        if raw_action is None:
            return ConverseResult(reply=reply)
        action, why = _validate_converse_action(raw_action, scenario_keys=keys,
                                                registry=registry)
        if action is None:
            # 越界动作只拦不炸:reply 仍可用,注记让用户知道有动作被丢弃
            return ConverseResult(reply=reply + "(动作越界已拦截)", rejected=why)
        return ConverseResult(reply=reply, action=action)

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
    if kind in ("execute", "status", "check_env"):
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
