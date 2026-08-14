"""net 层(B4 联网检索)测试:本地 http.server mock,全程离线,禁真实出网。

覆盖(LOOP-CONTRACT §5):
  - ASF jsonlite 正常解析 + 字段归一化(缺失容忍,稳定键集);
  - maxResults / 日期 / WKT / beamMode 查询参数透传与 UA / 匿名纪律;
  - HTTP 错误 / 超时 / 坏 JSON / 拒连 → NetError 闭集分类(http/timeout/parse/offline);
  - DuckDuckGo html 解析(uddg 跳转还原、广告丢弃、嵌套标签、k 截断)与 Tavily key 分支;
  - search_fanout 部分失败不整体失败;响应体 2MB 截断标注。

密封:autouse 夹具清代理,并把三个 base 环境变量钉到必然拒连的回环端口 ——
即便实现读错环境变量名,也只会拒连,绝不可能真实出网。
MockServer 参照 tests/test_provider_http.py 的模式(回环、端口 0 自选、守护线程)。
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import TIME_FACTOR
from insar_agent.net import NetError, asf_search, search_fanout, web_search


class MockNetServer:
    """可编程 mock(GET/POST 通吃):script 每项对应一次请求的应答动作。

    动作(dict,按需组合):
      {"json": <obj>}                  → 200 + JSON 体
      {"raw": b"...", "ctype": "..."}  → 200 + 原样字节(默认 text/html)
      {"status": 4xx/5xx, ...}         → 对应状态码(可配 json 错误体)
      {..., "delay": 秒}                → 先睡再应答(测客户端超时)
    script 用尽后返回 200 + {},便于发现多余请求;请求全部记录
    (method/path/query/headers/body)供参数透传断言。
    """

    def __init__(self, script: list[dict] | None = None):
        self.script: list[dict] = list(script or [])
        self.requests: list[dict] = []
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # 静默:保持测试输出干净
                pass

            def do_GET(self):
                self._respond()

            def do_POST(self):
                self._respond()

            def _respond(self):
                parsed = urllib.parse.urlsplit(self.path)
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    body = raw
                with outer._lock:
                    outer.requests.append({
                        "method": self.command,
                        "path": parsed.path,
                        "query": urllib.parse.parse_qs(parsed.query),
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": body,
                    })
                    action = outer.script.pop(0) if outer.script else {"json": {}}
                try:
                    if "delay" in action:
                        time.sleep(action["delay"])
                    status = action.get("status", 200)
                    if "raw" in action:
                        payload = action["raw"]
                        ctype = action.get("ctype", "text/html; charset=utf-8")
                    else:
                        doc = action.get("json")
                        if doc is None:
                            doc = {"error": {"report": "scripted failure"}}
                        payload = json.dumps(doc, ensure_ascii=False).encode("utf-8")
                        ctype = "application/json"
                    self.send_response(status)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except OSError:
                    pass  # 客户端已超时/截断挂断(延迟与超大体场景的正常结局)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def start(self):
        self._thread.start()

    def stop(self):
        self.httpd.shutdown()
        self._thread.join(timeout=5)
        self.httpd.server_close()


def _refused_loopback() -> str:
    """必然拒连的回环地址:临时绑定拿端口后立刻释放,该端口无监听者。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"http://127.0.0.1:{port}"


@pytest.fixture(autouse=True)
def _sealed_offline(monkeypatch):
    """密封:清代理防回环劫持;三个 base 钉到拒连端口,key 清空。

    每个测试再按需 setenv 指向自己的 mock —— 忘了指也绝不会打到真实端点。
    """
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    dead = _refused_loopback()
    monkeypatch.setenv("INSAR_ASF_SEARCH_BASE", dead)
    monkeypatch.setenv("INSAR_WEBSEARCH_BASE", dead)
    monkeypatch.setenv("INSAR_TAVILY_BASE", dead)
    monkeypatch.delenv("INSAR_TAVILY_KEY", raising=False)


@pytest.fixture()
def make_server():
    """mock 服务器工厂;测试结束统一关闭。"""
    servers: list[MockNetServer] = []

    def _make(script: list[dict] | None = None) -> MockNetServer:
        s = MockNetServer(script)
        s.start()
        servers.append(s)
        return s

    yield _make
    for s in servers:
        s.stop()


@pytest.fixture()
def refused_url() -> str:
    return _refused_loopback()


