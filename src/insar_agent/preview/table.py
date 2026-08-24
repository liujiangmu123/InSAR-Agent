"""CSV/TSV 表格预览:流式翻页,不把整文件一次塞进内存;不编造单元格。"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from insar_agent.preview.dispatch import Preview, current_opts, register

MAX_ROWS = 200
MAX_COLS = 40

_ENCODINGS = ("utf-8-sig", "utf-8", "gbk")
_DELIMS = (",", "\t", ";")


def _detect_encoding(path: Path) -> str:
    head = path.read_bytes()[:8192]
    if not head:
        return "utf-8"
    for enc in _ENCODINGS:
        try:
            head.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "utf-8"


def _sniff_delimiter(first_line: str, suffix: str) -> str:
    line = first_line.rstrip("\r\n")
    counts = {d: line.count(d) for d in _DELIMS}
    best = max(_DELIMS, key=lambda d: counts[d])
    if counts[best] == 0:
        return "\t" if suffix == ".tsv" else ","
    return best


def preview_from_text(text: str, *, suffix: str = ".csv") -> Preview:
    """内存文本(zip 成员 / 测试)走同一套表格窗口。"""
    return _from_reader_source(text, suffix=suffix)


def _table(
    *,
    columns: list[str],
    rows: list[list[str]],
    n_rows_total: int,
    n_cols_total: int,
    truncated: bool,
    note: str,
    offset: int,
    col_offset: int,
    limit: int,
    col_limit: int,
    sheet: str | None = None,
) -> Preview:
    return Preview(
        kind="table",
        payload={
            "columns": columns,
            "rows": rows,
            "n_rows_total": n_rows_total,
            "n_cols_total": n_cols_total,
            "sheet": sheet,
            "offset": offset,
            "col_offset": col_offset,
            "limit": limit,
            "col_limit": col_limit,
        },
        truncated=truncated,
        note=note,
    )


def _empty(note: str) -> Preview:
    opts = current_opts()
    return _table(
        columns=[],
        rows=[],
        n_rows_total=0,
        n_cols_total=0,
        truncated=False,
        note=note,
        offset=opts.offset,
        col_offset=opts.col_offset,
        limit=opts.limit,
        col_limit=opts.col_limit,
    )


def _from_reader_source(text: str, *, suffix: str) -> Preview:
    if not text.strip():
        return _empty("空文件")
    nl = text.find("\n")
    first_line = text[:nl] if nl >= 0 else text
    delim = _sniff_delimiter(first_line, suffix)
    return _consume(csv.reader(io.StringIO(text), delimiter=delim))


def _consume(reader: csv.reader) -> Preview:
    opts = current_opts()
    offset = opts.offset
    col_offset = opts.col_offset
    limit = opts.limit
    col_limit = opts.col_limit
    columns: list[str] | None = None
    rows: list[list[str]] = []
    n_rows_total = 0
    n_cols_total = 0
    try:
        for i, row in enumerate(reader):
            cells = ["" if c is None else str(c) for c in row]
            n_cols_total = max(n_cols_total, len(cells))
            if i == 0:
                columns = cells[col_offset: col_offset + col_limit]
                continue
            n_rows_total += 1
            if n_rows_total <= offset:
                continue
            if len(rows) < limit:
                rows.append(cells[col_offset: col_offset + col_limit])
    except csv.Error:
        return _empty("CSV 解析失败")

    if columns is None:
        return _empty("空文件")

    page_end = offset + len(rows)
    truncated = (
        n_rows_total > page_end
        or offset > 0
        or n_cols_total > col_offset + len(columns)
        or col_offset > 0
    )
    if n_rows_total == 0 and not any(columns):
        note = "空文件"
        truncated = False
    elif truncated:
        note = (
            f"第 {offset + 1}–{max(offset, page_end)} 行"
            f"（共 {n_rows_total} 行 × {n_cols_total} 列,可翻页）"
        )
    else:
        note = f"{n_rows_total} 行 × {n_cols_total} 列（完整本页）"

    return _table(
        columns=columns,
        rows=rows,
        n_rows_total=n_rows_total,
        n_cols_total=n_cols_total,
        truncated=truncated,
        note=note,
        offset=offset,
        col_offset=col_offset,
        limit=limit,
        col_limit=col_limit,
    )


@register(".csv", ".tsv")
def preview_csv(path: Path) -> Preview:
    try:
        size = path.stat().st_size
    except OSError:
        return _empty("无法读取文件")
    if size == 0:
        return _empty("空文件")
    enc = _detect_encoding(path)
    suffix = path.suffix.lower()
    try:
        with path.open("r", encoding=enc, newline="", errors="replace") as fh:
            first = fh.readline()
            if not first:
                return _empty("空文件")
            delim = _sniff_delimiter(first, suffix)
            fh.seek(0)
            return _consume(csv.reader(fh, delimiter=delim))
    except OSError:
        return _empty("无法读取文件")
