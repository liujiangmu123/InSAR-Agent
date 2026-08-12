"""Brain 门面:intent / select / triage / narrate 四职责(AGENT-DESIGN §3.5)。

铁律:四个职责全部能降级到无 LLM 路径 —— 把 Brain(provider=None) 传给 driver,
系统退化为手动可插拔流水线,结果完全正确(DESIGN.md:233 的架构约束,有守护测试)。

LLM 的输出永远是「候选集内的枚举索引/闭集标签」,越界直接拒绝:
  - select:{"choice": <int>}   索引越界 → 重问一次 → 仍错则取 recommend(§3.3 约束二)
  - triage:{"class": <闭集>}   不在闭集 → UNKNOWN
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
               prefer: str | None = None) -> SelectResult:
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

    def triage(self, log_text: str) -> TriageResult:
        cls = classify_log(log_text)  # 规则优先:命中不消耗 LLM 也不受幻觉影响
        if cls is not None:
            return TriageResult(cls, "rules")
        if not self.enabled:
            return TriageResult(FailureClass.UNKNOWN, "fallback", "规则未命中且 LLM 未启用")
        window = error_window(log_text)
        valid = [c.value for c in FailureClass]
        try:
            data = self.provider.complete_json(  # type: ignore[union-attr]
                system=('你是失败分类器。只输出 JSON:{"class": "<闭集之一>"}。'
                        f"闭集:{valid}。无法判断就输出 unknown,绝不发明新类别。"),
                user=f"错误窗口(±5 行):\n{window}", max_tokens=64)
            raw = data.get("class")
            if isinstance(raw, str) and raw in valid:
                return TriageResult(FailureClass(raw), "llm")
        except BrainUnavailable:
            pass
        return TriageResult(FailureClass.UNKNOWN, "fallback", "LLM 分类失败或越出闭集")

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
