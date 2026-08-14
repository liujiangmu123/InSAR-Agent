"""converse 流式旁路(WAVE-0814B §1.2)验收:假 provider,零真实网络。

覆盖:
  ⑨ on_delta=None 只调 complete_json,从不触碰 chat_stream(既有路径逐字节不变);
  ⑩ on_delta 给定 → chat_stream(json_only=True),外发的 delta 拼起来 == 终帧 reply;
  ⑪ _StreamingFieldTap 专项:转义跨帧 / \\uXXXX 跨帧 / emoji 代理对跨帧 /
     reply 含转义引号 / 字段缺失永不外发 / 孤立高位代理裁剪(NDJSON 编码层前科);
  ⑫ provider 缺可用 chat_stream(旧式假 provider / 只改写 complete_json 的
     LLMProvider 子类)→ 静默回落 complete_json,绝不出网;
  附:终帧仍全量校验(坏 JSON / 缺 reply / 动作越界),零外发失败降级非流式
     重试一次,外发过 delta 的失败原样上抛绝不重放,截断整体拒绝。
"""

from __future__ import annotations

import pytest

from insar_agent.brain.facade import Brain, _StreamingFieldTap
from insar_agent.brain.provider import (
    BrainTruncated,
    BrainUnavailable,
    ChatOutcome,
    LLMProvider,
    LLMRoute,
)
from insar_agent.registry.capabilities import REGISTRY

# ---------------- 假 provider 三态 ----------------


class StreamScriptProvider(LLMProvider):
    """chat_stream 按脚本切片外发的假 provider;complete_json 一碰即炸
    (钉死 on_delta 路径只走 chat_stream,回落即测试失败)。"""

    def __init__(self, chunks, *, fail_after: Exception | None = None):
        super().__init__(routes=[LLMRoute("http://fake", "", "fake-model")])
        self.chunks = list(chunks)
        self.fail_after = fail_after
        self.stream_calls: list[dict] = []

    def complete_json(self, *, system, user, max_tokens=512):
        raise AssertionError("on_delta 路径不应触碰 complete_json")

    def chat_stream(self, messages, *, tools=None, max_tokens=-1, json_only=False,
                    route_pin=None, on_delta=None):
        # json_only/max_tokens 缺省值故意偏离契约(False/-1),
        # 以便断言 facade 显式传了 json_only=True 与 max_tokens=2048
        self.stream_calls.append({"messages": messages, "tools": tools,
                                  "max_tokens": max_tokens, "json_only": json_only,
                                  "route_pin": route_pin})
        for chunk in self.chunks:
            if on_delta is not None:
                on_delta(chunk)
        if self.fail_after is not None:
            raise self.fail_after
        return ChatOutcome(content="".join(self.chunks), tool_calls=[],
                           finish_reason="stop", route_index=0)


class InheritedStreamProvider(LLMProvider):
    """test_converse.py 假 provider 形态:子类只改写 complete_json,chat_stream
    沿继承链解析到 LLMProvider 真实现(会对占位路由发真网络请求)——
    防御闸门必须按「缺 chat_stream」处置。"""

    def __init__(self, data):
        super().__init__(routes=[LLMRoute("http://fake", "", "fake-model")])
        self.data = data
        self.json_calls = 0

    def complete_json(self, *, system, user, max_tokens=512):
        self.json_calls += 1
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


class JsonOnlyProvider:
    """旧式最小 provider:只有 complete_json,连 LLMProvider 都不继承。"""

    enabled = True

    def __init__(self, data):
        self.data = data
        self.json_calls = 0

    def complete_json(self, *, system, user, max_tokens=512):
        self.json_calls += 1
        return self.data


class StreamFailJsonOkProvider(LLMProvider):
    """chat_stream 零外发即失败、complete_json 正常应答(验证零外发前缀降级)。"""

    def __init__(self, data, error: Exception):
        super().__init__(routes=[LLMRoute("http://fake", "", "fake-model")])
        self.data = data
        self.error = error
        self.json_calls = 0

    def complete_json(self, *, system, user, max_tokens=512):
        self.json_calls += 1
        return self.data

    def chat_stream(self, messages, **kwargs):
        raise self.error


# ---------------- ⑨ on_delta=None:只走 complete_json ----------------

def test_on_delta_none_only_complete_json_never_chat_stream(monkeypatch):
    provider = InheritedStreamProvider({"reply": "你好", "action": None})
    touched: list = []
    monkeypatch.setattr(provider, "chat_stream",
                        lambda *a, **k: touched.append(1), raising=False)

    r = Brain(provider).converse("嗨", history=[], state_summary="", registry=REGISTRY)

    assert r.reply == "你好" and r.action is None
    assert touched == []                        # 缺省路径从不触碰 chat_stream
    assert provider.json_calls == 1


