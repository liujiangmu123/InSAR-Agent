"""Phase 4 验收(brain):规则优先、闭集约束、越界拒绝、截断整体拒绝、全职责可降级。"""

import pytest

from insar_agent.brain.facade import Brain
from insar_agent.brain.provider import BrainTruncated, BrainUnavailable, LLMProvider, LLMRoute
from insar_agent.core.failures import FailureClass, classify_log, error_window
from insar_agent.planner.feasibility import narrow_methods
from insar_agent.registry.capabilities import REGISTRY
from insar_agent.runtime.probe import ProbeResult


class FakeProvider(LLMProvider):
    """按脚本返回响应的假 LLM(不走网络)。"""

    def __init__(self, responses):
        super().__init__(routes=[LLMRoute("http://fake", "", "fake-model")])
        self._responses = list(responses)
        self.calls = 0

    def complete_json(self, *, system, user, max_tokens=512):
        self.calls += 1
        if not self._responses:
            raise BrainUnavailable("no more scripted responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def full_probe():
    return ProbeResult(engines={"snaphu": "2.0.7", "isce2": "2.6.5", "unw3d": None,
                                "mintpy": "1.6.4"},
                       credentials={})


# ---------------- 失败分类:规则优先 ----------------

def test_triage_rules_hit_without_llm():
    brain = Brain(None)
    assert brain.triage("... No space left on device ...").failure_class == FailureClass.DISK_FULL
    assert brain.triage("process Killed by OOM").failure_class == FailureClass.OOM
    assert brain.triage("HTTP 401 Unauthorized").failure_class == FailureClass.AUTH_EXPIRED
    assert brain.triage("connection timed out").failure_class == FailureClass.NETWORK_TRANSIENT
    r = brain.triage("某种完全没见过的输出")
    assert r.failure_class == FailureClass.UNKNOWN and r.source == "fallback"


def test_triage_rules_order_sensitive():
    # 同时命中 OOM 与 timeout 词面 → 先匹配的规则(OOM 在前)生效
    assert classify_log("Killed after connection timed out") == FailureClass.OOM


def test_triage_llm_closed_set():
    brain = Brain(FakeProvider([{"class": "service_down"}]))
    assert brain.triage("很奇怪的错").failure_class == FailureClass.SERVICE_DOWN
    brain2 = Brain(FakeProvider([{"class": "我发明的新类别"}]))
    assert brain2.triage("很奇怪的错").failure_class == FailureClass.UNKNOWN


def test_error_window():
    log = "\n".join([f"line {i}" for i in range(100)] + ["FATAL ERROR: boom"] +
                    [f"tail {i}" for i in range(10)])
    window = error_window(log, context=3)
    assert "FATAL ERROR" in window and "line 0" not in window


# ---------------- select:候选集内枚举索引 ----------------

def _feas6():
    return narrow_methods(REGISTRY[6], full_probe())


def test_select_without_llm_uses_recommend():
    brain = Brain(None)
    pick = brain.select(REGISTRY[6], _feas6())
    assert pick.method_id == "snaphu_mcf" and pick.source == "recommend"


def test_select_llm_valid_choice():
    ok = [f for f in _feas6() if f.ok]
    brain = Brain(FakeProvider([{"choice": 1, "reason": "精度优先"}]))
    pick = brain.select(REGISTRY[6], _feas6())
    assert pick.method_id == ok[1].method.id and pick.source == "llm"


def test_select_out_of_range_retry_then_degrade():
    brain = Brain(FakeProvider([{"choice": 99}, {"choice": -1}]))
    pick = brain.select(REGISTRY[6], _feas6())
    assert pick.source == "recommend"  # 两次越界 → 降级,绝不越出候选集


def test_select_truncated_rejected_entirely():
    """absorb-E9:截断输出即使可解析也整体拒绝 → 降级路径。"""
    brain = Brain(FakeProvider([BrainTruncated("length")]))
    pick = brain.select(REGISTRY[6], _feas6())
    assert pick.source == "recommend"


def test_select_single_choice_short_circuits():
    feas = [f for f in _feas6() if f.method.id == "snaphu_mcf"]
    provider = FakeProvider([])
    brain = Brain(provider)
    pick = brain.select(REGISTRY[6], feas)
    assert pick.source == "only_choice" and provider.calls == 0  # 不浪费 LLM 调用


# ---------------- intent / narrate 降级 ----------------

def test_intent_rules_first_no_llm_call():
    provider = FakeProvider([])
    brain = Brain(provider)
    r = brain.intent("玉树冻土时序分析")
    assert r.ok and r.scenario.key == "permafrost" and provider.calls == 0


def test_intent_unknown_needs_form_when_disabled():
    brain = Brain(None)
    r = brain.intent("帮我处理一下")
    assert not r.ok and r.need_form


def test_narrate_template_fallback_and_number_guard():
    brain = Brain(None)
    prov = {"run_id": "r", "scenario": "quake", "simulated": True,
            "environment": {"python": "3.14", "platform": "win32", "tools": {}},
            "steps": {"6": {"name": "解缠", "method": "snaphu_mcf",
                            "params": {"min_coherence": 0.25}}},
            "metrics": {}, "evidence": {"level": "runnable", "ladder": [], "reasons": []},
            "thresholds": {}}
    md, source = brain.narrate(prov)
    assert source == "template" and "snaphu_mcf" in md

    # LLM 改动数字 → 拒绝润色,回退模板(反幻觉护栏)
    brain2 = Brain(FakeProvider([{"markdown": "润色后的文字,但把 0.25 改成了 0.3"}]))
    md2, source2 = brain2.narrate(prov)
    assert source2 == "template"
