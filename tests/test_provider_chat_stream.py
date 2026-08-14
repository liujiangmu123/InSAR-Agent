"""chat_stream(WAVE-0814B §1.1)HTTP 契约测试:本地 mock OpenAI SSE 端点,全程离线。

复用 tests/test_provider_chat.py 的 MockLLMServer 模式(stdlib ThreadingHTTPServer,
127.0.0.1 回环、端口 0 自选、守护线程;SSE 用原始字节脚本),钉死:
  ① content 增量逐帧回调、与聚合一致;reasoning_content 等思考增量不外发;
  ② tool_calls 按 index 分片拼装(首帧登记 id/type/name、arguments 跨帧拼接、坏帧跳过);
  ③ finish_reason=length → BrainTruncated(delta 已外发也整体拒绝),绝不换路由;
  ④ [DONE] 前未见 finish_reason → BrainUnavailable,外发过 delta 绝不重放;
  ⑤ 建流前 4xx → 同路由降级非流式重试一次 + _stream_unsupported 实例级记忆;
  ⑥ 200 + JSON 体(路由无视 stream=true)→ 就地按非流式解析,零外发;
  ⑦ usage 尾帧记账 kind="agent";无尾帧 token 记 None(绝不编数);
  ⑧ route_pin 语义与 chat() 一致(None 单跳换备 / i 钉死不切换 / 越界不出网)。
"""

from __future__ import annotations

import pytest

import insar_agent.brain.provider as provider_mod
from insar_agent.brain.provider import BrainTruncated, BrainUnavailable, LLMProvider
from test_provider_chat import MESSAGES, MockLLMServer, chat_body, sse_frames


def delta_frame(content: str | None = None, *, finish: str | None = None,
                tool_calls=None, extra_delta: dict | None = None,
                usage: dict | None = None) -> dict:
    """OpenAI 流式帧壳:choices[0].delta 按需装 content/tool_calls/思考增量。"""
    delta: dict = {}
    if content is not None:
        delta["content"] = content
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    if extra_delta:
        delta.update(extra_delta)
    choice: dict = {"delta": delta}
    if finish is not None:
        choice["finish_reason"] = finish
    frame: dict = {"choices": [choice]}
    if usage is not None:
        frame["usage"] = usage
    return frame


def usage_tail(prompt: int, completion: int) -> dict:
    """流式用量尾帧(choices 为空,只带 usage)。"""
    return {"choices": [],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion}}


@pytest.fixture(autouse=True)
def _direct_connection(monkeypatch):
    """强制直连 127.0.0.1:清空代理环境变量,防系统代理劫持回环请求。"""
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")


@pytest.fixture(autouse=True)
def _reset_sink():
    """usage sink 是模块级状态:用例退出即清空,防跨用例(含跨文件)泄漏。"""
    yield
    provider_mod.set_usage_sink(None)


@pytest.fixture()
def make_server():
    """mock 服务器工厂;测试结束统一关闭。"""
    servers: list[MockLLMServer] = []

    def _make(script: list[dict] | None = None) -> MockLLMServer:
        s = MockLLMServer(script)
        s.start()
        servers.append(s)
        return s

    yield _make
    for s in servers:
        s.stop()


# ---------------- ① content 增量:逐帧回调与聚合一致 ----------------

def test_stream_deltas_forwarded_and_aggregated(make_server):
    frames = sse_frames(
        delta_frame(extra_delta={"reasoning_content": "让我想想"}),  # 思考增量:不外发
        delta_frame('{"say'),
        delta_frame(""),                                            # 空增量:不外发
        delta_frame('": "好"}', finish="stop"),
        usage_tail(11, 7),
    )
    srv = make_server([{"sse": frames}])
    provider = LLMProvider(routes=[srv.route(api_key="stream-key")])
    got: list[str] = []

    out = provider.chat_stream(MESSAGES, on_delta=got.append)

    assert got == ['{"say', '": "好"}']            # 逐帧回调,思考/空增量不外发
    assert out.content == '{"say": "好"}'          # 聚合与回调逐字节一致
    assert out.finish_reason == "stop" and out.route_index == 0
    assert out.tool_calls == []
    assert len(srv.requests) == 1
    req = srv.requests[0]
    assert req["path"] == "/chat/completions"
    assert req["headers"]["authorization"] == "Bearer stream-key"
    body = req["body"]
    assert body["stream"] is True                   # 契约:stream=true
    assert body["stream_options"] == {"include_usage": True}
    assert body["response_format"] == {"type": "json_object"}  # json_only 默认
    assert body["temperature"] == 0
    assert body["max_tokens"] == 2048
    assert body["messages"] == MESSAGES             # 多轮四角色原样透传


