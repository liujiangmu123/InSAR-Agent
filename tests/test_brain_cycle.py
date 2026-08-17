"""B3 验收(Brain.cycle):自主循环单周期决策的契约测试(LOOP-CONTRACT §3)。

不依赖 B2 的 provider.chat 落地:注入「带 chat 方法的假 provider」,按 ChatOutcome
契约形状(content/tool_calls/finish_reason/route_index 四字段)返回脚本应答。
盯防:动作闭集(10 项,与 CYCLE_ACTION_TYPES 对齐)、say 收束、
越界动作丢弃留 say、坏形状整体拒绝(BrainUnavailable)、route_pin 透传、
enabled 闸门、user 消息形状(目标/状态/逐周期摘要,每条硬截 300 字)、
converse 校验纪律的复用与循环专属三动作(search_data/inspect_file/thinking)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from insar_agent.brain.facade import (
    CONVERSE_ACTION_TYPES,
    CYCLE_ACTION_TYPES,
    CYCLE_SYSTEM,
    Brain,
)
from insar_agent.brain.provider import BrainTruncated, BrainUnavailable
from insar_agent.registry.capabilities import REGISTRY

#: 事件契约钉死的 10 项动作闭集(LOOP-CONTRACT §1,顺序随前端 ACTION_META)
CONTRACT_ACTIONS = ("search_data", "inspect_file", "check_env", "list_data", "status",
                    "plan", "execute", "set_params", "set_method", "thinking",
                    "install_engine", "list_files",
                    "learn_tool", "search_docs", "probe_scratch")


@dataclass
class FakeOutcome:
    """ChatOutcome 的契约形状替身(LOOP-CONTRACT §2 四字段,不 import B2 实现)。"""

    content: str
    tool_calls: list = field(default_factory=list)
    finish_reason: str = "stop"
    route_index: int = 0


class ChatStub:
    """带 chat 方法的假 provider:记录每次调用的 messages/kwargs,按脚本应答。

    签名对齐 LOOP-CONTRACT §2 的 provider.chat;json_only/max_tokens 的缺省值
    故意偏离契约(False/-1),以便断言 cycle 显式传了 json_only=True 与
    max_tokens=2048(而不是碰巧吃到默认值)。
    """

    def __init__(self, script, *, enabled=True):
        self._script = list(script)
        self.enabled = enabled
        self.calls: list[dict] = []

    def chat(self, messages, *, tools=None, max_tokens=-1, json_only=False,
             route_pin=None):
        self.calls.append({"messages": messages, "tools": tools,
                           "max_tokens": max_tokens, "json_only": json_only,
                           "route_pin": route_pin})
        if not self._script:
            raise AssertionError("多余的 chat 调用(脚本已用尽)")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def outcome_of(obj, *, route_index=0) -> FakeOutcome:
    """把期望的 LLM JSON 包成 ChatOutcome 形状(content 是 JSON 文本)。"""
    return FakeOutcome(content=json.dumps(obj, ensure_ascii=False),
                       route_index=route_index)


def run_cycle(script, **kwargs):
    """默认参数的一次 cycle 调用,返回 (CycleResult, stub)。"""
    stub = ChatStub(script)
    brain = Brain(stub)
    defaults = dict(goal="盘点数据并给出处理建议", cycles_summary=[],
                    state_summary="runs=0", registry=REGISTRY)
    defaults.update(kwargs)
    return brain.cycle(**defaults), stub


# ---------------- 正常动作周期 / say 收束 ----------------

def test_normal_action_cycle():
    r, stub = run_cycle([outcome_of({"action": {"type": "check_env"},
                                     "say": "先做环境体检"}, route_index=1)])
    assert r.action == {"type": "check_env"}
    assert r.say == "先做环境体检"
    assert r.done is False and r.source == "llm"
    assert r.route_index == 1  # 驱动器靠它钉死路由(LOOP-CONTRACT §4)
    assert len(stub.calls) == 1


def test_say_only_collapses_turn():
    r, _ = run_cycle([outcome_of({"say": "数据齐了,建议按 quake 场景规划。"})])
    assert r.action is None and r.done is True and r.source == "llm"
    assert r.say == "数据齐了,建议按 quake 场景规划。"
    # action 显式为 null 与缺失同义
    r2, _ = run_cycle([outcome_of({"say": "收束", "action": None})])
    assert r2.action is None and r2.done is True


# ---------------- 越界动作:丢动作留 say ----------------

def test_out_of_set_action_dropped_keeps_say():
    r, _ = run_cycle([outcome_of({"action": {"type": "rm_dataset"},
                                  "say": "我来清理数据"})])
    assert r.action is None and r.done is True and r.source == "degraded"
    assert r.say.startswith("我来清理数据") and "拦截" in r.say


def test_action_not_a_dict_dropped():
    r, _ = run_cycle([outcome_of({"action": "check_env", "say": "查环境"})])
    assert r.action is None and r.done is True and r.source == "degraded"
    assert "拦截" in r.say


def test_converse_discipline_reused_for_shared_actions():
    """七个 converse 动作原样委托 _validate_converse_action(同源同纪律)。"""
    # 合法:plan 带场景闭集内的 key,可选自由文本透传
    r, _ = run_cycle([outcome_of({"action": {"type": "plan", "scenario": "quake",
                                             "region": "Ridgecrest"},
                                  "say": "开始规划"})])
    assert r.action == {"type": "plan", "scenario": "quake", "region": "Ridgecrest"}
    assert r.done is False
    # 合法:set_method 的 method 在候选集内
    r2, _ = run_cycle([outcome_of({"action": {"type": "set_method", "step": 6,
                                              "method": "icu"}, "say": "换方法"})])
    assert r2.action == {"type": "set_method", "step": 6, "method": "icu"}
    # 越界:幻觉参数名被 registry 校验拦截 → 丢动作留 say,标 degraded
    r3, _ = run_cycle([outcome_of({"action": {"type": "set_params", "step": 6,
                                              "params": {"magic_knob": 1}},
                                   "say": "调整参数"})])
    assert r3.action is None and r3.source == "degraded" and "拦截" in r3.say
    # 越界:场景不在闭集
    r4, _ = run_cycle([outcome_of({"action": {"type": "plan", "scenario": "臆造场景"},
                                   "say": "规划"})])
    assert r4.action is None and r4.source == "degraded" and "拦截" in r4.say


# ---------------- 循环专属三动作的同款纪律 ----------------

def test_search_data_optional_free_text_fields():
    r, _ = run_cycle([outcome_of({"action": {"type": "search_data",
                                             "query": " Ridgecrest SLC ",
                                             "region": 42,
                                             "timerange": "2019-06~2019-08",
                                             "extra": "smuggle"},
                                  "say": "去检索数据"})])
    # 非字符串静默丢弃、白名单外字段不透传、字符串去首尾空白
    assert r.action == {"type": "search_data", "query": "Ridgecrest SLC",
                        "timerange": "2019-06~2019-08"}
    assert r.done is False
    # 全可选:裸 search_data 合法(driver 侧降级为仅本地盘点)
    r2, _ = run_cycle([outcome_of({"action": {"type": "search_data"},
                                   "say": "先盘点本地"})])
    assert r2.action == {"type": "search_data"}


def test_inspect_file_requires_pathless_name():
    ok, _ = run_cycle([outcome_of({"action": {"type": "inspect_file",
                                              "name": " S1A_20190704.zip "},
                                   "say": "看看这个文件"})])
    assert ok.action == {"type": "inspect_file", "name": "S1A_20190704.zip"}
    assert ok.done is False
    # 红线「LLM 零路径」:分隔符/盘符/父级穿越/空值一律拦截
    for bad in ("C:\\data\\a.tif", "../secrets.txt", "data/slc/a.zip",
                "..", "", "   ", 7, None):
        r, _ = run_cycle([outcome_of({"action": {"type": "inspect_file", "name": bad},
                                      "say": "查看文件"})])
        assert r.action is None and r.source == "degraded", f"name={bad!r} 应被拦截"
        assert "拦截" in r.say


def test_learn_tool_and_probe_are_closed():
    ok, _ = run_cycle([outcome_of({"action": {"type": "learn_tool", "tool": " GDAL "},
                                   "say": "先学工具"})])
    assert ok.action == {"type": "learn_tool", "tool": "gdal"}
    path_hit, _ = run_cycle([outcome_of({
        "action": {"type": "learn_tool", "tool": "../x"}, "say": "学"})])
    assert path_hit.action is None and path_hit.source == "degraded"
    docs, _ = run_cycle([outcome_of({
        "action": {"type": "search_docs", "query": " unwrap "}, "say": "查文档"})])
    assert docs.action == {"type": "search_docs", "query": "unwrap"}
    probe, _ = run_cycle([outcome_of({
        "action": {"type": "probe_scratch", "kind": "import", "name": "numpy"},
        "say": "探针"})])
    assert probe.action == {"type": "probe_scratch", "kind": "import", "name": "numpy"}
    bad_kind, _ = run_cycle([outcome_of({
        "action": {"type": "probe_scratch", "kind": "bash", "name": "x"},
        "say": "探针"})])
    assert bad_kind.action is None and bad_kind.source == "degraded"


def test_inspect_file_step_form_reaches_closed_set():
    """inspect_file 的 step 形态(P2-10):与 name 二选一,step 优先。

    此前校验器只认 name,driver 的步骤日志/产物分支经真实 facade 永远不可达
    (死代码)—— step(int,registry 闭集)现在是合法形态。
    """
    ok, _ = run_cycle([outcome_of({"action": {"type": "inspect_file", "step": 3},
                                   "say": "查第 3 步"})])
    assert ok.action == {"type": "inspect_file", "step": 3}
    assert ok.done is False
    # 两者同给:step 优先,name 不透传(归一化 = 白名单字段)
    both, _ = run_cycle([outcome_of({"action": {"type": "inspect_file", "step": 2,
                                                "name": "a.tif"},
                                     "say": "看"})])
    assert both.action == {"type": "inspect_file", "step": 2}
    # step 越界(0/12 出 registry;bool/字符串/浮点不是步骤号)一律拦截,
    # 不静默退回 name 分支
    for bad in (0, 12, True, "3", 3.0):
        r, _ = run_cycle([outcome_of({"action": {"type": "inspect_file", "step": bad},
                                      "say": "查"})])
        assert r.action is None and r.source == "degraded", f"step={bad!r} 应被拦截"
        assert "拦截" in r.say


def test_thinking_action_normalized_no_extra_fields():
    r, _ = run_cycle([outcome_of({"action": {"type": "thinking", "smuggle": "x"},
                                  "say": "我梳理一下思路"})])
    assert r.action == {"type": "thinking"} and r.done is False


# ---------------- 坏形状 / 截断:整体拒绝 ----------------

def test_missing_say_is_bad_shape():
    with pytest.raises(BrainUnavailable):
        run_cycle([outcome_of({"action": {"type": "status"}})])


def test_blank_or_nonstring_say_is_bad_shape():
    with pytest.raises(BrainUnavailable):
        run_cycle([outcome_of({"say": "   "})])
    with pytest.raises(BrainUnavailable):
        run_cycle([outcome_of({"say": 42})])


def test_non_json_content_is_bad_shape():
    with pytest.raises(BrainUnavailable):
        run_cycle([FakeOutcome(content="好的,我这就去查(这不是 JSON)")])


def test_non_object_json_is_bad_shape():
    with pytest.raises(BrainUnavailable):
        run_cycle([FakeOutcome(content='["say", "顶层是数组"]')])


def test_truncation_propagates_as_brain_truncated():
    """截断语义沿用:provider.chat 抛 BrainTruncated,cycle 原样上抛(整体拒绝)。"""
    with pytest.raises(BrainTruncated):
        run_cycle([BrainTruncated("输出被 token 上限截断")])
    assert issubclass(BrainTruncated, BrainUnavailable)  # 调用方可统一按降级捕获


# ---------------- route_pin 透传 / enabled 闸门 ----------------

def test_route_pin_passthrough_and_json_only():
    r, stub = run_cycle([outcome_of({"say": "好"}, route_index=1)], route_pin=1)
    call = stub.calls[0]
    assert call["route_pin"] == 1          # 钉死路由原样透传
    assert call["json_only"] is True       # 显式 JSON 模式(stub 缺省是 False)
    assert call["max_tokens"] == 2048      # 显式 token 预算(stub 缺省是 -1)
    assert call["tools"] is None           # cycle 不带工具
    assert r.route_index == 1
    # 缺省不钉:route_pin=None(首周期语义)
    _, stub2 = run_cycle([outcome_of({"say": "好"})])
    assert stub2.calls[0]["route_pin"] is None


def test_disabled_gate_matches_converse():
    with pytest.raises(BrainUnavailable):
        Brain(None).cycle(goal="g", cycles_summary=[], state_summary="",
                          registry=REGISTRY)
    stub = ChatStub([], enabled=False)
    with pytest.raises(BrainUnavailable):
        Brain(stub).cycle(goal="g", cycles_summary=[], state_summary="",
                          registry=REGISTRY)
    assert stub.calls == []  # 闸门在前:一次 chat 都不发


# ---------------- user 消息形状:目标 + 状态 + 逐周期摘要 ----------------

def test_user_message_shape_and_per_entry_budget():
    long_tail = "x" * 300 + "OVERFLOW"
    r, stub = run_cycle([outcome_of({"say": "收到"})],
                        goal="处理 2019 年干旱区形变",
                        state_summary="引擎就绪;runs=1",
                        cycles_summary=["盘点到 3 个数据集", long_tail])
    assert r.done is True
    msgs = stub.calls[0]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user"]  # 不带对话历史
    user = msgs[1]["content"]
    assert "【回合目标】" in user and "处理 2019 年干旱区形变" in user
    assert "【系统状态】" in user and "引擎就绪;runs=1" in user
    assert "【已完成周期】" in user and "1. 盘点到 3 个数据集" in user
    assert "2. " + "x" * 300 in user       # 逐条编号的结构化摘要
    assert "OVERFLOW" not in user          # 每条硬截 300 字
    # system 保持静态:动态内容(目标/状态/摘要)绝不进 system(prompt 缓存友好)
    system = msgs[0]["content"]
    assert "处理 2019 年干旱区形变" not in system and "盘点到 3 个数据集" not in system


def test_first_cycle_has_empty_summary_placeholder():
    _, stub = run_cycle([outcome_of({"say": "收到"})], cycles_summary=[])
    user = stub.calls[0]["messages"][1]["content"]
    assert "【已完成周期】" in user and "第 1 个周期" in user


# ---------------- 提示词:动作闭集完整性(与前端 ACTION_META 对齐) ----------------

def test_cycle_action_types_closed_set():
    assert CYCLE_ACTION_TYPES == CONTRACT_ACTIONS
    assert set(CONVERSE_ACTION_TYPES) < set(CYCLE_ACTION_TYPES)  # 循环闭集是超集


def test_prompt_declares_every_action_and_stays_small():
    for name in CONTRACT_ACTIONS:
        assert f'"{name}"' in CYCLE_SYSTEM, f"提示词缺动作 {name}"
    assert len(CYCLE_SYSTEM.encode("utf-8")) < 2048  # 契约:静态循环提示词 <2KB
    # 静态模板仅含两个 replace 占位符(闭集内容按 registry 填充,同 CONVERSE_SYSTEM)
    assert "{SCENARIO_KEYS}" in CYCLE_SYSTEM and "{STEP_LINES}" in CYCLE_SYSTEM
    # 红线「LLM 零命令零路径」:提示词声明纪律,但自身绝不携带具体命令/引擎名/路径
    for banned in ("snaphu", "isce", "mintpy", "gdal", "python", "bash", "pip",
                   "http", "c:\\", "/usr", "/data", "./"):
        assert banned not in CYCLE_SYSTEM.lower(), f"提示词不应含 {banned!r}"


def test_cycle_on_delta_taps_say_field():
    chunks: list[str] = []

    class StreamStub:
        enabled = True

        def chat_stream(self, messages, **kwargs):
            on_delta = kwargs.get("on_delta")
            raw = '{"say":"先检查环境","action":{"type":"check_env"}}'
            if on_delta:
                on_delta(raw)
            return FakeOutcome(content=raw)

    result = Brain(StreamStub()).cycle(
        goal="x", cycles_summary=[], state_summary="s", registry=REGISTRY,
        on_delta=chunks.append)
    assert result.action == {"type": "check_env"}
    assert "".join(chunks) == "先检查环境"


def test_cycle_prefers_chat_stream_when_present():
    """上游决策走流式:有 chat_stream 时不再走整段 chat。"""

    class StreamStub:
        enabled = True
        stream_calls = 0
        chat_calls = 0

        def chat_stream(self, messages, **kwargs):
            self.stream_calls += 1
            return FakeOutcome(content='{"say":"流式","action":{"type":"status"}}')

        def chat(self, messages, **kwargs):
            self.chat_calls += 1
            return FakeOutcome(content='{"say":"非流"}')

    stub = StreamStub()
    result = Brain(stub).cycle(goal="x", cycles_summary=[], state_summary="s",
                               registry=REGISTRY)
    assert stub.stream_calls == 1 and stub.chat_calls == 0
    assert result.action == {"type": "status"} and result.say == "流式"


def test_install_engine_and_list_files_validate():
    r, _ = run_cycle([outcome_of({"action": {"type": "install_engine", "engine": "gdal"},
                                  "say": "装 gdal"})])
    assert r.action == {"type": "install_engine", "engine": "gdal"}
    r2, _ = run_cycle([outcome_of({"action": {"type": "list_files"}, "say": "看目录"})])
    assert r2.action == {"type": "list_files"}
    r3, _ = run_cycle([outcome_of({"action": {"type": "install_engine", "engine": "nope"},
                                   "say": "装一个不存在的"})])
    assert r3.done is True and r3.source == "degraded"
