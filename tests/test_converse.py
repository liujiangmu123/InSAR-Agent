"""converse(Brain 第五职责)验收:对话驱动入口,mock provider,零真实网络。

覆盖:闲聊不动状态、plan 动作进既有规划流程、越界动作拦截、set_params/
set_method 入队与 run 归属、execute/status/check_env 映射、LLM 失败诚实降级、
JSON 坏形状容错(reply 缺失 → BrainUnavailable → 降级)、无 LLM 守护
(Brain(None) 不进 converse,规则路径行为与历史版本一致)。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from insar_agent.brain.facade import Brain
from insar_agent.brain.provider import BrainUnavailable, LLMProvider, LLMRoute
from insar_agent.loop.driver import Driver
from insar_agent.registry.capabilities import REGISTRY
from insar_agent.runtime.probe import ProbeResult


class ConverseScriptProvider(LLMProvider):
    """只对 converse 调用回脚本的假 LLM(不走网络)。

    其他职责(select/intent/triage)一律 BrainUnavailable → 走各自既有降级
    路径(recommend/表单),测试聚焦 converse 本身;以 system prompt 里的
    助手身份行区分调用来源,免受规划流程中 select 调用消耗脚本的干扰。
    """

    def __init__(self, responses):
        super().__init__(routes=[LLMRoute("http://fake", "", "fake-model")])
        self._responses = list(responses)
        self.converse_calls: list[dict] = []

    def complete_json(self, *, system, user, max_tokens=512):
        if "InSAR 数据处理助手" not in system:
            raise BrainUnavailable("非 converse 调用,走降级")
        self.converse_calls.append(
            {"system": system, "user": user, "max_tokens": max_tokens})
        if not self._responses:
            raise BrainUnavailable("no more scripted responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def brain_with(*responses) -> Brain:
    return Brain(ConverseScriptProvider(list(responses)))


def empty_probe():
    return ProbeResult(engines={"isce2": None, "mintpy": None, "snaphu": None, "gdal": None,
                                "snap": None, "pystamps": None, "pyaps": None},
                       credentials={"earthdata": False, "cds": False, "gacos": False},
                       disk_free_gb=100.0, cpu_count=8)


def make_driver(store, workspace, brain) -> Driver:
    return Driver(store, workspace=workspace, probe=empty_probe(), poll=0.05,
                  allow_simulated=True, brain=brain)


def collect(agen) -> list[dict]:
    async def _run():
        return [e async for e in agen]

    return asyncio.run(_run())


# ---------------- facade 层:闭集校验与 prompt 契约 ----------------

def test_converse_type_out_of_set_intercepted():
    brain = brain_with({"reply": "好的", "action": {"type": "rm_rf"}})
    r = brain.converse("x", history=[], state_summary="", registry=REGISTRY)
    assert r.action is None and r.rejected
    assert r.reply.endswith("(动作越界已拦截)")


def test_converse_plan_scenario_out_of_set_intercepted():
    brain = brain_with({"reply": "好", "action": {"type": "plan", "scenario": "mars_ice"}})
    r = brain.converse("x", history=[], state_summary="", registry=REGISTRY)
    assert r.action is None and "scenario" in r.rejected


def test_converse_step_bool_int_str_out_of_set_intercepted():
    # bool 是 int 子类(与 select 的 choice 拦截同款):true 不是步骤号,是幻觉
    for step in (True, 99, "6"):
        brain = brain_with({"reply": "改", "action": {
            "type": "set_params", "step": step, "params": {"min_coherence": 0.3}}})
        r = brain.converse("x", history=[], state_summary="", registry=REGISTRY)
        assert r.action is None, f"step={step!r} 应被拦截"


def test_converse_params_validated_by_registry():
    # 值域越界(min_coherence 0-1)与未声明参数名都走 registry 既有校验
    for params in ({"min_coherence": 99}, {"fake_param": 1}):
        brain = brain_with({"reply": "改", "action": {
            "type": "set_params", "step": 6, "params": params}})
        r = brain.converse("x", history=[], state_summary="", registry=REGISTRY)
        assert r.action is None and "参数校验失败" in r.rejected


def test_converse_method_out_of_set_intercepted():
    brain = brain_with({"reply": "换", "action": {
        "type": "set_method", "step": 6, "method": "quantum_unwrap"}})
    r = brain.converse("x", history=[], state_summary="", registry=REGISTRY)
    assert r.action is None and "method" in r.rejected


def test_converse_valid_action_normalized_to_whitelist_fields():
    brain = brain_with({"reply": "来", "action": {
        "type": "plan", "scenario": "quake", "region": "Ridgecrest", "junk": 1}})
    r = brain.converse("x", history=[], state_summary="", registry=REGISTRY)
    assert r.action == {"type": "plan", "scenario": "quake", "region": "Ridgecrest"}
    assert not r.rejected


def test_converse_reply_missing_raises_brain_unavailable():
    brain = brain_with({"action": None})  # 坏形状:缺 reply → 整体拒绝
    with pytest.raises(BrainUnavailable):
        brain.converse("x", history=[], state_summary="", registry=REGISTRY)


def test_converse_disabled_raises():
    with pytest.raises(BrainUnavailable):
        Brain(None).converse("x", history=[], state_summary="", registry=REGISTRY)


def test_converse_prompt_carries_state_history_and_budget():
    provider = ConverseScriptProvider([{"reply": "嗯", "action": None}])
    brain = Brain(provider)
    history = [{"role": "user", "content": f"消息{i}"} for i in range(20)]
    brain.converse("现在这句", history=history, state_summary="环境:X",
                   registry=REGISTRY)
    call = provider.converse_calls[0]
    assert call["max_tokens"] == 2048  # 推理型模型余量
    assert "环境:X" in call["user"] and "现在这句" in call["user"]
    # 滚动历史硬预算:只带最近 8 条
    assert "消息19" in call["user"] and "消息12" in call["user"]
    assert "消息11" not in call["user"]
    # 闭集进 system prompt:场景 key 与步骤方法候选
    assert "quake" in call["system"] and "snaphu_mcf" in call["system"]


# ---------------- driver 层:闲聊/动作映射 ----------------

def test_chat_action_null_does_not_touch_state(store, workspace):
    brain = brain_with({"reply": "你好!想处理哪批数据?", "action": None})
    driver = make_driver(store, workspace, brain)
    events = collect(driver.turn("s1", "你好呀"))
    assert [e["t"] for e in events] == ["say"]  # 纯聊天:无 thinking/plan/candidates
    assert events[0]["parts"] == ["你好!想处理哪批数据?"]
    assert store.latest_run("s1") is None  # 不碰 run/plan 状态
    assert [m["role"] for m in store.chat_history("s1")] == ["user", "agent"]


def test_out_of_set_action_becomes_chat_with_note(store, workspace):
    brain = brain_with({"reply": "好的", "action": {"type": "delete_everything"}})
    driver = make_driver(store, workspace, brain)
    events = collect(driver.turn("s1", "把所有东西删了"))
    assert [e["t"] for e in events] == ["say"]
    assert events[0]["parts"][0].endswith("(动作越界已拦截)")
    assert store.latest_run("s1") is None


def test_plan_action_enters_existing_planning_flow(store, workspace):
    brain = brain_with({"reply": "好,按同震场景来规划。",
                        "action": {"type": "plan", "scenario": "quake"}})
    driver = make_driver(store, workspace, brain)
    events = collect(driver.turn("s1", "帮我看看那次地震的形变"))
    kinds = [e["t"] for e in events]
    assert kinds[0] == "say"  # 过渡语先行
    assert "thinking" in kinds and "plan" in kinds and "candidates" in kinds
    run = store.latest_run("s1")
    assert run is not None and run["scenario"] == "quake"
    assert json.loads(run["intent"])["source"] == "converse"


def test_plan_action_region_timerange_reach_thinking(store, workspace):
    brain = brain_with({"reply": "安排。", "action": {
        "type": "plan", "scenario": "quake",
        "region": "Ridgecrest 北段", "timerange": "2019-06..2019-08"}})
    driver = make_driver(store, workspace, brain)
    events = collect(driver.turn("s1", "看下 Ridgecrest 北段 2019 年夏天的形变"))
    thinking = next(e for e in events if e["t"] == "thinking")
    assert "Ridgecrest 北段" in thinking["body"]
    assert "2019-06..2019-08" in thinking["body"]


def test_execute_action_maps_to_execute_entry(store, workspace):
    # 无 run 时委托给 execute 入口的既有检查(不重复造判断):note 说明先规划
    brain = brain_with({"reply": "开跑!", "action": {"type": "execute"}})
    driver = make_driver(store, workspace, brain)
    events = collect(driver.turn("s1", "跑起来"))
    assert events[0]["t"] == "say"
    assert any(e["t"] == "note" and "没有可执行的 run" in e["text"] for e in events)


def test_status_action_renders_run_state(store, workspace):
    brain = brain_with(
        {"reply": "先规划。", "action": {"type": "plan", "scenario": "quake"}},
        {"reply": "看下进度。", "action": {"type": "status"}},
    )
    driver = make_driver(store, workspace, brain)
    collect(driver.turn("s1", "分析地震形变"))
    run = store.latest_run("s1")
    events = collect(driver.turn("s1", "现在啥状态?"))
    say = next(e for e in events if e["t"] == "say")
    assert say["parts"][0] == "看下进度。"
    assert run["run_id"] in say["parts"][1] and "ready" in say["parts"][1]
    # 状态文本一并落聊天历史(重开会话可回看)
    assert run["run_id"] in store.chat_history("s1")[-1]["content"]


def test_status_action_without_run(store, workspace):
    brain = brain_with({"reply": "我看看。", "action": {"type": "status"}})
    driver = make_driver(store, workspace, brain)
    events = collect(driver.turn("s1", "进度如何?"))
    say = next(e for e in events if e["t"] == "say")
    assert "还没有 run" in say["parts"][1]
    assert store.latest_run("s1") is None


def test_check_env_action_renders_probe_summary(store, workspace):
    brain = brain_with({"reply": "环境是这样。", "action": {"type": "check_env"}})
    driver = make_driver(store, workspace, brain)
    events = collect(driver.turn("s1", "我的环境能跑吗?"))
    say = next(e for e in events if e["t"] == "say")
    assert "磁盘 100 GB 可用" in say["parts"][1]
    assert "缺失" in say["parts"][1]  # empty_probe 全缺:摘要必须如实报缺失


def test_set_params_queues_with_run_attribution(store, workspace):
    brain = brain_with(
        {"reply": "先规划。", "action": {"type": "plan", "scenario": "quake"}},
        {"reply": "把第 6 步相干性阈值改成 0.3。", "action": {
            "type": "set_params", "step": 6, "params": {"min_coherence": 0.3}}},
    )
    driver = make_driver(store, workspace, brain)
    collect(driver.turn("s1", "分析地震形变"))
    run = store.latest_run("s1")
    events = collect(driver.turn("s1", "第 6 步阈值改 0.3"))
    kinds = [e["t"] for e in events]
    assert "say" in kinds and "intervention" in kinds  # 入队留痕
    queued = store.due_actions("steer", run_id=run["run_id"], include_unattributed=False)
    assert len(queued) == 1
    assert queued[0]["action"] == "SET_PARAMS" and queued[0]["target"] == "6"
    assert queued[0]["payload"] == {"params": {"min_coherence": 0.3}}
    assert queued[0]["run_id"] == run["run_id"]  # 归属当前会话最近 run


def test_set_method_queues_with_run_attribution(store, workspace):
    brain = brain_with(
        {"reply": "先规划。", "action": {"type": "plan", "scenario": "quake"}},
        {"reply": "第 9 步换指数模型。", "action": {
            "type": "set_method", "step": 9, "method": "exponential"}},
    )
    driver = make_driver(store, workspace, brain)
    collect(driver.turn("s1", "分析地震形变"))
    run = store.latest_run("s1")
    collect(driver.turn("s1", "第 9 步换 exponential"))
    queued = store.due_actions("steer", run_id=run["run_id"], include_unattributed=False)
    assert len(queued) == 1
    assert queued[0]["action"] == "SET_METHOD"
    assert queued[0]["payload"] == {"method": "exponential"}


def test_set_params_without_run_warns_and_queues_nothing(store, workspace):
    brain = brain_with({"reply": "改一下。", "action": {
        "type": "set_params", "step": 6, "params": {"min_coherence": 0.3}}})
    driver = make_driver(store, workspace, brain)
    events = collect(driver.turn("s1", "第 6 步阈值改 0.3"))
    assert any(e["t"] == "note" and "无处归属" in e["text"] for e in events)
    assert store.due_actions("steer") == []


# ---------------- 降级与守护 ----------------

def test_llm_failure_degrades_honestly_then_rules_plan(store, workspace):
    brain = brain_with(BrainUnavailable("boom"))
    driver = make_driver(store, workspace, brain)
    events = collect(driver.turn("s1", "Ridgecrest 地震同震形变分析"))
    assert events[0]["t"] == "note" and "LLM 暂不可用" in events[0]["text"]  # 诚实提示
    kinds = [e["t"] for e in events]
    assert "plan" in kinds and "candidates" in kinds  # 规则路径认出场景,照常规划
    assert store.latest_run("s1")["scenario"] == "quake"


def test_llm_failure_unknown_text_falls_to_form(store, workspace):
    brain = brain_with(BrainUnavailable("boom"))
    driver = make_driver(store, workspace, brain)
    events = collect(driver.turn("s1", "随便帮我搞一下"))
    assert events[0]["t"] == "note" and "LLM 暂不可用" in events[0]["text"]
    assert events[-1]["t"] == "ask"  # 规则路径:识别不出 → 补充表单


def test_bad_json_shape_degrades_like_failure(store, workspace):
    brain = brain_with({"action": None})  # 缺 reply → BrainUnavailable → 降级
    driver = make_driver(store, workspace, brain)
    events = collect(driver.turn("s1", "你好"))
    assert events[0]["t"] == "note" and "LLM 暂不可用" in events[0]["text"]
    assert events[-1]["t"] == "ask"


def test_brain_none_rules_path_unchanged(store, workspace):
    """守护:Brain(None) 全降级,turn 行为与手动流水线时代一致(DESIGN.md:233)。"""
    driver = make_driver(store, workspace, Brain(None))
    events = collect(driver.turn("s1", "随便帮我搞一下"))
    assert [e["t"] for e in events] == ["ask"]  # 无 converse say,无降级 note
    assert events[0]["prompt"] == "无法从描述中识别场景,请补充:"

    events2 = collect(driver.turn("s2", "Ridgecrest 地震同震形变分析"))
    kinds = [e["t"] for e in events2]
    assert kinds[0] == "thinking"  # 首事件仍是 thinking,前面没有多出任何事件
    assert "plan" in kinds and "candidates" in kinds
    assert not any(e["t"] == "note" and "LLM" in e.get("text", "") for e in events2)
