"""联网检索层(LOOP-CONTRACT §5,B4):ASF 数据检索 + 网页检索 + 检索扇出。

纯 stdlib urllib,零第三方依赖;错误闭集 NetError(timeout|http|parse|offline),
绝不向调用方泄漏 urllib 裸异常。循环核心(loop/driver,B1)惰性 import 本包;
本层只提供同步原语,并行化由循环核心经子任务池(loop/subtasks,B8)做。
"""

from __future__ import annotations

from insar_agent.net._http import NetError
from insar_agent.net.asf_search import asf_search
from insar_agent.net.websearch import web_search

__all__ = ["NetError", "asf_search", "search_fanout", "web_search"]


def search_fanout(*, region_wkt: str | None = None, start: str | None = None,
                  end: str | None = None, query: str | None = None,
                  include_web: bool = False, timeout: float = 20.0) -> dict:
    """检索扇出:ASF 必发,web 仅 include_web=True 时发;部分失败不整体失败。

    返回 {"asf": list | {"error": str}, "web": list | {"error": str}}
    (web 键仅在 include_web=True 时存在;error 字符串带 [kind] 前缀)。
    同步顺序调用 —— 并行化由循环核心把本函数(或两个单通道函数)经
    asyncio.to_thread 包成协程后交 gather_limited(B8)。
    """
    out: dict = {}
    try:
        out["asf"] = asf_search(intersects_wkt=region_wkt, start=start, end=end,
                                timeout=timeout)
    except NetError as exc:
        out["asf"] = {"error": str(exc)}
    if include_web:
        if query and query.strip():
            try:
                out["web"] = web_search(query, timeout=timeout)
            except NetError as exc:
                out["web"] = {"error": str(exc)}
        else:
            out["web"] = {"error": "缺少查询词(query),web 检索未执行"}
    return out