# ---------------- ⑩ on_delta 给定:走 chat_stream,delta == 终帧 reply ----------------

def test_on_delta_streams_reply_and_matches_final_frame():
    chunks = ['{"re', 'ply": "你好', ',数据已经', '就绪了", "action"', ': null}']
    provider = StreamScriptProvider(chunks)
    got: list[str] = []

    r = Brain(provider).converse("在吗", history=[], state_summary="状态X",
                                 registry=REGISTRY, on_delta=got.append)

    assert r.reply == "你好,数据已经就绪了" and r.action is None
    assert "".join(got) == r.reply              # 外发增量拼起来 == 终帧 reply
    call = provider.stream_calls[0]
    assert call["json_only"] is True            # 显式 JSON 模式(桩缺省是 False)
    assert call["max_tokens"] == 2048           # 显式 token 预算(桩缺省是 -1)
    assert call["tools"] is None
    assert [m["role"] for m in call["messages"]] == ["system", "user"]
    assert "InSAR 数据处理助手" in call["messages"][0]["content"]  # converse 提示词
    assert "状态X" in call["messages"][1]["content"]
    assert "在吗" in call["messages"][1]["content"]


def test_stream_final_frame_validates_action_closed_set():
    provider = StreamScriptProvider(['{"reply": "好的", "action": {"type": "rm_rf"}}'])
    got: list[str] = []

    r = Brain(provider).converse("删库", history=[], state_summary="",
                                 registry=REGISTRY, on_delta=got.append)

    assert r.action is None and r.rejected      # 终帧闭集校验照常拦截
    assert r.reply.endswith("(动作越界已拦截)")
    assert "".join(got) == "好的"               # 旁路只外发 reply 字段值


def test_stream_valid_action_and_session_title_passthrough():
    provider = StreamScriptProvider(
        ['{"reply": "来规划", "action": {"type": "plan", "scenario": "quake"},'
         ' "session_title": "地震形变分析"}'])
    r = Brain(provider).converse("看地震", history=[], state_summary="",
                                 registry=REGISTRY, on_delta=lambda s: None)
    assert r.action == {"type": "plan", "scenario": "quake"}
    assert r.session_title == "地震形变分析"


def test_stream_bad_final_frame_raises_unavailable():
    # 终帧语义与 complete_json 对齐:非 JSON / 顶层非对象 / 缺 reply → 整体拒绝
    for chunks in (["这不是 JSON"],
                   ['["reply", "顶层是数组"]'],
                   ['{"say": "缺 reply"}']):
        provider = StreamScriptProvider(chunks)
        with pytest.raises(BrainUnavailable):
            Brain(provider).converse("x", history=[], state_summary="",
                                     registry=REGISTRY, on_delta=lambda s: None)


# ---------------- 失败语义:零外发降级重试 / 已外发绝不重放 / 截断整体拒绝 ----------------

def test_zero_emission_stream_failure_falls_back_to_complete_json():
    provider = StreamFailJsonOkProvider({"reply": "非流式接管", "action": None},
                                        BrainUnavailable("建流失败"))
    got: list[str] = []

    r = Brain(provider).converse("x", history=[], state_summary="",
                                 registry=REGISTRY, on_delta=got.append)

    assert r.reply == "非流式接管"              # 零外发前缀内:静默降级重试一次
    assert provider.json_calls == 1 and got == []


def test_emitted_stream_failure_raises_never_replays():
    # tap 已把 reply 增量外发给用户 → 失败原样上抛;complete_json(AssertionError
    # 桩)绝不能被触碰 —— 重放会让用户看到重复文本
    provider = StreamScriptProvider(['{"reply": "半'],
                                    fail_after=BrainUnavailable("断流"))
    got: list[str] = []

    with pytest.raises(BrainUnavailable):
        Brain(provider).converse("x", history=[], state_summary="",
                                 registry=REGISTRY, on_delta=got.append)
    assert got == ["半"]                        # 确实外发过


def test_truncated_stream_raises_without_retry():
    provider = StreamFailJsonOkProvider({"reply": "不该出现"},
                                        BrainTruncated("token 上限"))
    with pytest.raises(BrainTruncated):
        Brain(provider).converse("x", history=[], state_summary="",
                                 registry=REGISTRY, on_delta=lambda s: None)
    assert provider.json_calls == 0             # 截断重试解决不了 prompt 过长


# ---------------- ⑪ _StreamingFieldTap 专项 ----------------

def tap_run(chunks) -> list[str]:
    got: list[str] = []
    tap = _StreamingFieldTap("reply", got.append)
    for chunk in chunks:
        tap.feed(chunk)
    return got


def test_tap_backslash_escape_split_across_chunks():
    # \n 断在反斜杠后:结尾奇数个反斜杠先裁掉,下一 chunk 补齐后精确反转义
    got = tap_run(['{"reply": "a\\', 'nb"}'])
    assert got == ["a", "\nb"]