AOI_WKT = "POLYGON((100 0,101 0,101 1,100 1,100 0))"

ASF_ITEM_FULL = {
    "granuleName": "S1A_IW_SLC__1SDV_20240110T120000_20240110T120027_051999_0645AB_1A2B",
    "startTime": "2024-01-10T12:00:00Z",
    "stopTime": "2024-01-10T12:00:27Z",
    "dataset": "SENTINEL-1A",
    "beamMode": "IW",
    "productType": "SLC",
    "orbit": 51999,
    "path": 25,
    "frame": "470",  # 故意给字符串:归一化须宽容整数化
    "flightDirection": "ASCENDING",
    "downloadUrl": "https://datapool.asf.alaska.edu/SLC/SA/S1A_IW_SLC_example.zip",
    "sizeMB": "4321.75",  # jsonlite 实测为字符串
    "wkt": AOI_WKT,
}

# DDG html 端点的实测结构缩样:uddg 跳转 + 直链 + 广告(y.js)+ div 形态摘要。
# CJK 文本刻意保持单行:解析器做空白归一,跨行会引入拼接空格,影响精确断言。
DDG_HTML = """<!DOCTYPE html>
<html><body><div id="links" class="results">
<div class="result results_links results_links_deep web-result">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a"
       href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Finsar&amp;rut=r1"
       >InSAR <b>形变监测</b>入门</a>
  </h2>
  <a class="result__snippet" href="#">合成孔径雷达<b>干涉测量</b>基础教程。</a>
</div>
<div class="result result--ad">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a"
       href="//duckduckgo.com/y.js?ad_provider=x&amp;click=abc">广告条目(应被丢弃)</a>
  </h2>
</div>
<div class="result results_links results_links_deep web-result">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a" href="https://direct.example.org/slc"
       >Sentinel-1 SLC 数据说明</a>
  </h2>
  <div class="result__snippet">SLC 产品<span>包含相位</span>信息。</div>
</div>
<div class="result results_links web-result">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a"
       href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fthird.example.net%2F&amp;rut=r3"
       >第三条结果</a>
  </h2>
  <a class="result__snippet" href="#">第三条摘要。</a>
</div>
</div></body></html>"""


# ---------------- ASF:正常解析与字段归一化 ----------------

def test_asf_search_normalizes_fields(make_server, monkeypatch):
    srv = make_server([{"json": {"results": [ASF_ITEM_FULL, {"granuleName": "S1B_MINIMAL"}]}}])
    monkeypatch.setenv("INSAR_ASF_SEARCH_BASE", srv.base_url)

    out = asf_search()

    assert [r["sceneName"] for r in out] == [ASF_ITEM_FULL["granuleName"], "S1B_MINIMAL"]
    full, minimal = out
    assert full["startTime"] == "2024-01-10T12:00:00Z"
    assert full["stopTime"] == "2024-01-10T12:00:27Z"
    assert full["platform"] == "SENTINEL-1A"  # jsonlite 的 dataset → platform
    assert full["beamMode"] == "IW"
    assert full["processingLevel"] == "SLC"  # jsonlite 的 productType → processingLevel
    assert full["orbit"] == 51999  # 绝对轨道号(契约字段:轨道)
    assert full["path"] == 25 and full["frame"] == 470  # "470"(字符串)→ int
    assert full["flightDirection"] == "ASCENDING"
    assert full["url"] == ASF_ITEM_FULL["downloadUrl"]  # downloadUrl → url
    assert full["sizeMB"] == pytest.approx(4321.75)  # "4321.75"(字符串)→ float
    assert full["wkt"] == AOI_WKT
    # 缺失容忍:键集稳定,缺失字段置 None,绝不 KeyError
    assert set(minimal) == set(full)
    assert minimal["startTime"] is None and minimal["path"] is None
    assert minimal["sizeMB"] is None and minimal["url"] is None


def test_asf_search_tolerates_bare_list_body(make_server, monkeypatch):
    srv = make_server([{"json": [ASF_ITEM_FULL]}])
    monkeypatch.setenv("INSAR_ASF_SEARCH_BASE", srv.base_url)
    assert asf_search()[0]["sceneName"] == ASF_ITEM_FULL["granuleName"]


# ---------------- ASF:查询参数透传与联网纪律 ----------------