def test_stream_json_only_false_omits_constraints(make_server):
    srv = make_server([{"sse": sse_frames(delta_frame("随便聊", finish="stop"))}])
    out = LLMProvider(routes=[srv.route()]).chat_stream(MESSAGES, json_only=False)
    assert out.content == "随便聊"
    body = srv.requests[0]["body"]
    assert "response_format" not in body and "temperature" not in body
    assert body["stream"] is True


def test_stream_without_on_delta_still_aggregates(make_server):
    # on_delta=None:零外发,聚合结果照常(json_only 且无回调的调用零外发)
    srv = make_server([{"sse": sse_frames(delta_frame("甲"),
                                          delta_frame("乙", finish="stop"))}])
    out = LLMProvider(routes=[srv.route()]).chat_stream(MESSAGES)
    assert out.content == "甲乙" and out.finish_reason == "stop"


# ---------------- ② tool_calls 分片拼装 ----------------

def test_stream_tool_calls_assembled_by_index(make_server):
    frames = sse_frames(
        delta_frame(tool_calls=[{"index": 0, "id": "call_a", "type": "function",
                                 "function": {"name": "search_data",
                                              "arguments": '{"qu'}}]),
        delta_frame(tool_calls=[{"index": 1, "id": "call_b", "type": "function",
                                 "function": {"name": "status", "arguments": ""}}]),
        delta_frame(tool_calls=[{"index": 0,
                                 "function": {"arguments": 'ery": "s1"}'}}]),
        # 坏帧逐种跳过:缺 index / 分片非对象 / bool 不是 index / arguments 非字符串
        delta_frame(tool_calls=[{"function": {"arguments": "孤儿分片"}},
                                "不是对象",
                                {"index": True, "function": {"arguments": "x"}},
                                {"index": 0, "function": {"arguments": 42}}]),
        delta_frame(tool_calls="不是数组"),
        delta_frame(finish="tool_calls"),
    )
    srv = make_server([{"sse": frames}])

    out = LLMProvider(routes=[srv.route()]).chat_stream(MESSAGES)

    assert out.finish_reason == "tool_calls" and out.content == ""
    assert out.tool_calls == [
        {"id": "call_a", "type": "function",
         "function": {"name": "search_data", "arguments": '{"query": "s1"}'}},
        {"id": "call_b", "type": "function",
         "function": {"name": "status", "arguments": ""}},
    ]


# ---------------- ③ 截断:delta 已外发也整体拒绝,绝不换路由 ----------------

def test_stream_truncated_after_deltas_rejects_and_never_reroutes(make_server):
    records: list[dict] = []
    provider_mod.set_usage_sink(records.append)
    primary = make_server([{"sse": sse_frames(
        delta_frame("半截 JSON"),
        delta_frame(finish="length"),
        usage_tail(5, 2048),
    )}])
    fallback = make_server()
    provider = LLMProvider(routes=[primary.route(), fallback.route()])
    got: list[str] = []

    with pytest.raises(BrainTruncated):
        provider.chat_stream(MESSAGES, on_delta=got.append)

    assert got == ["半截 JSON"]                    # 增量确实外发过
    assert len(fallback.requests) == 0             # 截断绝不换路由(重放=重复文本)
    assert len(records) == 1                       # 流建立即记账:截断照样入账
    assert records[0]["kind"] == "agent"
    assert records[0]["prompt_tokens"] == 5
    assert records[0]["completion_tokens"] == 2048


# ---------------- ④ 断流(无 finish_reason):BrainUnavailable,绝不重放 ----------------

def test_stream_done_without_finish_unavailable_never_replays(make_server):
    records: list[dict] = []
    provider_mod.set_usage_sink(records.append)
    primary = make_server([{"sse": sse_frames(delta_frame("外发过的半截"))}])
    fallback = make_server([{"json": chat_body(content="{}")}])
    provider = LLMProvider(routes=[primary.route(), fallback.route()])
    got: list[str] = []

    with pytest.raises(BrainUnavailable) as exc_info:
        provider.chat_stream(MESSAGES, on_delta=got.append)

    assert not isinstance(exc_info.value, BrainTruncated)  # 半截≠截断,语义分明
    assert got == ["外发过的半截"]
    assert len(fallback.requests) == 0             # 外发过 ≥1 delta:绝不重放
    # ⑦ 无 usage 尾帧:流建立照样记账,token 记 None 绝不编数
    assert len(records) == 1 and records[0]["kind"] == "agent"
    assert records[0]["prompt_tokens"] is None
    assert records[0]["completion_tokens"] is None


# ---------------- ⑤ 建流前 4xx:同路由非流式重试一次 + 实例级记忆 ----------------