def test_tap_escaped_quote_split_across_chunks():
    # \" 断在反斜杠后:不能误判字符串闭合,也不能把反斜杠先漏出去
    got = tap_run(['{"reply": "说 \\', '"引\\"号"}'])
    assert got == ["说 ", '"引"号']


def test_tap_unicode_escape_split_across_chunks():
    # \u4e2d(中)断在第 3 个十六进制位:不完整 \uXXXX 整段暂扣
    got = tap_run(['{"reply": "十\\u4e2', 'd"}'])
    assert got == ["十", "中"]


def test_tap_surrogate_pair_split_across_chunks():
    # emoji 代理对(\ud83d\ude00 = 😀)断在两个转义之间:高位代理暂扣等配对
    got = tap_run(['{"reply": "笑\\ud83d', '\\ude00了"}'])
    assert got == ["笑", "😀了"]
    "".join(got).encode("utf-8")                # 每一段都必须可编码(NDJSON 层)


def test_tap_reply_with_escaped_quotes_single_chunk():
    got = tap_run(['{"reply": "他说 \\"到\\" 了", "action": null}'])
    assert got == ['他说 "到" 了']              # 值后内容(action)绝不外发


def test_tap_field_missing_never_forwards():
    assert tap_run(['{"say": "你好"}']) == []                    # 无 reply 字段
    assert tap_run(['{"note": "reply", "count": 3}']) == []      # 值恰好叫 reply
    assert tap_run(['{"reply": 42}']) == []                      # 值不是字符串
    assert tap_run(['{"action": {"reply": "内层"}}']) == []      # 嵌套里的同名键


def test_tap_orphan_high_surrogate_never_emitted():
    # 孤立高位代理:配对永远不来 → 绝不能把裸代理外发(UTF-8 编不了,
    # NDJSON 响应编码层裸断连,项目有前科);替换为 U+FFFD 后必须可编码
    for chunks in (['{"reply": "坏\\ud83d"}'],           # 值尾孤代理,单帧
                   ['{"reply": "\\ud83d', 'abc"}']):     # 暂扣后配对落空,跨帧
        got = tap_run(chunks)
        joined = "".join(got)
        assert not any(0xD800 <= ord(c) <= 0xDFFF for c in joined)
        joined.encode("utf-8")                  # 可编码 = 不会炸 NDJSON 层
        assert "\ufffd" in joined               # 孤代理被替换而非硬吞上下文
    assert tap_run(['{"reply": "\\ud83d', 'abc"}']) == ["\ufffdabc"]


def test_tap_key_split_and_whitespace_tolerated():
    got = tap_run(['{"re', 'ply"', ' : ', '"ok"', ', "action": {"type": "status"}}'])
    assert got == ["ok"]


def test_tap_decoy_value_then_real_key():
    # 顶层某个值恰好等于 "reply",真正的 reply 键在后面:只外发真键的值
    got = tap_run(['{"note": "reply", "reply": "真身"}'])
    assert got == ["真身"]


# ---------------- ⑫ 缺可用 chat_stream:静默回落 complete_json ----------------

def test_provider_without_chat_stream_falls_back():
    provider = JsonOnlyProvider({"reply": "旧式回答", "action": None})
    got: list[str] = []

    r = Brain(provider).converse("x", history=[], state_summary="",
                                 registry=REGISTRY, on_delta=got.append)

    assert r.reply == "旧式回答"
    assert provider.json_calls == 1 and got == []   # 回落路径零外发


def test_provider_with_non_callable_chat_stream_falls_back():
    provider = JsonOnlyProvider({"reply": "旧", "action": None})
    provider.chat_stream = None                 # 属性存在但不可调用:同样按缺失处置
    r = Brain(provider).converse("x", history=[], state_summary="",
                                 registry=REGISTRY, on_delta=lambda s: None)
    assert r.reply == "旧" and provider.json_calls == 1


def test_subclass_overriding_only_complete_json_never_streams(monkeypatch):
    """test_converse.py 的 ConverseScriptProvider 形态:LLMProvider 子类只改写
    complete_json —— 继承来的真 chat_stream 会对 http://fake 出网,闸门必须
    按「缺 chat_stream」处置,离线测试绝不出网。"""

    def _explode(self, *args, **kwargs):
        raise AssertionError("继承来的 chat_stream 不应被触碰")

    monkeypatch.setattr(LLMProvider, "chat_stream", _explode)
    provider = InheritedStreamProvider({"reply": "剧本台词", "action": None})
    got: list[str] = []

    r = Brain(provider).converse("x", history=[], state_summary="",
                                 registry=REGISTRY, on_delta=got.append)

    assert r.reply == "剧本台词"
    assert provider.json_calls == 1 and got == []
