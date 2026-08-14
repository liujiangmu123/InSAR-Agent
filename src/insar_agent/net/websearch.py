"""网页检索(尽力而为):默认 DuckDuckGo html 端点,可选 Tavily。

默认走 DDG html 端点(零 key,GET ?q=...,stdlib HTMLParser 解析结果结构);
环境变量 INSAR_TAVILY_KEY 存在时优先 Tavily API(POST JSON,结构化结果更稳)。
统一返回 [{"title", "url", "snippet"}];失败一律 NetError 闭集。

端点可用环境变量覆盖(测试指向本地 mock):
  INSAR_WEBSEARCH_BASE —— DDG html 端点(默认 https://html.duckduckgo.com/html/)
  INSAR_TAVILY_BASE    —— Tavily 检索端点(默认 https://api.tavily.com/search)

联网纪律:只发查询词;Tavily key 只进请求头/请求体,绝不进 URL;
错误详情打码 key(凭据不进日志不进 LLM)。
"""

from __future__ import annotations

import json
import os
import urllib.parse
from html.parser import HTMLParser

from insar_agent.net._http import NetError, http_fetch, json_body

#: DuckDuckGo html 端点(INSAR_WEBSEARCH_BASE 可覆盖)
DDG_BASE_DEFAULT = "https://html.duckduckgo.com/html/"

#: Tavily 检索端点(INSAR_TAVILY_BASE 可覆盖)
TAVILY_BASE_DEFAULT = "https://api.tavily.com/search"


def web_search(query: str, *, k: int = 5, timeout: float = 15.0) -> list[dict]:
    """网页检索:返回至多 k 条 {"title", "url", "snippet"}。

    空查询直接返回 [](不出网);INSAR_TAVILY_KEY 存在 → Tavily,否则 DDG html。
    """
    q = (query or "").strip()
    if not q:
        return []
    key = os.environ.get("INSAR_TAVILY_KEY", "").strip()
    if key:
        return _tavily_search(q, key=key, k=k, timeout=timeout)
    return _ddg_search(q, k=k, timeout=timeout)


# ---------------- DuckDuckGo html 分支(零 key,尽力而为) ----------------


def _ddg_search(query: str, *, k: int, timeout: float) -> list[dict]:
    """DDG html 端点:GET ?q=...,HTMLParser 解析 result__a / result__snippet。

    页面结构非官方契约,可能漂移 —— 解析宽容:响应截断 / 结构漂移时能解多少
    返回多少(零结果就是空列表),解析环节绝不裸抛。
    """
    base = os.environ.get("INSAR_WEBSEARCH_BASE", "").strip() or DDG_BASE_DEFAULT
    sep = "&" if "?" in base else "?"
    res = http_fetch(base + sep + urllib.parse.urlencode({"q": query}), timeout=timeout)
    parser = _DDGParser()
    try:
        parser.feed(res.body.decode("utf-8", "replace"))
        parser.close()
    except Exception as exc:  # HTMLParser 极少抛;真抛也要归入闭集,不裸抛
        raise NetError("parse", f"DDG HTML 解析失败:{exc}") from exc
    return parser.results[: max(0, int(k))]


class _DDGParser(HTMLParser):
    """DDG html 结果解析器(尽力而为)。

    目标结构(2026-08 实测形态):
      <a class="result__a" href="//duckduckgo.com/l/?uddg=<编码URL>&rut=..">标题</a>
      <a class="result__snippet" ...>摘要(可含 <b> 高亮)</a>(或 div 形态)
    标题锚点开一条结果;紧随的 snippet 元素回填摘要;广告(y.js 跳转)丢弃。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self._mode: str | None = None  # None | "title" | "snippet"
        self._mode_tag = ""            # 进入模式的标签名(端标签配对退出)
        self._depth = 0                # 同名标签嵌套深度(div 形态摘要会嵌套)
        self._buf: list[str] = []
        self._href = ""

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if self._mode:
            if tag == self._mode_tag:
                self._depth += 1
            return
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag == "a" and "result__a" in cls:
            self._mode, self._mode_tag, self._depth = "title", tag, 1
            self._buf, self._href = [], (a.get("href") or "")
        elif "result__snippet" in cls:
            self._mode, self._mode_tag, self._depth = "snippet", tag, 1
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if not self._mode or tag != self._mode_tag:
            return
        self._depth -= 1
        if self._depth > 0:
            return
        text = " ".join("".join(self._buf).split())  # 空白归一(换行/缩进折叠)
        if self._mode == "title":
            url = _resolve_ddg_href(self._href)
            if url:
                self.results.append({"title": text, "url": url, "snippet": ""})
        elif self.results and not self.results[-1]["snippet"]:
            self.results[-1]["snippet"] = text
        self._mode, self._buf = None, []

    def handle_data(self, data: str) -> None:
        if self._mode:
            self._buf.append(data)


def _resolve_ddg_href(href: str) -> str:
    """还原 DDG 跳转链接:/l/?uddg=<编码URL> → 真实 URL;广告与站内链接丢弃。"""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    try:
        parts = urllib.parse.urlsplit(href)
    except ValueError:
        return href  # 畸形 URL:原样返回(尽力而为)
    if not parts.netloc or "duckduckgo.com" in parts.netloc:
        if "y.js" in parts.path:
            return ""  # 广告跳转,无真实外链可还原
        if parts.path == "/l" or parts.path.startswith("/l/"):
            return (urllib.parse.parse_qs(parts.query).get("uddg") or [""])[0]
        return ""  # 其余站内链接(翻页/设置)不是检索结果
    return href


# ---------------- Tavily 分支(INSAR_TAVILY_KEY 存在时优先) ----------------


def _tavily_search(query: str, *, key: str, k: int, timeout: float) -> list[dict]:
    """Tavily 检索:POST JSON;新版 Bearer 头 + 旧版 body api_key 双形态兼容。"""
    base = os.environ.get("INSAR_TAVILY_BASE", "").strip() or TAVILY_BASE_DEFAULT
    payload = {"query": query, "max_results": max(1, int(k)), "api_key": key}
    try:
        res = http_fetch(base, method="POST",
                         data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                         headers={"Content-Type": "application/json",
                                  "Authorization": f"Bearer {key}"},
                         timeout=timeout)
        doc = json_body(res, what="Tavily")
    except NetError as exc:
        # 凭据纪律:异常网关可能把 key 回显进错误体 —— 打码后再抛。
        # from None 掐断异常链(P2-14):原始 NetError 的 detail/上下文异常
        # 仍带未打码的 key,挂在 __cause__ 上会随 traceback 进日志。
        raise NetError(exc.kind, exc.detail.replace(key, "***")) from None
    items = doc.get("results") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        raise NetError("parse", "Tavily 响应缺 results 数组")
    out: list[dict] = []
    for item in items[: max(0, int(k))]:
        if isinstance(item, dict):
            out.append({"title": str(item.get("title") or ""),
                        "url": str(item.get("url") or ""),
                        "snippet": str(item.get("content") or item.get("snippet") or "")})
    return out