def test_pre_stream_400_downgrades_same_route_and_remembers(make_server):
    srv = make_server([
        {"status": 400},
        {"json": chat_body(content='{"n": 1}')},
        {"json": chat_body(content='{"n": 2}')},
    ])
    provider = LLMProvider(routes=[srv.route()])
    got: list[str] = []

    first = provider.chat_stream(MESSAGES, on_delta=got.append)

    assert first.content == '{"n": 1}' and first.route_index == 0
    assert got == []                               # 非流式降级:零外发
    assert provider._stream_unsupported == {0}
    assert len(srv.requests) == 2                  # 流式尝试 + 同路由非流式重试
    assert srv.requests[0]["body"]["stream"] is True
    assert "stream" not in srv.requests[1]["body"]

    second = provider.chat_stream(MESSAGES)        # 第二次调用:记忆生效不再探测
    assert second.content == '{"n": 2}'
    assert len(srv.requests) == 3
    assert "stream" not in srv.requests[2]["body"]


# ---------------- ⑥ 200 + JSON 体:就地按非流式解析 ----------------

def test_200_json_body_parsed_in_place(make_server):
    records: list[dict] = []
    provider_mod.set_usage_sink(records.append)
    srv = make_server([{"json": chat_body(content='{"say": "就地解析"}',
                                          usage={"prompt_tokens": 3,
                                                 "completion_tokens": 4})}])
    provider = LLMProvider(routes=[srv.route()])
    got: list[str] = []

    out = provider.chat_stream(MESSAGES, on_delta=got.append)

    assert out.content == '{"say": "就地解析"}' and out.finish_reason == "stop"
    assert got == []                               # 非流式解析:不外发增量
    assert len(srv.requests) == 1                  # 就地消化,不发第二个请求
    assert srv.requests[0]["body"]["stream"] is True   # 确实按流式发起过
    assert provider._stream_unsupported == set()   # JSON 体不算 4xx,不记忆
    assert len(records) == 1 and records[0]["kind"] == "agent"
    assert records[0]["completion_tokens"] == 4


# ---------------- ⑦ usage 尾帧:kind="agent" 记账 ----------------

def test_stream_usage_tail_frame_recorded_kind_agent(make_server):
    records: list[dict] = []
    provider_mod.set_usage_sink(records.append)
    srv = make_server([{"sse": sse_frames(
        delta_frame('{"ok": 1}', finish="stop"),
        usage_tail(33, 12),
    )}])

    LLMProvider(routes=[srv.route(model="agent-stream-m")]).chat_stream(MESSAGES)

    assert len(records) == 1
    rec = records[0]
    assert rec["kind"] == "agent"                  # 契约:chat_stream 按 agent 计量
    assert rec["model"] == "agent-stream-m"
    assert rec["prompt_tokens"] == 33 and rec["completion_tokens"] == 12
    assert isinstance(rec["latency_ms"], int) and rec["latency_ms"] >= 0


# ---------------- ⑧ route_pin 语义与 chat() 一致 ----------------

def test_route_pin_selects_exact_route(make_server):
    a = make_server()
    b = make_server([{"sse": sse_frames(delta_frame('{"ok": 1}', finish="stop"))}])
    provider = LLMProvider(routes=[a.route(), b.route()])

    out = provider.chat_stream(MESSAGES, route_pin=1)

    assert out.route_index == 1 and out.content == '{"ok": 1}'
    assert len(a.requests) == 0 and len(b.requests) == 1


def test_route_pin_failure_never_switches(make_server):
    a = make_server([{"status": 503}])
    b = make_server()
    provider = LLMProvider(routes=[a.route(), b.route()])

    with pytest.raises(BrainUnavailable):
        provider.chat_stream(MESSAGES, route_pin=0)
    assert len(a.requests) == 1
    assert len(b.requests) == 0                    # 钉死语义:失败直接抛,绝不切换


def test_route_pin_out_of_range_rejected_before_network(make_server):
    srv = make_server()
    provider = LLMProvider(routes=[srv.route()])

    with pytest.raises(BrainUnavailable):
        provider.chat_stream(MESSAGES, route_pin=1)
    assert len(srv.requests) == 0                  # 越界在发请求前拒绝,不出网


def test_pre_stream_500_falls_back_single_hop(make_server):
    primary = make_server([{"status": 500}])
    fallback = make_server([{"sse": sse_frames(delta_frame('{"src": "备"}',
                                                           finish="stop"))}])
    provider = LLMProvider(routes=[primary.route(api_key="k1", model="m1"),
                                   fallback.route(api_key="k2", model="m2")])
    got: list[str] = []

    out = provider.chat_stream(MESSAGES, on_delta=got.append)

    assert out.content == '{"src": "备"}' and out.route_index == 1  # 回填实际路由
    assert got == ['{"src": "备"}']               # 建流前失败换备:外发不受影响
    assert len(primary.requests) == 1 and len(fallback.requests) == 1
    assert fallback.requests[0]["headers"]["authorization"] == "Bearer k2"
    assert fallback.requests[0]["body"]["model"] == "m2"


def test_no_routes_unavailable():
    with pytest.raises(BrainUnavailable):
        LLMProvider(routes=[]).chat_stream(MESSAGES)
