"""PDF 预览:页数来自解析库;无库则 unsupported;不编造页数。"""

from __future__ import annotations

from pathlib import Path

import pytest

from insar_agent.preview import pdf_view
from insar_agent.preview.dispatch import preview_file
from insar_agent.preview.pdf_view import preview_pdf


def _write_pdf(path: Path, *, pages: int, title: str | None = None) -> None:
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    if title is not None:
        writer.add_metadata({"/Title": title})
    with path.open("wb") as fh:
        writer.write(fh)


def test_missing_pdf_lib(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pdf_view, "_pdf_reader_cls", lambda: None)
    monkeypatch.setattr(pdf_view, "_have_fitz", lambda: False)
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.4\n")
    result = preview_pdf(path)
    assert result.kind == "unsupported"
    assert result.payload.get("reason") == "missing_pdf_lib"
    assert result.note == "未安装 pypdf，无法预览 PDF（可用外部阅读器）"
    assert result.png is None
    assert "pages" not in result.payload


def test_pypdf_page_count_and_title(tmp_path: Path) -> None:
    path = tmp_path / "named.pdf"
    _write_pdf(path, pages=2, title="InSAR Report")
    result = preview_file(path)
    assert result.kind == "pdf"
    assert result.payload["pages"] == 2
    assert result.payload["title"] == "InSAR Report"


def test_pypdf_title_null_when_absent(tmp_path: Path) -> None:
    path = tmp_path / "blank.pdf"
    _write_pdf(path, pages=1)
    result = preview_file(path)
    assert result.kind == "pdf"
    assert result.payload["pages"] == 1
    assert result.payload["title"] is None


def test_invalid_pdf_unsupported(tmp_path: Path) -> None:
    pytest.importorskip("pypdf")
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"this is not a pdf")
    result = preview_file(path)
    assert result.kind == "unsupported"
    assert "pages" not in result.payload


def test_no_renderer_meta_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pdf_view, "_render_first_page", lambda _p: None)
    path = tmp_path / "meta.pdf"
    _write_pdf(path, pages=1, title="X")
    result = preview_pdf(path)
    assert result.kind == "pdf"
    assert result.payload["pages"] == 1
    assert result.png is None
    assert result.note == "仅页数元数据，未渲染页面"
    assert result.as_json()["has_image"] is False


def test_first_page_png_when_fitz(tmp_path: Path) -> None:
    pytest.importorskip("pypdf")
    pytest.importorskip("fitz")
    path = tmp_path / "img.pdf"
    _write_pdf(path, pages=1)
    result = preview_file(path)
    assert result.kind == "pdf"
    assert result.payload["pages"] == 1
    assert result.png is not None
    assert result.png[:8] == b"\x89PNG\r\n\x1a\n"
    assert result.as_json()["has_image"] is True
