"""converse(Brain 第五职责)验收:对话驱动入口,mock provider,零真实网络。

覆盖:闲聊不动状态、plan 动作进既有规划流程、越界动作拦截、set_params/
set_method 入队与 run 归属、execute/status/check_env 映射、LLM 失败诚实降级、
JSON 坏形状容错(reply 缺失 → BrainUnavailable → 降级)、无 LLM 守护
(Brain(None) 不进 converse,规则路径行为与历史版本一致)。

二期新增覆盖:WSL 合并环境摘要(缓存热/冷两态,绝不触发真探测)、list_data
动作与数据集摘要注入、记忆注入(brain/memory 存在/缺失/坏契约三态)、
会话自动命名(触发与不触发)。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import types

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


def make_driver(store, workspace, brain, probe=None) -> Driver:
    return Driver(store, workspace=workspace, probe=probe or empty_probe(), poll=0.05,
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


def test_converse_list_data_action_normalized():
    # 二期闭集扩容:list_data 是无参数动作,多余字段不透传
    brain = brain_with({"reply": "我看看", "action": {"type": "list_data", "junk": 1}})
    r = brain.converse("x", history=[], state_summary="", registry=REGISTRY)
    assert r.action == {"type": "list_data"} and not r.rejected


def test_converse_single_arg_call_uses_default_registry():
    # 评测 harness 契约:Brain(provider).converse(text) 单参可调,
    # registry 缺省 = 全流水线闭集(越界照拦)
    brain = brain_with({"reply": "改", "action": {
        "type": "set_params", "step": 6, "params": {"min_coherence": 0.3}}})
    r = brain.converse("第 6 步阈值改 0.3")
    assert r.action == {"type": "set_params", "step": 6,
                        "params": {"min_coherence": 0.3}}
    brain2 = brain_with({"reply": "改", "action": {
        "type": "set_params", "step": 99, "params": {"min_coherence": 0.3}}})
    assert brain2.converse("x").action is None  # 缺省闭集下越界仍拦


def test_converse_session_title_sanitized_and_truncated():
    brain = brain_with({"reply": "好", "action": None,
                        "session_title": "  玉树冻土\n二〇二〇到二〇二三形变分析  "})
    r = brain.converse("x", history=[], state_summary="", registry=REGISTRY)
    assert len(r.session_title) == 12  # 压空白后硬截 12 字
    assert r.session_title.startswith("玉树冻土 二〇")
    # 非字符串标题一律弃用;缺字段 = 空串
    assert brain_with({"reply": "好", "action": None, "session_title": 123}) \
        .converse("x", history=[], state_summary="", registry=REGISTRY).session_title == ""
    assert brain_with({"reply": "好", "action": None}) \
        .converse("x", history=[], state_summary="", registry=REGISTRY).session_title == ""


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

def test_llm_failure_shows_call_error_and_does_not_degrade(store, workspace):
    brain = brain_with(BrainUnavailable("HTTP 401 Unauthorized: invalid api key"))
    driver = make_driver(store, workspace, brain)
    events = collect(driver.turn("s1", "Ridgecrest 地震同震形变分析"))
    assert events[0]["t"] == "note" and events[0].get("tone") == "bad"
    assert "HTTP 401" in events[0]["text"]
    assert "invalid api key" in events[0]["text"]
    assert not any(e["t"] in ("plan", "candidates", "ask") for e in events)
    assert store.latest_run("s1") is None
    hist = store.chat_history("s1")
    assert hist[-1]["role"] == "agent" and "HTTP 401" in hist[-1]["content"]


def test_llm_failure_unknown_text_does_not_open_form(store, workspace):
    brain = brain_with(BrainUnavailable("boom"))
    driver = make_driver(store, workspace, brain)
    events = collect(driver.turn("s1", "随便帮我搞一下"))
    assert events[0]["t"] == "note" and "boom" in events[0]["text"]
    assert not any(e["t"] == "ask" for e in events)


def test_bad_json_shape_shows_call_error(store, workspace):
    brain = brain_with({"action": None})  # 缺 reply → BrainUnavailable
    driver = make_driver(store, workspace, brain)
    events = collect(driver.turn("s1", "你好"))
    assert events[0]["t"] == "note" and events[0].get("tone") == "bad"
    assert "reply" in events[0]["text"] or "JSON" in events[0]["text"] or events[0]["text"]
    assert not any(e["t"] == "ask" for e in events)


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
    assert store.get_session("s2")["name"] == "s2"  # 无 LLM 绝不自动改名


# ---------------- 二期:WSL 合并环境摘要 ----------------

def wsl_cache_entry(**engines):
    """构造 probe_wsl_engines 形状的结果(ok=True)。engines 传 名字=版本(None=缺失)。"""
    return {"ok": True, "distro": "insar", "error": None,
            "engine_prefix": "/opt/miniforge3/envs/insar",
            "engines": {name: {"present": ver is not None,
                               "path": f"/usr/bin/{name}" if ver else None,
                               "version": ver, "error": None}
                        for name, ver in engines.items()}}


def warm_wsl_cache(entry):
    """把结果灌进 wsl_probe 的模块级 TTL 缓存(conftest 每个测试自动清空)。"""
    from insar_agent.runtime import wsl_probe
    from insar_agent.runtime.backend_select import wsl_distro

    wsl_probe._PROBE_CACHE[wsl_distro()] = (time.monotonic(), entry)


def env_text_of(events) -> str:
    say = next(e for e in events if e["t"] == "say")
    return say["parts"][1]


def test_check_env_merges_warm_wsl_cache(store, workspace):
    # 实测缺口(2026-08-13 浏览器):本机探测把 WSL 里可用的 isce2/snaphu 报缺失。
    # 缓存热 → 合并 WSL 口径,标注来源,WSL 在位引擎不再进缺失清单。
    warm_wsl_cache(wsl_cache_entry(isce2="2.6.3", mintpy=None, snaphu="2.0.6"))
    brain = brain_with({"reply": "环境是这样。", "action": {"type": "check_env"}})
    driver = make_driver(store, workspace, brain)
    text = env_text_of(collect(driver.turn("s1", "环境咋样?")))
    assert "isce2 2.6.3(WSL)" in text and "snaphu 2.0.6(WSL)" in text
    assert "未预热" not in text
    missing = text.split("缺失:", 1)[1]
    assert "isce2" not in missing and "snaphu" not in missing
    assert "mintpy" in missing  # 本机与 WSL 都没有 → 仍如实报缺失


def test_check_env_cold_wsl_cache_annotates_and_never_probes(store, workspace):
    # 缓存冷:不阻塞对话、不触发真探测(约 20s),如实标注「未预热」;
    # 缺失清单退回本机口径(conftest 已保证缓存为空)
    brain = brain_with({"reply": "我看看。", "action": {"type": "check_env"}})
    driver = make_driver(store, workspace, brain)
    text = env_text_of(collect(driver.turn("s1", "环境行不行?")))
    assert "未预热" in text
    assert "isce2" in text.split("缺失:", 1)[1]


def test_local_engine_version_beats_wsl_in_summary(store, workspace):
    # 本机优先、WSL 兜底(与 setup_router._engine 同序)
    warm_wsl_cache(wsl_cache_entry(mintpy="1.5.0"))
    probe = empty_probe()
    probe.engines["mintpy"] = "1.6.4"
    brain = brain_with({"reply": "看环境。", "action": {"type": "check_env"}})
    driver = make_driver(store, workspace, brain, probe=probe)
    text = env_text_of(collect(driver.turn("s1", "环境咋样")))
    assert "mintpy 1.6.4(本机)" in text and "1.5.0" not in text


def test_wsl_merge_never_mutates_shared_probe(store, workspace):
    # 合并只落在探测副本上:共享 self._probe 不动 → 规则路径逐字节不变的前提
    warm_wsl_cache(wsl_cache_entry(isce2="2.6.3"))
    brain = brain_with({"reply": "环境。", "action": {"type": "check_env"}})
    driver = make_driver(store, workspace, brain)
    collect(driver.turn("s1", "环境咋样"))
    assert "isce2 (wsl)" not in driver.probe().engines
    assert driver.probe().engines["isce2"] is None


# ---------------- 二期:list_data 动作与数据集摘要注入 ----------------

def make_hyp3_dataset(root):
    """按 HyP3 产品目录形态造一个最小数据集(文件名判型,内容不重要)。"""
    d = root / "S1AA_pair1"
    d.mkdir(parents=True)
    stem = "S1AA_20210101T000000_20210113T000000"
    (d / f"{stem}_unw_phase_clipped.tif").write_bytes(b"x" * 2048)
    (d / f"{stem}_corr_clipped.tif").write_bytes(b"y" * 1024)


@pytest.fixture()
def data_home(tmp_path, monkeypatch):
    """隔离数据集扫描根:INSAR_HOME 指向空目录,INSAR_DATA_DIR 指向专用数据根。"""
    monkeypatch.setenv("INSAR_HOME", str(tmp_path / "home"))
    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setenv("INSAR_DATA_DIR", str(data_root))
    return data_root


def test_list_data_action_renders_dataset_summary(store, workspace, data_home):
    make_hyp3_dataset(data_home)
    brain = brain_with({"reply": "我盘了下本地数据。", "action": {"type": "list_data"}})
    driver = make_driver(store, workspace, brain)
    events = collect(driver.turn("s1", "我本地都有什么数据?"))
    say = next(e for e in events if e["t"] == "say")
    assert say["parts"][0] == "我盘了下本地数据。"
    text = say["parts"][1]
    assert "S1AA_pair1" in text and "HyP3 产品" in text  # 名称与类型
    assert "3 KB" in text  # 大小(2048+1024 字节)
    assert "2021-01-01~2021-01-13" in text  # 日期范围
    assert store.latest_run("s1") is None  # 纯查询不碰 run/plan 状态
    assert "S1AA_pair1" in store.chat_history("s1")[-1]["content"]  # 落聊天历史


def test_list_data_without_datasets(store, workspace, data_home):
    brain = brain_with({"reply": "我查查。", "action": {"type": "list_data"}})
    driver = make_driver(store, workspace, brain)
    events = collect(driver.turn("s1", "有数据吗"))
    say = next(e for e in events if e["t"] == "say")
    assert "未发现数据集" in say["parts"][1]
    assert "INSAR_DATA_DIR" in say["parts"][1]  # 给出补救指引


def test_converse_state_injects_dataset_summary_line(store, workspace, data_home):
    make_hyp3_dataset(data_home)
    provider = ConverseScriptProvider([{"reply": "嗯。", "action": None}])
    driver = make_driver(store, workspace, Brain(provider))
    collect(driver.turn("s1", "你好"))
    user = provider.converse_calls[0]["user"]
    assert "数据集:共 1 个(HyP3 产品 1 个)" in user


# ---------------- 二期:记忆注入(契约防御式) ----------------

def fake_memory_module(fn):
    mod = types.ModuleType("insar_agent.brain.memory")
    mod.get_context_snippets = fn
    return mod


def test_memory_snippets_injected_when_module_present(store, workspace, monkeypatch):
    calls: dict = {}

    def get_context_snippets(store_arg, session_id, limit=5):
        calls["args"] = (store_arg, session_id, limit)
        return ["常用区域:玉树", "偏好 SBAS"]

    monkeypatch.setitem(sys.modules, "insar_agent.brain.memory",
                        fake_memory_module(get_context_snippets))
    provider = ConverseScriptProvider([{"reply": "好。", "action": None}])
    driver = make_driver(store, workspace, Brain(provider))
    collect(driver.turn("s1", "你好"))
    user = provider.converse_calls[0]["user"]
    assert "用户记忆:常用区域:玉树;偏好 SBAS" in user
    assert calls["args"] == (store, "s1", 5)  # 契约按约定形参调用


def test_memory_module_absent_zero_impact(store, workspace, monkeypatch):
    # sys.modules 置 None:import 机制必抛 ImportError —— 无论并行代理的
    # brain/memory.py 是否已落地,本测试都强制走「模块缺失」路径
    monkeypatch.setitem(sys.modules, "insar_agent.brain.memory", None)
    provider = ConverseScriptProvider([{"reply": "好。", "action": None}])
    driver = make_driver(store, workspace, Brain(provider))
    events = collect(driver.turn("s1", "你好"))
    assert [e["t"] for e in events] == ["say"]  # 对话完全正常
    assert "用户记忆" not in provider.converse_calls[0]["user"]


@pytest.mark.parametrize("bad_fn", [
    lambda *a, **k: (_ for _ in ()).throw(RuntimeError("记忆库炸了")),  # 调用抛错
    lambda *a, **k: "不是列表",                                        # 返回形状不对
    lambda *a, **k: [123, "", "  "],                                   # 列表里没有可用片段
])
def test_memory_contract_violation_zero_impact(store, workspace, monkeypatch, bad_fn):
    monkeypatch.setitem(sys.modules, "insar_agent.brain.memory",
                        fake_memory_module(bad_fn))
    provider = ConverseScriptProvider([{"reply": "好。", "action": None}])
    driver = make_driver(store, workspace, Brain(provider))
    events = collect(driver.turn("s1", "你好"))
    assert [e["t"] for e in events] == ["say"]
    assert "用户记忆" not in provider.converse_calls[0]["user"]


# ---------------- 二期:会话自动命名 ----------------

def test_first_message_auto_names_session(store, workspace):
    brain = brain_with({"reply": "好嘞,先聊聊需求。", "action": None,
                        "session_title": "玉树冻土形变分析"})
    driver = make_driver(store, workspace, brain)
    events = collect(driver.turn("s1", "想看看玉树冻土的形变"))
    assert store.get_session("s1")["name"] == "玉树冻土形变分析"
    assert any(e["t"] == "note" and "玉树冻土形变分析" in e["text"] for e in events)


def test_naming_hint_only_on_first_message_and_late_title_ignored(store, workspace):
    provider = ConverseScriptProvider([
        {"reply": "好。", "action": None, "session_title": "地震形变分析"},
        {"reply": "在呢。", "action": None, "session_title": "迟到的标题"},
    ])
    driver = make_driver(store, workspace, Brain(provider))
    collect(driver.turn("s1", "帮我看看地震"))
    assert store.get_session("s1")["name"] == "地震形变分析"
    collect(driver.turn("s1", "第二句"))
    # 非首条消息:提示不注入,LLM 硬给的标题也被忽略
    assert "会话命名" in provider.converse_calls[0]["user"]
    assert "会话命名" not in provider.converse_calls[1]["user"]
    assert store.get_session("s1")["name"] == "地震形变分析"


def test_first_message_without_title_then_never_renames(store, workspace):
    provider = ConverseScriptProvider([
        {"reply": "你好!", "action": None},  # 首条:LLM 没给标题 → 不改名
        {"reply": "嗯。", "action": None, "session_title": "补一个标题"},
    ])
    driver = make_driver(store, workspace, Brain(provider))
    collect(driver.turn("s1", "你好"))
    assert store.get_session("s1")["name"] == "s1"
    collect(driver.turn("s1", "再来一句"))
    assert store.get_session("s1")["name"] == "s1"  # 已非首条,永不追改


def test_custom_named_session_not_renamed(store, workspace):
    store.create_session("s1", "我的自定义会话")
    brain = brain_with({"reply": "嗨。", "action": None, "session_title": "抢名字"})
    driver = make_driver(store, workspace, brain)
    provider = brain.provider
    collect(driver.turn("s1", "你好"))
    assert store.get_session("s1")["name"] == "我的自定义会话"
    assert "会话命名" not in provider.converse_calls[0]["user"]  # 提示都不该出现
