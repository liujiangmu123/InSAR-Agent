"""net 层共用 HTTP 内核:纯 stdlib urllib + 错误闭集 + 响应体上限。

联网纪律(LOOP-CONTRACT §0.5 / §5):
  - 只发查询词:URL 只含检索参数;凭据只进请求头/请求体,绝不进 URL;
  - 错误详情只留定位所需的少量文本,凭据打码由调用方(websearch Tavily 分支)负责;
  - UA 统一标识 insar-agent;
  - 单次响应体上限 2MB,超限截断并在 HttpBody.truncated 标注;
  - 对调用方绝不泄漏 urllib 裸异常 —— 出口只有 NetError 闭集。
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

#: 联网 UA 标识(契约 §5:UA 标 insar-agent)
USER_AGENT = "insar-agent/0.1"

#: 单次响应体上限:2MB,超限截断(防超大响应拖垮内存与下游摘要)
MAX_BODY_BYTES = 2 * 1024 * 1024

#: 出网 scheme 白名单(llm_router._require_safe_base_url 同款先例,AUDIT-api-r3):
#: 端点基址可被 INSAR_*_BASE 环境变量覆盖,urllib 对 file:// 会直读本地文件 ——
#: 不校验即 SSRF/LFI 面。越界按 NetError(kind="http") 拒绝(调用方闭集内可诊断)。
_ALLOWED_SCHEMES = ("http", "https")


class NetError(RuntimeError):
    """联网层错误闭集:kind ∈ {"timeout", "http", "parse", "offline"}。

    - timeout:连接/读取超时;
    - http:对端以 HTTP 状态码拒绝(4xx/5xx)或应答里明确报错;
    - parse:响应体无法按预期格式解析(坏 JSON / 缺关键字段);
    - offline:网络不可达(拒连 / DNS 失败 / 传输中断)。
    """

    def __init__(self, kind: str, detail: str):
        self.kind = kind
        self.detail = detail
        super().__init__(f"[{kind}] {detail}")


@dataclass(frozen=True)
class HttpBody:
    """一次 HTTP 请求的读取结果;truncated=True 表示响应体超上限被截断。"""

    status: int
    body: bytes
    truncated: bool


def http_fetch(url: str, *, method: str = "GET", data: bytes | None = None,
               headers: dict[str, str] | None = None, timeout: float = 20.0) -> HttpBody:
    """发一次 HTTP 请求并读体(上限 2MB)。失败一律折叠为 NetError 闭集。"""
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise NetError("http", f"仅支持 http/https 出网,拒绝 scheme "
                               f"{scheme or '(空)'}(检查 INSAR_*_BASE 配置)")
    merged = {"User-Agent": USER_AGENT}
    merged.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=merged, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_BODY_BYTES + 1)
            status = int(resp.status)
    except urllib.error.HTTPError as exc:
        raise NetError("http", f"HTTP {exc.code}:{_error_snippet(exc)}") from exc
    except TimeoutError as exc:  # socket.timeout 自 3.10 起就是 TimeoutError(读体阶段常见)
        raise NetError("timeout", f"请求超时(>{timeout:g}s)") from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
            raise NetError("timeout", f"请求超时(>{timeout:g}s)") from exc
        raise NetError("offline", f"网络不可达:{reason}") from exc
    except (http.client.HTTPException, ConnectionError, OSError) as exc:
        raise NetError("offline", f"连接失败:{exc}") from exc
    truncated = len(raw) > MAX_BODY_BYTES
    return HttpBody(status=status, body=raw[:MAX_BODY_BYTES], truncated=truncated)


def _error_snippet(exc: urllib.error.HTTPError) -> str:
    """从 HTTPError 读少量错误体辅助定位(读不到就退回 reason)。"""
    try:
        text = exc.read(500).decode("utf-8", "replace").strip()
    except Exception:  # 错误体读取失败不再追究,detail 保底用 reason
        text = ""
    return (text or str(exc.reason))[:200]


def json_body(res: HttpBody, *, what: str) -> object:
    """把响应体按 JSON 解析;失败 → NetError("parse"),截断场景在详情中标注。"""
    try:
        return json.loads(res.body.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        note = "(响应体超 2MB 已截断)" if res.truncated else ""
        preview = res.body[:120].decode("utf-8", "replace")
        raise NetError("parse", f"{what} 响应不是合法 JSON{note}:{preview}") from exc
