"""ASF SearchAPI 数据检索(匿名可用,零凭据)。

对接 https://api.daac.asf.alaska.edu/services/search/param 的 jsonlite 输出:
检索不需要 Earthdata 凭据(下载才需要,不在本层职责内)。返回字段归一化为
稳定键集(见 _normalize),缺失容忍 —— 对端字段漂移时置 None,绝不 KeyError。

端点可用环境变量 INSAR_ASF_SEARCH_BASE 覆盖(测试指向本地 http.server mock)。
失败一律抛 NetError(timeout|http|parse|offline),绝不裸抛 urllib 异常。
"""

from __future__ import annotations

import os
import urllib.parse

from insar_agent.net._http import NetError, http_fetch, json_body

#: ASF SearchAPI 参数端点(jsonlite 输出;INSAR_ASF_SEARCH_BASE 可覆盖)
ASF_SEARCH_BASE_DEFAULT = "https://api.daac.asf.alaska.edu/services/search/param"


def asf_search(*, platform: str = "SENTINEL-1", intersects_wkt: str | None = None,
               start: str | None = None, end: str | None = None,
               beam_mode: str | None = None, processing_level: str = "SLC",
               max_results: int = 50, timeout: float = 20.0) -> list[dict]:
    """按平台 / 时窗 / AOI 检索 ASF 归档,返回归一化场景列表。

    参数直译为 SearchAPI query:start/end 传 ISO 日期,intersects_wkt 传 WKT
    (POLYGON/POINT 等),beam_mode 如 "IW";None/空值参数不上送。
    联网纪律:GET 只带查询词,匿名请求,无任何凭据头。
    """
    query: list[tuple[str, str]] = [("output", "jsonlite"),
                                    ("maxResults", str(int(max_results)))]
    if platform:
        query.append(("platform", platform))
    if processing_level:
        query.append(("processingLevel", processing_level))
    if beam_mode:
        query.append(("beamMode", beam_mode))
    if start:
        query.append(("start", start))
    if end:
        query.append(("end", end))
    if intersects_wkt:
        query.append(("intersectsWith", intersects_wkt))
    base = (os.environ.get("INSAR_ASF_SEARCH_BASE", "").strip().rstrip("/")
            or ASF_SEARCH_BASE_DEFAULT)
    res = http_fetch(f"{base}?{urllib.parse.urlencode(query)}", timeout=timeout)
    doc = json_body(res, what="ASF")
    return [_normalize(item) for item in _results_of(doc) if isinstance(item, dict)]


def _results_of(doc: object) -> list:
    """从 jsonlite 响应取 results 数组;对端报错 / 形状不对 → NetError。"""
    if isinstance(doc, list):
        return doc  # 容忍部分部署直接返回数组(非标准但见过的形态)
    if isinstance(doc, dict):
        err = doc.get("error")
        if err:
            # ASF 在部分参数错误场景下返回 200 + {"error": {"type","report"}}
            report = err.get("report") if isinstance(err, dict) else err
            raise NetError("http", f"ASF 报错:{str(report)[:200]}")
        results = doc.get("results")
        if isinstance(results, list):
            return results
    raise NetError("parse", "ASF 响应缺 results 数组(非 jsonlite 形态)")


def _normalize(item: dict) -> dict:
    """jsonlite 单条 → 稳定字段闭集;缺失 / 类型漂移容忍(置 None,绝不抛)。

    jsonlite 实测键:granuleName/startTime/stopTime/orbit/path/frame/
    flightDirection/downloadUrl/sizeMB(字符串)/beamMode/dataset/wkt 等;
    旧版 json 输出用 sceneName/platform —— 两代键名都认。
    """
    return {
        "sceneName": item.get("sceneName") or item.get("granuleName") or item.get("fileName"),
        "startTime": item.get("startTime"),
        "stopTime": item.get("stopTime"),
        "platform": item.get("platform") or item.get("dataset"),
        "beamMode": item.get("beamMode"),
        "processingLevel": item.get("processingLevel") or item.get("productType"),
        "orbit": _as_int(item.get("orbit")),
        "path": _as_int(item.get("path")),
        "frame": _as_int(item.get("frame")),
        "flightDirection": item.get("flightDirection"),
        "url": item.get("downloadUrl") or item.get("url"),
        "sizeMB": _as_float(item.get("sizeMB")),
        "wkt": item.get("wkt"),
    }


def _as_int(value: object) -> int | None:
    """宽容整数化:path/frame 偶见字符串形态;转不动 → None。"""
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None


def _as_float(value: object) -> float | None:
    """宽容浮点化:sizeMB 实测是字符串;转不动 → None。"""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
