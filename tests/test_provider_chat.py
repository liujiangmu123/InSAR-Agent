"""chat 原语(docs/LOOP-CONTRACT.md §2)HTTP 契约测试:本地 mock OpenAI 端点,全程离线。

参照 tests/test_provider_http.py 的 MockServer 模式(stdlib ThreadingHTTPServer,
127.0.0.1 回环、端口 0 自选、守护线程;另加 SSE 原始字节动作服务流式识图),钉死:
  - messages(system/user/assistant/tool 多轮)原样透传,content/finish_reason 解析;
  - tools 按 OpenAI 形状透传,tool_calls 原样带回(缺失/坏形状 → []);
  - json_only=True → response_format=json_object + temperature=0,False → 两字段都不发;
  - finish_reason=length → BrainTruncated 整体拒绝(含 tool_calls),绝不换路由;
  - route_pin=None 单跳 fallback(至多两路)与 route_index 回填;route_pin=i 钉死不切换;
  - 用量上报 kind="agent"(set_usage_sink 通道,截断同样入账);
  - _iter_sse 抽取后识图流式(describe_image_stream)行为不回归。
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import insar_agent.brain.provider as provider_mod
from conftest import TIME_FACTOR
from insar_agent.brain.provider import (
    BrainTruncated,
    BrainUnavailable,
    ChatOutcome,
    LLMProvider,
    LLMRoute,
    _iter_sse,
    describe_image_stream,
)

# ---------------- 样例数据(OpenAI 形状) ----------------

#: 多轮四角色历史:含 assistant 的 null content + tool 回执,透传必须逐字节保真
MESSAGES = [
    {"role": "system", "content": "循环提示词:动作闭集与纪律"},
    {"role": "user", "content": "回合目标:找到可用的 SLC 数据"},
    {"role": "assistant", "content": None,
     "tool_calls": [{"id": "call_000", "type": "function",
                     "function": {"name": "search_data", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "call_000", "content": "检索到 3 景"},
]

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_data",
        "description": "检索本地目录与 ASF 的 SLC 数据",
        "parameters": {"type": "object",
                       "properties": {"query": {"type": "string"}},
                       "required": ["query"]},
    },
}

TOOL_CALL = {"id": "call_001", "type": "function",
             "function": {"name": "search_data",
                          "arguments": '{"query": "sentinel-1"}'}}


def chat_body(*, content=None, tool_calls=None, finish: str = "stop",
              usage: dict | None = None) -> dict:
    """OpenAI /chat/completions 非流式响应壳(content 可为 None,贴近纯工具调用)。"""
    message: dict = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    body: dict = {"choices": [{"message": message, "finish_reason": finish}]}
    if usage is not None:
        body["usage"] = usage
    return body


def sse_frames(*objs: dict) -> list[bytes]:
    """OpenAI 兼容 SSE 帧字节序列(每帧一行 data:,自动补 [DONE] 尾行)。"""
    lines = [b"data: " + json.dumps(o, ensure_ascii=False).encode("utf-8") + b"\n\n"
             for o in objs]
    return lines + [b"data: [DONE]\n\n"]


# ---------------- 本地 mock OpenAI 端点 ----------------

class MockLLMServer:
    """可编程 mock:script 每项对应一次请求的应答动作,收到的请求全部记录。

    动作(dict,按需组合):
      {"json": <dict>}       → 200 + JSON 体
      {"status": 500}        → 对应状态码 + JSON 错误体
      {"sse": [b"...", ...]} → 200 + text/event-stream 原始字节(流式识图场景)
      {..., "delay": 秒}     → 先睡再应答(测客户端超时)
    script 用尽后返回 200 + 空对象 content,便于发现多余请求。
    """

    def __init__(self, script: list[dict] | None = None):
        self.script: list[dict] = list(script or [])
        self.requests: list[dict] = []
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # 静默:保持测试输出干净
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    body = raw
                with outer._lock:
                    outer.requests.append({
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": body,
                    })
                    action = (outer.script.pop(0) if outer.script
                              else {"json": chat_body(content="{}")})
                try:
                    if "delay" in action:
                        time.sleep(action["delay"])
                    if "sse" in action:
                        payload = b"".join(action["sse"])
                        ctype, status = "text/event-stream", 200
                    else:
                        status = action.get("status", 200)
                        doc = action.get("json") or {"error": {"message": "scripted failure"}}
                        payload = json.dumps(doc, ensure_ascii=False).encode("utf-8")
                        ctype = "application/json"
                    self.send_response(status)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except OSError:
                    pass  # 客户端已超时挂断(延迟应答场景的正常结局)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def route(self, api_key: str = "test-key", model: str = "test-model") -> LLMRoute:
        return LLMRoute(self.base_url, api_key, model)

    def start(self):
        self._thread.start()

    def stop(self):
        self.httpd.shutdown()
        self._thread.join(timeout=5)
        self.httpd.server_close()


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


# ---------------- ① messages 透传与 content 解析 ----------------

def test_chat_messages_passthrough_and_content_parsed(make_server):
    srv = make_server([{"json": chat_body(content='{"say": "已完成"}')}])
    provider = LLMProvider(routes=[srv.route(api_key="agent-key-1")])

    out = provider.chat(MESSAGES)

    assert isinstance(out, ChatOutcome)
    assert out.content == '{"say": "已完成"}'   # content 原文带回,不做 JSON 解析
    assert out.tool_calls == []                  # 响应无 tool_calls → [],不是 None
    assert out.finish_reason == "stop"
    assert out.route_index == 0
    assert len(srv.requests) == 1
    req = srv.requests[0]
    assert req["path"] == "/chat/completions"
    assert req["headers"]["authorization"] == "Bearer agent-key-1"
    assert req["headers"]["content-type"] == "application/json"
    body = req["body"]
    assert body["model"] == "test-model"
    assert body["max_tokens"] == 2048            # 契约默认
    assert body["messages"] == MESSAGES          # 多轮四角色原样透传(含 null content)
    assert "tools" not in body                   # 未给 tools 则不发该字段


def test_chat_empty_content_normalized_to_empty_string(make_server):
    """content:null(纯工具调用之外也可能出现)归一为空串,绝不外漏 None。"""
    srv = make_server([{"json": chat_body(content=None)}])
    out = LLMProvider(routes=[srv.route()]).chat(MESSAGES)
    assert out.content == "" and out.tool_calls == []


# ---------------- ② tools 透传与 tool_calls 解析 ----------------

def test_chat_tools_passthrough_and_tool_calls_parsed(make_server):
    srv = make_server([{"json": chat_body(content=None, tool_calls=[TOOL_CALL],
                                          finish="tool_calls")}])
    provider = LLMProvider(routes=[srv.route()])

    out = provider.chat(MESSAGES, tools=[SEARCH_TOOL])

    assert srv.requests[0]["body"]["tools"] == [SEARCH_TOOL]  # OpenAI 形状原样透传
    assert out.tool_calls == [TOOL_CALL]         # id/type/function 原样带回
    assert out.content == ""                     # content:null 归一为空串
    assert out.finish_reason == "tool_calls"


def test_chat_tool_calls_bad_shapes_normalized_to_empty(make_server):
    """坏中转站防御:tool_calls 非数组 → [];数组内非对象项剔除。"""
    srv = make_server([
        {"json": chat_body(content="x", tool_calls="不是数组")},
        {"json": chat_body(content="x", tool_calls=[TOOL_CALL, "混进来的字符串"])},
    ])
    provider = LLMProvider(routes=[srv.route()])

    assert provider.chat(MESSAGES).tool_calls == []
    assert provider.chat(MESSAGES).tool_calls == [TOOL_CALL]


# ---------------- ③ json_only 的 payload 契约 ----------------

def test_chat_json_only_controls_payload(make_server):
    srv = make_server([{"json": chat_body(content="{}")},
                       {"json": chat_body(content="随便聊")}])
    provider = LLMProvider(routes=[srv.route()])

    provider.chat(MESSAGES)                      # 默认 json_only=True
    body = srv.requests[0]["body"]
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0

    provider.chat(MESSAGES, json_only=False)     # 自由文本模式:两个约束字段都不发
    body = srv.requests[1]["body"]
    assert "response_format" not in body
    assert "temperature" not in body


# ---------------- ④ 截断:BrainTruncated 整体拒绝,绝不换路由 ----------------

def test_chat_truncated_with_tool_calls_rejected_never_falls_back(make_server):
    """absorb-E9:截断响应里已解析出的 tool_calls 也可能语义不完整,一个都不能用。"""
    primary = make_server([{"json": chat_body(content=None, tool_calls=[TOOL_CALL],
                                              finish="length")}])
    fallback = make_server()
    provider = LLMProvider(routes=[primary.route(), fallback.route()])

    with pytest.raises(BrainTruncated):
        provider.chat(MESSAGES, tools=[SEARCH_TOOL])

    assert len(primary.requests) == 1
    assert len(fallback.requests) == 0           # 截断换供应商解决不了,钉死不 fallback
    assert issubclass(BrainTruncated, BrainUnavailable)   # 调用方统一按父类捕获降级


def test_chat_truncated_with_route_pin_raises_truncated(make_server):
    pinned = make_server([{"json": chat_body(content="半截", finish="length")}])
    other = make_server()
    provider = LLMProvider(routes=[pinned.route(), other.route()])

    with pytest.raises(BrainTruncated):
        provider.chat(MESSAGES, route_pin=0)

    assert len(pinned.requests) == 1 and len(other.requests) == 0


# ---------------- ⑤ route_pin=None 单跳 fallback 与 route_index 回填 ----------------

def test_chat_fallback_single_hop_and_route_index_backfill(make_server):
    primary = make_server([{"status": 500}])
    fallback = make_server([{"json": chat_body(content='{"say": "备路接管"}')}])
    provider = LLMProvider(routes=[primary.route(api_key="k1", model="m1"),
                                   fallback.route(api_key="k2", model="m2")])

    out = provider.chat(MESSAGES)

    assert out.content == '{"say": "备路接管"}'
    assert out.route_index == 1                  # 回填实际路由,供调用方下周期钉死
    assert len(primary.requests) == 1 and len(fallback.requests) == 1
    assert fallback.requests[0]["headers"]["authorization"] == "Bearer k2"
    assert fallback.requests[0]["body"]["model"] == "m2"


def test_chat_at_most_two_routes_tried(make_server):
    a = make_server([{"status": 500}])
    b = make_server([{"status": 500}])
    c = make_server([{"json": chat_body(content="{}")}])
    provider = LLMProvider(routes=[a.route(), b.route(), c.route()])

    with pytest.raises(BrainUnavailable):
        provider.chat(MESSAGES)
    assert len(a.requests) == 1 and len(b.requests) == 1
    assert len(c.requests) == 0                  # 单跳 = 主 + 备,第三路永不触达


@pytest.mark.timing
def test_chat_primary_timeout_falls_back(make_server):
    # 主路由睡 2s×TF > provider timeout 0.5s×TF → 读超时 → 切 fallback。
    # 判定窗与应答余量同乘系数保持比例(同 tests/test_provider_http.py 先例)
    primary = make_server([{"json": chat_body(content="{}"),
                            "delay": 2.0 * TIME_FACTOR}])
    fallback = make_server([{"json": chat_body(content='{"src": "fallback"}')}])
    provider = LLMProvider(routes=[primary.route(), fallback.route()],
                           timeout=0.5 * TIME_FACTOR)

    out = provider.chat(MESSAGES)

    assert out.content == '{"src": "fallback"}' and out.route_index == 1
    assert len(primary.requests) == 1 and len(fallback.requests) == 1


def test_chat_route_pin_selects_exact_route(make_server):
    a = make_server()
    b = make_server([{"json": chat_body(content='{"ok": 1}')}])
    provider = LLMProvider(routes=[a.route(), b.route()])

    out = provider.chat(MESSAGES, route_pin=1)

    assert out.route_index == 1 and out.content == '{"ok": 1}'
    assert len(a.requests) == 0 and len(b.requests) == 1


def test_chat_route_pin_failure_never_switches(make_server):
    a = make_server([{"status": 503}])
    b = make_server([{"json": chat_body(content="{}")}])
    provider = LLMProvider(routes=[a.route(), b.route()])

    with pytest.raises(BrainUnavailable):
        provider.chat(MESSAGES, route_pin=0)
    assert len(a.requests) == 1
    assert len(b.requests) == 0                  # 钉死语义:失败直接抛,绝不切换


def test_chat_route_pin_out_of_range_rejected(make_server):
    srv = make_server()
    provider = LLMProvider(routes=[srv.route()])

    with pytest.raises(BrainUnavailable):
        provider.chat(MESSAGES, route_pin=1)
    assert len(srv.requests) == 0                # 越界在发请求前拒绝,不出网


def test_chat_no_routes_unavailable():
    with pytest.raises(BrainUnavailable):
        LLMProvider(routes=[]).chat(MESSAGES)


# ---------------- ⑥ 用量上报 kind="agent" ----------------

def test_chat_usage_reported_kind_agent(make_server):
    records: list[dict] = []
    provider_mod.set_usage_sink(records.append)
    srv = make_server([{"json": chat_body(content="{}",
                                          usage={"prompt_tokens": 321,
                                                 "completion_tokens": 45})}])
    provider = LLMProvider(routes=[srv.route(model="agent-m")])

    provider.chat(MESSAGES)

    assert len(records) == 1
    rec = records[0]
    assert rec["kind"] == "agent"                # 契约:chat 原语按 agent 计量
    assert rec["model"] == "agent-m"
    assert rec["prompt_tokens"] == 321 and rec["completion_tokens"] == 45
    assert isinstance(rec["latency_ms"], int) and rec["latency_ms"] >= 0


def test_chat_truncated_still_reports_usage(make_server):
    """拿到响应体即记账:截断整体拒绝,但 token 已被中转站计费,照样入账。"""
    records: list[dict] = []
    provider_mod.set_usage_sink(records.append)
    srv = make_server([{"json": chat_body(content="x", finish="length",
                                          usage={"prompt_tokens": 9,
                                                 "completion_tokens": 2048})}])
    provider = LLMProvider(routes=[srv.route()])

    with pytest.raises(BrainTruncated):
        provider.chat(MESSAGES)
    assert len(records) == 1
    assert records[0]["kind"] == "agent"
    assert records[0]["completion_tokens"] == 2048


# ---------------- ⑦ _iter_sse 抽取与识图流式不回归 ----------------

def test_iter_sse_parses_data_frames_and_stops_at_done():
    lines = [
        b"event: ping\n",                        # 非 data 行忽略
        b"\n",                                   # 空行忽略
        b": keep-alive\n",                       # SSE 注释行忽略
        b"data: not-json\n",                     # 坏帧跳过(心跳/半截 JSON)
        b"data: [1, 2, 3]\n",                    # 合法 JSON 但非对象 → 跳过
        b'data: {"a": 1}\n',
        b'data:{"b": 2}\n',                      # 冒号后无空格同样可解析
        b"data: [DONE]\n",
        b'data: {"c": 3}\n',                     # [DONE] 之后的数据绝不再产出
    ]
    assert list(_iter_sse(lines)) == [{"a": 1}, {"b": 2}]


def test_describe_image_stream_behavior_unchanged_via_http(make_server):
    """识图走真 HTTP SSE 回环:聚合文本、请求形状、vision 计量口径全部不回归。"""
    records: list[dict] = []
    provider_mod.set_usage_sink(records.append)
    frames = sse_frames(
        {"choices": [{"delta": {"content": "红"}}], "usage": None},
        {"choices": [{"delta": {"content": "色"}, "finish_reason": "stop"}],
         "usage": None},
        {"choices": [], "usage": {"prompt_tokens": 25, "completion_tokens": 80,
                                  "total_tokens": 105}},
    )
    srv = make_server([{"sse": frames}])

    text = describe_image_stream(srv.route(model="vision-m"),
                                 prompt="这是什么颜色?",
                                 image_data_url="data:image/png;base64,AA")

    assert text == "红色"
    body = srv.requests[0]["body"]
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/")
    rec = records[0]
    assert rec["kind"] == "vision"               # 识图计量口径不变(chat 才是 agent)
    assert rec["prompt_tokens"] == 25 and rec["completion_tokens"] == 80


def test_describe_image_stream_truncated_via_http(make_server):
    srv = make_server([{"sse": sse_frames(
        {"choices": [{"delta": {"content": "半截"}, "finish_reason": "length"}]})}])

    with pytest.raises(BrainTruncated):
        describe_image_stream(srv.route(), prompt="p",
                              image_data_url="data:image/png;base64,AA")
