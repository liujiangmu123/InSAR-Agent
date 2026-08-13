"""provider HTTP 层集成测试:本地 mock OpenAI 兼容服务器,不需要真实 API key。

现有 brain 测试全部用 FakeProvider 绕过网络,provider 的 HTTP 行为从未被真实测试。
本文件用 stdlib ThreadingHTTPServer(127.0.0.1 回环,端口 0 自选,守护线程)把它钉死:
  - 请求形状:model / messages / response_format / temperature=0 / Authorization;
  - finish_reason=length → BrainTruncated 整体拒绝,且绝不尝试 fallback;
  - 主路由 500 / 超时 / 拒连 → 单跳切 fallback;两路都挂 → BrainUnavailable;
  - content 不是合法 JSON(或不是 JSON 对象)→ BrainUnavailable,绝不返回非 dict。
纯 HTTP 回环:无子进程、无窗口。
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import TIME_FACTOR
from insar_agent.brain.provider import BrainTruncated, BrainUnavailable, LLMProvider, LLMRoute


def openai_body(content, finish_reason: str = "stop") -> dict:
    """OpenAI /chat/completions 响应壳;content 传 dict/list 时序列化为消息文本。"""
    if isinstance(content, (dict, list)):
        content = json.dumps(content, ensure_ascii=False)
    return {"choices": [{"message": {"role": "assistant", "content": content},
                         "finish_reason": finish_reason}]}


class MockLLMServer:
    """可编程 mock:script 每项对应一次请求的应答动作,收到的请求全部记录。

    动作(dict,按需组合):
      {"json": <dict>}              → 200 + JSON 体
      {"status": 500}               → 对应状态码 + JSON 错误体
      {"raw": b"..."}               → 200 但 HTTP 体不是 JSON(坏网关场景)
      {..., "delay": 秒}            → 先睡再应答(测客户端超时)
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
                    action = outer.script.pop(0) if outer.script else {"json": openai_body({})}
                try:
                    if "delay" in action:
                        time.sleep(action["delay"])
                    if "raw" in action:
                        payload, ctype, status = action["raw"], "text/html", 200
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


