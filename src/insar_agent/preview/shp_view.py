"""ESRI Shapefile 预览:属性表来自 .dbf;bbox 仅来自 .shp 文件头,不解析环。"""

from __future__ import annotations

import struct
from pathlib import Path

from insar_agent.preview.dispatch import Preview, current_opts, register


def _sidecars(shp: Path) -> dict[str, bool]:
    stem = shp.with_suffix("")
    out = {}
    for suf in (".shp", ".shx", ".dbf", ".prj", ".cpg", ".shp.xml"):
        out[suf] = (stem.with_suffix(suf) if suf != ".shp.xml"
                    else Path(str(stem) + ".shp.xml")).is_file()
    out[".shp"] = shp.is_file()
    return out


def _cpg(shp: Path) -> str:
    cpg = shp.with_suffix(".cpg")
    if cpg.is_file():
        try:
            name = cpg.read_text(encoding="ascii", errors="ignore").strip()
        except OSError:
            name = ""
        if name:
            return name
    return "utf-8"


def _decode(raw: bytes, enc: str) -> str:
    for candidate in (enc, "utf-8", "gbk", "latin-1"):
        try:
            errors = "strict" if candidate != "latin-1" else "replace"
            return raw.decode(candidate, errors=errors).strip("\x00").strip()
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("latin-1", errors="replace").strip("\x00").strip()


def _preview_dbf(dbf: Path, enc: str) -> Preview:
    opts = current_opts()
    data = dbf.read_bytes()
    if len(data) < 32:
        return Preview(kind="unsupported", note="DBF 头过短", payload={"reason": "invalid_dbf"})
    nrec = struct.unpack_from("<I", data, 4)[0]
    header_len = struct.unpack_from("<H", data, 8)[0]
    rec_len = struct.unpack_from("<H", data, 10)[0]
    fields: list[tuple[str, int, int]] = []
    pos = 32
    while pos + 32 <= header_len and pos < len(data) and data[pos] != 0x0D:
        name = data[pos:pos + 11].split(b"\x00", 1)[0].decode("ascii", "replace").strip()
        flen = data[pos + 16]
        fields.append((name, pos, flen))
        pos += 32
    names = [n for n, _p, _l in fields]
    col_offset = opts.col_offset
    col_limit = opts.col_limit
    shown_fields = fields[col_offset: col_offset + col_limit]
    columns = [n for n, _p, _l in shown_fields]
    offset = opts.offset
    limit = opts.limit
    rows: list[list[str]] = []
    cursor = header_len
    loaded = 0
    for i in range(nrec):
        if cursor + rec_len > len(data):
            break
        rec = data[cursor: cursor + rec_len]
        cursor += rec_len
        if rec[:1] == b"*":
            continue
        if loaded < offset:
            loaded += 1
            continue
        if len(rows) >= limit:
            loaded += 1
            continue
        cells = []
        fpos = 1
        for name, _p, flen in fields:
            chunk = rec[fpos: fpos + flen]
            fpos += flen
            if name not in columns:
                continue
            cells.append(_decode(chunk, enc))
        rows.append(cells)
        loaded += 1
    # n_rows_total: undeleted count would need a full scan; header nrec is honest file record count
    n_cols_total = len(names)
    page_end = offset + len(rows)
    truncated = (nrec > page_end or offset > 0
                 or n_cols_total > col_offset + len(columns) or col_offset > 0)
    note = (
        f"属性表 .dbf · 第 {offset + 1}–{max(offset, page_end)} 条"
        f"（头记录 {nrec} × {n_cols_total} 列;不含几何）"
    )
    return Preview(
        kind="table",
        payload={
            "columns": columns,
            "rows": rows,
            "n_rows_total": nrec,
            "n_cols_total": n_cols_total,
            "sheet": dbf.name,
            "offset": offset,
            "col_offset": col_offset,
            "limit": limit,
            "col_limit": col_limit,
            "sidecars": _sidecars(dbf.with_suffix(".shp")),
        },
        truncated=truncated,
        note=note,
    )


def _shp_header_bbox(shp: Path) -> dict[str, float] | None:
    """ESRI shapefile 文件头 bbox:字节 36-67 的 4 个 little-endian double。

    头不足 100 字节则返回 None,不编造范围。不读 record/ring。
    """
    try:
        with shp.open("rb") as fh:
            header = fh.read(100)
    except OSError:
        return None
    if len(header) < 100:
        return None
    xmin, ymin, xmax, ymax = struct.unpack_from("<4d", header, 36)
    return {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax}


def _attach_shp_bbox(payload: dict, shp: Path) -> None:
    bbox = _shp_header_bbox(shp)
    if bbox is not None:
        payload["bbox"] = bbox


@register(".shp", ".dbf")
def preview_shp(path: Path) -> Preview:
    if path.suffix.lower() == ".dbf":
        try:
            return _preview_dbf(path, _cpg(path.with_suffix(".shp")))
        except Exception as exc:  # noqa: BLE001
            return Preview(
                kind="unsupported",
                note=f"DBF 解析失败:{type(exc).__name__}",
                payload={"reason": "invalid_dbf", "sidecars": _sidecars(path.with_suffix(".shp"))},
            )

    dbf = path.with_suffix(".dbf")
    sidecars = _sidecars(path)
    if not dbf.is_file():
        payload: dict = {"sidecars": sidecars}
        _attach_shp_bbox(payload, path)
        return Preview(
            kind="shp",
            payload=payload,
            note="没有同名 .dbf,无法列出属性表;几何请用 QGIS 打开",
            truncated=False,
        )
    try:
        result = _preview_dbf(dbf, _cpg(path))
        _attach_shp_bbox(result.payload, path)
        return result
    except Exception as exc:  # noqa: BLE001
        payload = {"reason": "invalid_dbf", "sidecars": sidecars}
        _attach_shp_bbox(payload, path)
        return Preview(
            kind="unsupported",
            note=f"DBF 解析失败:{type(exc).__name__}",
            payload=payload,
        )
