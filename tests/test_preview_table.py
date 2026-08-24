"""CSV/TSV 表格预览:截断、编码、分隔符嗅探;不编造行。"""

from __future__ import annotations

from pathlib import Path

from insar_agent.preview.dispatch import PreviewOpts, preview_file
from insar_agent.preview.table import MAX_COLS, MAX_ROWS, preview_csv


def _assert_table_shape(result) -> None:
    assert result.kind == "table"
    payload = result.payload
    assert set(payload) >= {"columns", "rows", "n_rows_total", "n_cols_total", "sheet",
                            "offset", "col_offset", "limit", "col_limit"}
    assert payload["sheet"] is None
    assert isinstance(payload["columns"], list)
    assert isinstance(payload["rows"], list)
    assert all(isinstance(c, str) for c in payload["columns"])
    assert all(isinstance(row, list) for row in payload["rows"])
    assert all(isinstance(cell, str) for row in payload["rows"] for cell in row)
    assert not any(isinstance(v, (bytes, bytearray)) for v in payload.values())
    assert isinstance(result.note, str) and result.note


def test_csv_truncated_honest_totals(tmp_path: Path) -> None:
    n_rows = 250
    n_cols = 50
    header = [f"c{i}" for i in range(n_cols)]
    lines = [",".join(header)]
    for r in range(n_rows):
        lines.append(",".join(f"{r}_{c}" for c in range(n_cols)))
    path = tmp_path / "wide.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = preview_file(path)
    _assert_table_shape(result)
    assert result.truncated is True
    assert result.payload["n_rows_total"] == n_rows
    assert result.payload["n_cols_total"] == n_cols
    assert len(result.payload["columns"]) == MAX_COLS
    assert len(result.payload["rows"]) == MAX_ROWS
    assert result.payload["columns"][0] == "c0"
    assert result.payload["columns"][-1] == f"c{MAX_COLS - 1}"
    assert result.payload["rows"][0][0] == "0_0"
    assert result.payload["rows"][0][-1] == f"0_{MAX_COLS - 1}"
    assert result.payload["rows"][-1][0] == f"{MAX_ROWS - 1}_0"
    assert len(result.payload["rows"][0]) == MAX_COLS
    assert result.payload["offset"] == 0
    assert result.payload["col_offset"] == 0
    assert result.payload["limit"] == MAX_ROWS
    assert result.payload["col_limit"] == MAX_COLS


def test_empty_csv(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_bytes(b"")
    result = preview_csv(path)
    _assert_table_shape(result)
    assert result.truncated is False
    assert result.payload["columns"] == []
    assert result.payload["rows"] == []
    assert result.payload["n_rows_total"] == 0
    assert result.payload["n_cols_total"] == 0
    assert "空" in result.note


def test_tsv_and_semicolon_sniff(tmp_path: Path) -> None:
    tsv = tmp_path / "a.tsv"
    tsv.write_text("h1\th2\nv1\tv2\n", encoding="utf-8")
    tsv_result = preview_file(tsv)
    _assert_table_shape(tsv_result)
    assert tsv_result.payload["columns"] == ["h1", "h2"]
    assert tsv_result.payload["rows"] == [["v1", "v2"]]
    assert tsv_result.payload["n_rows_total"] == 1
    assert tsv_result.payload["n_cols_total"] == 2
    assert tsv_result.truncated is False

    scsv = tmp_path / "eu.csv"
    scsv.write_text("a;b;c\n1;2;3\n", encoding="utf-8")
    scsv_result = preview_csv(scsv)
    _assert_table_shape(scsv_result)
    assert scsv_result.payload["columns"] == ["a", "b", "c"]
    assert scsv_result.payload["rows"] == [["1", "2", "3"]]


def test_encoding_utf8_sig_then_gbk(tmp_path: Path) -> None:
    bom = tmp_path / "bom.csv"
    bom.write_bytes("name,value\nalpha,1\n".encode("utf-8-sig"))
    bom_result = preview_csv(bom)
    _assert_table_shape(bom_result)
    assert bom_result.payload["columns"] == ["name", "value"]
    assert bom_result.payload["rows"] == [["alpha", "1"]]

    gbk = tmp_path / "gbk.csv"
    gbk.write_bytes("名称,备注\n甲,乙\n".encode("gbk"))
    gbk_result = preview_csv(gbk)
    _assert_table_shape(gbk_result)
    assert gbk_result.payload["columns"] == ["名称", "备注"]
    assert gbk_result.payload["rows"] == [["甲", "乙"]]
    assert gbk_result.payload["n_rows_total"] == 1


def test_csv_second_page_and_col_window(tmp_path: Path) -> None:
    n_rows = 250
    n_cols = 50
    header = [f"c{i}" for i in range(n_cols)]
    lines = [",".join(header)]
    for r in range(n_rows):
        lines.append(",".join(f"{r}_{c}" for c in range(n_cols)))
    path = tmp_path / "wide.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    page = preview_file(path, PreviewOpts(offset=200, limit=200))
    assert page.kind == "table"
    assert page.payload["n_rows_total"] == 250
    assert len(page.payload["rows"]) == 50
    assert page.payload["rows"][0][0] == "200_0"
    assert page.payload["rows"][-1][0] == "249_0"
    cols = preview_file(path, PreviewOpts(col_offset=40, col_limit=40))
    assert cols.payload["columns"][0] == "c40"
    assert cols.payload["columns"][-1] == "c49"
    assert cols.payload["rows"][0][0] == "0_40"