def test_asf_search_passes_query_params(make_server, monkeypatch):
    srv = make_server([{"json": {"results": []}}])
    monkeypatch.setenv("INSAR_ASF_SEARCH_BASE", srv.base_url)

    out = asf_search(platform="SENTINEL-1", intersects_wkt=AOI_WKT,
                     start="2024-01-01", end="2024-02-01", beam_mode="IW",
                     processing_level="SLC", max_results=7)

    assert out == []
    assert len(srv.requests) == 1
    req = srv.requests[0]
    assert req["method"] == "GET"
    q = req["query"]
    assert q["output"] == ["jsonlite"]
    assert q["maxResults"] == ["7"]
    assert q["platform"] == ["SENTINEL-1"]
    assert q["processingLevel"] == ["SLC"]
    assert q["beamMode"] == ["IW"]
    assert q["start"] == ["2024-01-01"]
    assert q["end"] == ["2024-02-01"]
    assert q["intersectsWith"] == [AOI_WKT]  # WKT 原文经 urlencode 往返不变形
    # 联网纪律:UA 标识 insar-agent;匿名检索,绝无凭据头
    assert req["headers"]["user-agent"].startswith("insar-agent")
    assert "authorization" not in req["headers"]


def test_asf_search_omits_optional_params(make_server, monkeypatch):
    srv = make_server([{"json": {"results": []}}])
    monkeypatch.setenv("INSAR_ASF_SEARCH_BASE", srv.base_url)
    asf_search()
    q = srv.requests[0]["query"]
    assert set(q) == {"output", "maxResults", "platform", "processingLevel"}


# ---------------- 错误闭集:http / timeout / parse / offline ----------------

def test_asf_http_error_kind(make_server, monkeypatch):
    srv = make_server([{"status": 400, "json": {"error": {"report": "Invalid date"}}}])
    monkeypatch.setenv("INSAR_ASF_SEARCH_BASE", srv.base_url)
    with pytest.raises(NetError) as ei:
        asf_search(start="不是日期")
    assert ei.value.kind == "http"
    assert "400" in ei.value.detail


def test_asf_error_document_with_200_is_http_kind(make_server, monkeypatch):
    # ASF 在部分参数错误场景返回 200 + {"error": {...}}:同样归入 http 类
    srv = make_server([{"json": {"error": {"type": "VALUE", "report": "maxResults 非法"}}}])
    monkeypatch.setenv("INSAR_ASF_SEARCH_BASE", srv.base_url)
    with pytest.raises(NetError) as ei:
        asf_search()
    assert ei.value.kind == "http"
    assert "maxResults 非法" in ei.value.detail


@pytest.mark.timing
def test_asf_timeout_kind(make_server, monkeypatch):
    # 服务端睡 2s×TF > 客户端超时 0.5s×TF → NetError(timeout)。
    # 判定窗双向:超时须早于应答;比例随 TIME_FACTOR 同步放宽(参照 provider 超时用例)
    srv = make_server([{"json": {"results": []}, "delay": 2.0 * TIME_FACTOR}])
    monkeypatch.setenv("INSAR_ASF_SEARCH_BASE", srv.base_url)
    with pytest.raises(NetError) as ei:
        asf_search(timeout=0.5 * TIME_FACTOR)
    assert ei.value.kind == "timeout"


def test_asf_bad_json_kind(make_server, monkeypatch):
    srv = make_server([{"raw": b"<html>maintenance</html>"}])
    monkeypatch.setenv("INSAR_ASF_SEARCH_BASE", srv.base_url)
    with pytest.raises(NetError) as ei:
        asf_search()
    assert ei.value.kind == "parse"
    assert "截断" not in ei.value.detail  # 未截断场景不得误标


def test_asf_results_shape_missing_is_parse_kind(make_server, monkeypatch):
    srv = make_server([{"json": {"totally": "unexpected"}}])
    monkeypatch.setenv("INSAR_ASF_SEARCH_BASE", srv.base_url)
    with pytest.raises(NetError) as ei:
        asf_search()
    assert ei.value.kind == "parse"


def test_asf_offline_kind_on_refused_connection(monkeypatch, refused_url):
    monkeypatch.setenv("INSAR_ASF_SEARCH_BASE", refused_url)
    with pytest.raises(NetError) as ei:
        asf_search()
    assert ei.value.kind == "offline"


