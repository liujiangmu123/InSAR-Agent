"""纯文本预览(yaml/yml/md/txt/log/par/rsc/xml)。只读字节,不执行 YAML,不当代码解析。"""

from __future__ import annotations

from pathlib import Path

from insar_agent.preview.dispatch import Preview, current_opts, register

#: 侧栏窗口;大日志用 offset 字节翻页
_MAX_BYTES = 256 * 1024


def _decode(raw: bytes) -> tuple[str, str]:
    """utf-8 → gbk → latin-1(replace)。返回 (文本, 实际编码名)。"""
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1"


@register(".yaml", ".yml", ".md", ".txt", ".log", ".par", ".rsc", ".xml", ".kml")
def preview_text(path: Path) -> Preview:
    opts = current_opts()
    size = path.stat().st_size
    start = min(opts.offset, max(0, size))
    truncated = start > 0 or size > start + _MAX_BYTES
    with path.open("rb") as fh:
        if start:
            fh.seek(start)
        raw = fh.read(_MAX_BYTES)
    text, encoding = _decode(raw)
    note = None
    if truncated:
        note = f"字节 {start}–{start + len(raw)} / {size},可翻页"
    return Preview(
        kind="text",
        payload={
            "text": text,
            "n_bytes": len(raw),
            "n_bytes_total": size,
            "offset": start,
            "encoding": encoding,
        },
        truncated=truncated,
        note=note,
    )
