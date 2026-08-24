"""JSON 预览(.json)。只读前 64KB;不注册 .yaml(text_view 属地)。

纪律:keys 只来自实际解析到的顶层 dict,绝不编造;坏 JSON 诚实 unsupported。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from insar_agent.preview.dispatch import Preview, register

_MAX_READ = 1024 * 1024
_TEXT_CAP = 256 * 1024


def _cap_utf8(text: str, max_bytes: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    return raw[:max_bytes].decode("utf-8", errors="ignore")


def _unsupported(*, truncated: bool, note: str) -> Preview:
    return Preview(
        kind="unsupported",
        truncated=truncated,
        note=note,
        payload={"reason": "invalid_json"},
    )


@register(".json")
def preview_json(path: Path) -> Preview:
    size = path.stat().st_size
    truncated = size > _MAX_READ
    with path.open("rb") as fh:
        raw = fh.read(_MAX_READ)
    try:
        source = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        note = (
            "文件过大且截断后不是合法 JSON"
            if truncated
            else type(exc).__name__
        )
        return _unsupported(truncated=truncated, note=note)

    try:
        obj = json.loads(source)
    except json.JSONDecodeError as exc:
        note = (
            "文件过大且截断后不是合法 JSON"
            if truncated
            else type(exc).__name__
        )
        return _unsupported(truncated=truncated, note=note)

    pretty = json.dumps(obj, indent=2, ensure_ascii=False)
    pretty_bytes = pretty.encode("utf-8")
    text = pretty if len(pretty_bytes) <= _TEXT_CAP else _cap_utf8(pretty, _TEXT_CAP)
    small = isinstance(obj, (dict, list)) and len(pretty_bytes) <= _TEXT_CAP

    payload: dict[str, Any] = {}
    if small:
        payload["data"] = obj
    payload["text"] = text
    if isinstance(obj, dict):
        payload["keys"] = list(obj)
    return Preview(kind="json", payload=payload, truncated=truncated)