def test_web_search_offline_kind(monkeypatch, refused_url):
    monkeypatch.setenv("INSAR_WEBSEARCH_BASE", refused_url)
    with pytest.raises(NetError) as ei:
        web_search("insar")
    assert ei.value.kind == "offline"


def test_env_base_non_http_scheme_rejected(monkeypatch, tmp_path):
    """env base 的 scheme 白名单(P2-13,对齐 llm_router 先例):urllib 对
    file:// 会直读本地文件(SSRF/LFI 面)—— 非 http/https 按 NetError("http")
    拒绝,绝不出网也绝不读盘。"""
    secret = tmp_path / "secret.json"
    secret.write_text('{"results": []}', encoding="utf-8")
    monkeypatch.setenv("INSAR_ASF_SEARCH_BASE", secret.as_uri())  # file:///…
    with pytest.raises(NetError) as ei:
        asf_search()
    assert ei.value.kind == "http"
    assert "http/https" in ei.value.detail

    monkeypatch.setenv("INSAR_WEBSEARCH_BASE", "ftp://mirror.example/q")
    with pytest.raises(NetError) as ei:
        web_search("insar")
    assert ei.value.kind == "http" and "http/https" in ei.value.detail


# ---------------- web_search:DDG html 分支 ----------------

def test_ddg_parses_results_and_resolves_redirects(make_server, monkeypatch):
    srv = make_server([{"raw": DDG_HTML.encode("utf-8")}])
    monkeypatch.setenv("INSAR_WEBSEARCH_BASE", srv.base_url)

    out = web_search("insar 形变监测", k=5)

    assert [r["url"] for r in out] == ["https://example.com/insar",  # uddg 还原
                                       "https://direct.example.org/slc",  # 直链透传
                                       "https://third.example.net/"]  # 广告(y.js)已丢弃
    assert out[0]["title"] == "InSAR 形变监测入门"  # 嵌套 <b> 拼接 + 空白归一
    assert out[0]["snippet"] == "合成孔径雷达干涉测量基础教程。"
    assert out[1]["snippet"] == "SLC 产品包含相位信息。"  # div 形态摘要 + 嵌套 span
    req = srv.requests[0]
    assert req["method"] == "GET"
    assert req["query"]["q"] == ["insar 形变监测"]  # 联网纪律:只发查询词
    assert req["headers"]["user-agent"].startswith("insar-agent")


def test_ddg_k_caps_results(make_server, monkeypatch):
    srv = make_server([{"raw": DDG_HTML.encode("utf-8")}])
    monkeypatch.setenv("INSAR_WEBSEARCH_BASE", srv.base_url)
    out = web_search("insar", k=2)
    assert len(out) == 2
    assert out[0]["url"] == "https://example.com/insar"


def test_web_search_blank_query_returns_empty_without_network():
    # 空查询不出网:密封环境的 base 全是拒连端口,一旦出网必抛 offline
    assert web_search("") == []
    assert web_search("   ") == []


# ---------------- web_search:Tavily key 分支 ----------------

def test_tavily_branch_when_key_present(make_server, monkeypatch):
    srv = make_server([{"json": {"results": [
        {"title": "Tavily 结果 1", "url": "https://a.example/1", "content": "摘要一"},
        {"title": "Tavily 结果 2", "url": "https://a.example/2", "content": "摘要二"},
        {"title": "多余的第三条", "url": "https://a.example/3", "content": "应被 k 截断"},
    ]}}])
    monkeypatch.setenv("INSAR_TAVILY_KEY", "tvly-secret-123")
    monkeypatch.setenv("INSAR_TAVILY_BASE", srv.base_url + "/search")

    out = web_search("insar 干涉图 教程", k=2)

    assert out == [
        {"title": "Tavily 结果 1", "url": "https://a.example/1", "snippet": "摘要一"},
        {"title": "Tavily 结果 2", "url": "https://a.example/2", "snippet": "摘要二"},
    ]
    req = srv.requests[0]
    assert req["method"] == "POST" and req["path"] == "/search"
    assert req["body"]["query"] == "insar 干涉图 教程"  # 只发查询词
    assert req["body"]["max_results"] == 2
    assert req["headers"]["authorization"] == "Bearer tvly-secret-123"  # key 只进头/体