@pytest.fixture()
def refused_url() -> str:
    """必然拒连的回环地址:临时绑定拿端口后立刻释放,该端口无监听者。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"http://127.0.0.1:{port}"


# ---------------- 正常响应与请求形状 ----------------

def test_complete_json_ok_and_request_shape(make_server):
    srv = make_server([{"json": openai_body({"choice": 1, "reason": "精度优先"})}])
    provider = LLMProvider(routes=[srv.route(api_key="test-key-123")])

    data = provider.complete_json(system="系统提示", user="用户输入", max_tokens=77)

    assert data == {"choice": 1, "reason": "精度优先"}
    assert len(srv.requests) == 1
    req = srv.requests[0]
    assert req["path"] == "/chat/completions"  # base_url + /chat/completions
    assert req["headers"]["authorization"] == "Bearer test-key-123"
    assert req["headers"]["content-type"] == "application/json"
    body = req["body"]
    assert body["model"] == "test-model"
    assert body["temperature"] == 0
    assert body["max_tokens"] == 77
    assert body["response_format"] == {"type": "json_object"}
    # 决策请求不带历史(§3.3 约束四):恰好 system + user 两条
    assert body["messages"] == [{"role": "system", "content": "系统提示"},
                                {"role": "user", "content": "用户输入"}]


def test_no_routes_disabled_and_unavailable():
    provider = LLMProvider(routes=[])
    assert not provider.enabled
    with pytest.raises(BrainUnavailable):
        provider.complete_json(system="s", user="u")


# ---------------- 截断:整体拒绝,绝不换路由 ----------------

def test_truncated_raises_and_never_falls_back(make_server):
    primary = make_server([{"json": openai_body({"choice": 0}, finish_reason="length")}])
    fallback = make_server()
    provider = LLMProvider(routes=[primary.route(), fallback.route()])

    with pytest.raises(BrainTruncated):
        provider.complete_json(system="s", user="u")

    assert len(primary.requests) == 1
    assert len(fallback.requests) == 0  # 截断换供应商解决不了 prompt 过长,钉死不 fallback
    # 调用方按 BrainUnavailable 统一捕获降级,截断必须是其子类
    assert issubclass(BrainTruncated, BrainUnavailable)


# ---------------- 单跳 fallback:500 / 超时 / 拒连 ----------------

def test_primary_500_falls_back_single_hop(make_server):
    primary = make_server([{"status": 500}])
    fallback = make_server([{"json": openai_body({"src": "fallback"})}])
    provider = LLMProvider(routes=[primary.route(api_key="primary-key", model="m1"),
                                   fallback.route(api_key="fallback-key", model="m2")])

    assert provider.complete_json(system="s", user="u") == {"src": "fallback"}
    assert len(primary.requests) == 1 and len(fallback.requests) == 1
    # 每条路由用自己的 api_key / model
    assert fallback.requests[0]["headers"]["authorization"] == "Bearer fallback-key"
    assert fallback.requests[0]["body"]["model"] == "m2"


@pytest.mark.timing
def test_primary_timeout_falls_back(make_server):
    # 主路由睡 2s×TF > provider timeout 0.5s×TF → 读超时 → 切 fallback。
    # 双向判定窗:超时须小于主路由延迟(判超时),又须容下 fallback 正常应答
    # (负载下 0.5s 可能不够)—— 两者同乘系数保持比例
    primary = make_server([{"json": openai_body({"src": "primary"}),
                            "delay": 2.0 * TIME_FACTOR}])
    fallback = make_server([{"json": openai_body({"src": "fallback"})}])
    provider = LLMProvider(routes=[primary.route(), fallback.route()],
                           timeout=0.5 * TIME_FACTOR)

    assert provider.complete_json(system="s", user="u") == {"src": "fallback"}
    assert len(primary.requests) == 1 and len(fallback.requests) == 1


def test_primary_connection_refused_falls_back(make_server, refused_url):
    fallback = make_server([{"json": openai_body({"src": "fallback"})}])
    provider = LLMProvider(routes=[LLMRoute(refused_url, "k", "m"), fallback.route()])

    assert provider.complete_json(system="s", user="u") == {"src": "fallback"}
    assert len(fallback.requests) == 1


def test_all_routes_down_raises_unavailable(make_server, refused_url):
    primary = make_server([{"status": 503}])
    provider = LLMProvider(routes=[primary.route(), LLMRoute(refused_url, "k", "m")])

    with pytest.raises(BrainUnavailable):
        provider.complete_json(system="s", user="u")
    assert len(primary.requests) == 1  # 单跳:每路只试一次,不无限重试


def test_at_most_two_routes_ever_tried(make_server):
    a = make_server([{"status": 500}])
    b = make_server([{"status": 500}])
    c = make_server([{"json": openai_body({"src": "third"})}])
    provider = LLMProvider(routes=[a.route(), b.route(), c.route()])

    with pytest.raises(BrainUnavailable):
        provider.complete_json(system="s", user="u")
    assert len(a.requests) == 1 and len(b.requests) == 1
    assert len(c.requests) == 0  # 单跳 = 主 + 备,第三路永不触达


# ---------------- content / HTTP 体不是合法 JSON ----------------

def test_content_not_json_raises_unavailable(make_server):
    srv = make_server([{"json": openai_body("好的,我建议使用 snaphu(这不是 JSON)")}])
    provider = LLMProvider(routes=[srv.route()])

    with pytest.raises(BrainUnavailable):
        provider.complete_json(system="s", user="u")


def test_content_json_but_not_object_raises_unavailable(make_server):
    """content 是合法 JSON 却不是对象(路由无视 response_format 时会发生)。

    complete_json 的契约是返回 dict;返回 list/str 会让 facade 在 .get() 上炸穿,
    破坏「全职责可降级」铁律 → 必须在 provider 层拒绝。
    """
    srv = make_server([{"json": openai_body([1, 2, 3])}])
    provider = LLMProvider(routes=[srv.route()])

    with pytest.raises(BrainUnavailable):
        provider.complete_json(system="s", user="u")


def test_bad_content_switches_route_then_succeeds(make_server):
    # 解析失败属于「路由失败」→ 换备路由一次(换个模型可能就守规矩了)
    primary = make_server([{"json": openai_body("自然语言,不是 JSON")}])
    fallback = make_server([{"json": openai_body({"src": "fallback"})}])
    provider = LLMProvider(routes=[primary.route(), fallback.route()])

    assert provider.complete_json(system="s", user="u") == {"src": "fallback"}
    assert len(primary.requests) == 1 and len(fallback.requests) == 1


def test_http_body_not_json_falls_back(make_server):
    # 200 但整个 HTTP 体是 HTML(坏网关/门户劫持)→ 视为路由失败切备
    primary = make_server([{"raw": b"<html>gateway error</html>"}])
    fallback = make_server([{"json": openai_body({"src": "fallback"})}])
    provider = LLMProvider(routes=[primary.route(), fallback.route()])

    assert provider.complete_json(system="s", user="u") == {"src": "fallback"}
