"""PDF 预览:页数与可选首页 PNG;页数只来自解析库,不编造。"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from insar_agent.preview.dispatch import Preview, register

_NOTE_MISSING = "未安装 pypdf，无法预览 PDF（可用外部阅读器）"
_NOTE_META_ONLY = "仅页数元数据，未渲染页面"


def _pdf_reader_cls() -> type | None:
    try:
        from pypdf import PdfReader
        return PdfReader
    except ImportError:
        pass
    try:
        from PyPDF2 import PdfReader
        return PdfReader
    except ImportError:
        return None


def _import_fitz():
    try:
        import fitz
        return fitz
    except ImportError:
        try:
            import pymupdf as fitz
            return fitz
        except ImportError:
            return None


def _have_fitz() -> bool:
    return _import_fitz() is not None


def _title_from_meta(meta: Any) -> str | None:
    if meta is None:
        return None
    raw = None
    if hasattr(meta, "title"):
        raw = meta.title
    elif isinstance(meta, dict):
        raw = meta.get("/Title") or meta.get("title")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _pages_title_from_reader(path: Path, reader_cls: type) -> tuple[int, str | None]:
    reader = reader_cls(str(path))
    pages = len(reader.pages)
    meta = getattr(reader, "metadata", None)
    if meta is None and hasattr(reader, "getDocumentInfo"):
        meta = reader.getDocumentInfo()
    try:
        title = _title_from_meta(meta)
    except Exception:  # noqa: BLE001 — 缺标题不得拖垮页数
        title = None
    return pages, title


def _pages_title_from_fitz(path: Path) -> tuple[int, str | None]:
    fitz = _import_fitz()
    if fitz is None:
        raise RuntimeError("fitz missing")
    doc = fitz.open(path)
    try:
        pages = int(doc.page_count)
        meta = doc.metadata or {}
        title = None
        if isinstance(meta, dict):
            raw = meta.get("title")
            if raw is not None:
                title = str(raw).strip() or None
        return pages, title
    finally:
        doc.close()


def _render_first_page(path: Path) -> bytes | None:
    return _render_page(path, 0)


def _render_page(path: Path, index: int) -> bytes | None:
    fitz = _import_fitz()
    if fitz is not None:
        try:
            doc = fitz.open(path)
            try:
                n = int(doc.page_count)
                if n < 1:
                    return None
                page_i = min(max(0, int(index)), n - 1)
                pix = doc.load_page(page_i).get_pixmap()
                png = pix.tobytes("png")
                return png or None
            finally:
                doc.close()
        except Exception:  # noqa: BLE001 — 渲染失败仍可只报页数
            pass
    try:
        from pdf2image import convert_from_path
    except ImportError:
        return None
    try:
        page_no = max(1, int(index) + 1)
        images = convert_from_path(str(path), first_page=page_no, last_page=page_no)
        if not images:
            return None
        buf = BytesIO()
        images[0].save(buf, format="PNG")
        return buf.getvalue() or None
    except Exception:  # noqa: BLE001
        return None


@register(".pdf")
def preview_pdf(path: Path) -> Preview:
    reader_cls = _pdf_reader_cls()
    if reader_cls is None and not _have_fitz():
        return Preview(
            kind="unsupported",
            payload={"reason": "missing_pdf_lib"},
            note=_NOTE_MISSING,
        )

    try:
        if reader_cls is not None:
            pages, title = _pages_title_from_reader(path, reader_cls)
        else:
            pages, title = _pages_title_from_fitz(path)
    except Exception:  # noqa: BLE001 — 坏文件不得 500
        return Preview(
            kind="unsupported",
            payload={"reason": "invalid_pdf"},
            note="无法解析 PDF",
        )

    from insar_agent.preview.dispatch import current_opts

    page_i = min(max(0, current_opts().page - 1), max(0, pages - 1))
    png = _render_page(path, page_i)
    return Preview(
        kind="pdf",
        payload={"pages": pages, "title": title, "page": page_i + 1},
        png=png,
        note=None if png is not None else _NOTE_META_ONLY,
    )