def test_tavily_error_detail_scrubs_key(make_server, monkeypatch):
    srv = make_server([{"status": 401, "json": {"detail": "invalid key tvly-secret-123"}}])
    monkeypatch.setenv("INSAR_TAVILY_KEY", "tvly-secret-123")
    monkeypatch.setenv("INSAR_TAVILY_BASE", srv.base_url + "/search")
    with pytest.raises(NetError) as ei:
        web_search("任意查询")
    assert ei.value.kind == "http"
    assert "tvly-secret-123" not in str(ei.value)  # 凭据绝不进错误详情/日志


# ---------------- search_fanout:部分失败不整体失败 ----------------

def test_search_fanout_all_success(make_server, monkeypatch):
    asf_srv = make_server([{"json": {"results": [ASF_ITEM_FULL]}}])
    web_srv = make_server([{"raw": DDG_HTML.encode("utf-8")}])
    monkeypatch.setenv("INSAR_ASF_SEARCH_BASE", asf_srv.base_url)
    monkeypatch.setenv("INSAR_WEBSEARCH_BASE", web_srv.base_url)

    out = search_fanout(region_wkt=AOI_WKT, start="2024-01-01", end="2024-02-01",
                        query="insar 教程", include_web=True)

    assert [r["sceneName"] for r in out["asf"]] == [ASF_ITEM_FULL["granuleName"]]
    assert len(out["web"]) == 3
    # 扇出接线:region/start/end 透传给 ASF
    q = asf_srv.requests[0]["query"]
    assert q["intersectsWith"] == [AOI_WKT]
    assert q["start"] == ["2024-01-01"] and q["end"] == ["2024-02-01"]


def test_search_fanout_partial_failure_keeps_other_channel(make_server, monkeypatch,
                                                           refused_url):
    web_srv = make_server([{"raw": DDG_HTML.encode("utf-8")}])
    monkeypatch.setenv("INSAR_ASF_SEARCH_BASE", refused_url)  # ASF 通道拒连
    monkeypatch.setenv("INSAR_WEBSEARCH_BASE", web_srv.base_url)

    out = search_fanout(region_wkt=AOI_WKT, query="insar", include_web=True)

    assert isinstance(out["asf"], dict) and "[offline]" in out["asf"]["error"]
    assert isinstance(out["web"], list) and out["web"]  # web 通道不受 ASF 失败牵连


def test_search_fanout_without_web(make_server, monkeypatch):
    asf_srv = make_server([{"json": {"results": []}}])
    monkeypatch.setenv("INSAR_ASF_SEARCH_BASE", asf_srv.base_url)
    out = search_fanout(region_wkt=AOI_WKT)
    assert out == {"asf": []}  # include_web=False → 无 web 键


def test_search_fanout_include_web_without_query(make_server, monkeypatch):
    asf_srv = make_server([{"json": {"results": []}}])
    monkeypatch.setenv("INSAR_ASF_SEARCH_BASE", asf_srv.base_url)
    out = search_fanout(include_web=True)  # 没给 query → web 通道显式报缺参,不出网
    assert out["asf"] == []
    assert isinstance(out["web"], dict) and "查询词" in out["web"]["error"]


# ---------------- 响应体 2MB 上限 ----------------

def test_asf_oversized_body_truncated_and_marked(make_server, monkeypatch):
    # 合法 JSON 但体超 2MB:截断后解析必失败 → parse,详情必须标注「截断」
    big = b'{"results": [' + b" " * (2 * 1024 * 1024) + b"]}"
    srv = make_server([{"raw": big, "ctype": "application/json"}])
    monkeypatch.setenv("INSAR_ASF_SEARCH_BASE", srv.base_url)
    with pytest.raises(NetError) as ei:
        asf_search()
    assert ei.value.kind == "parse"
    assert "截断" in ei.value.detail


def test_ddg_oversized_body_best_effort_parse(make_server, monkeypatch):
    # 结果都在前 2MB 内:截断只丢尾部 padding,html 解析尽力而为照常出结果
    page = DDG_HTML.encode("utf-8") + b"<!-- pad -->" * (200 * 1024)  # 约 2.4MB
    srv = make_server([{"raw": page}])
    monkeypatch.setenv("INSAR_WEBSEARCH_BASE", srv.base_url)
    out = web_search("insar", k=5)
    assert len(out) == 3
    assert out[0]["url"] == "https://example.com/insar"
