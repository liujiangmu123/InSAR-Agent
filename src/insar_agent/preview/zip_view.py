"""Zip / KMZ:中央目录 + 可选成员预览(图/KML/CSV)。不解压落盘。"""

from __future__ import annotations

import zipfile
from pathlib import Path

from insar_agent.preview.dispatch import Preview, current_opts, register

_MAX_MEMBERS = 200
_MAX_MEMBER_BYTES = 8 * 1024 * 1024
_TEXT_CAP = 64 * 1024
_IMAGE_SUF = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_TEXT_SUF = {".kml", ".xml", ".txt", ".md", ".json", ".csv", ".tsv", ".log"}


def _safe_member(zf: zipfile.ZipFile, name: str) -> zipfile.ZipInfo | None:
    want = name.replace("\\", "/")
    if not want or want.startswith("/") or ".." in want.split("/"):
        return None
    for info in zf.infolist():
        if info.filename.replace("\\", "/") == want:
            return info
    return None


def _read_capped(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes | None:
    if info.file_size > _MAX_MEMBER_BYTES:
        return None
    try:
        data = zf.read(info)
    except Exception:  # noqa: BLE001
        return None
    if len(data) > _MAX_MEMBER_BYTES:
        return data[:_MAX_MEMBER_BYTES]
    return data


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


@register(".zip", ".kmz")
def preview_zip(path: Path) -> Preview:
    try:
        zf = zipfile.ZipFile(path, "r")
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError):
        return Preview(
            kind="unsupported",
            note="不是合法 zip/kmz",
            payload={"reason": "invalid_zip"},
        )
    try:
        infos = zf.infolist()
        total = len(infos)
        members = [
            {"name": info.filename, "size": int(info.file_size)}
            for info in infos[:_MAX_MEMBERS]
        ]
        opts = current_opts()
        payload: dict = {
            "members": members,
            "n_members_total": total,
        }
        png: bytes | None = None
        media = "image/png"
        truncated = total > _MAX_MEMBERS
        note = None

        target = opts.member
        if not target:
            names = [i.filename.replace("\\", "/") for i in infos]
            for n in names:
                if n.lower().endswith((".png", ".jpg", ".jpeg")):
                    target = n
                    break
            if target is None:
                for n in names:
                    if n.lower().endswith(".kml"):
                        target = n
                        break

        if target:
            info = _safe_member(zf, target)
            if info is None:
                payload["member"] = target
                note = "成员不存在或路径非法"
            else:
                payload["member"] = info.filename
                data = _read_capped(zf, info)
                suf = Path(info.filename).suffix.lower()
                if data is None:
                    note = f"{info.filename} 超过 {_MAX_MEMBER_BYTES} 字节,未展开"
                    truncated = True
                elif suf in _IMAGE_SUF:
                    png = data
                    media = {
                        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                        ".gif": "image/gif", ".webp": "image/webp",
                    }.get(suf, "image/png")
                    payload["member_kind"] = "image"
                elif suf in {".csv", ".tsv"}:
                    from insar_agent.preview.table import preview_from_text
                    inner = preview_from_text(_decode(data), suffix=suf)
                    payload.update({
                        "member_kind": "table",
                        "columns": inner.payload.get("columns"),
                        "rows": inner.payload.get("rows"),
                        "n_rows_total": inner.payload.get("n_rows_total"),
                        "n_cols_total": inner.payload.get("n_cols_total"),
                        "offset": inner.payload.get("offset"),
                        "col_offset": inner.payload.get("col_offset"),
                        "limit": inner.payload.get("limit"),
                        "col_limit": inner.payload.get("col_limit"),
                    })
                    truncated = truncated or inner.truncated
                    note = inner.note
                else:
                    start = min(max(0, int(opts.offset)), len(data))
                    chunk = data[start:start + _TEXT_CAP]
                    text = _decode(chunk)
                    payload["member_kind"] = "text"
                    payload["member_text"] = text
                    payload["n_bytes"] = len(chunk)
                    payload["n_bytes_total"] = int(info.file_size)
                    payload["offset"] = start
                    if suf == ".kml":
                        payload["kml_text"] = text
                    if start > 0 or info.file_size > start + len(chunk):
                        truncated = True
                        note = (
                            f"{info.filename} 字节 {start}–{start + len(chunk)}"
                            f" / {info.file_size},可翻页"
                        )
        return Preview(
            kind="zip",
            payload=payload,
            png=png,
            truncated=truncated,
            note=note,
            media_type=media,
        )
    finally:
        zf.close()
