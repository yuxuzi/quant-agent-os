"""Writing a report by its file's suffix, shaded tables, and a document's footer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quant_toolbox.report import (
    REPORT_FORMATS,
    Column,
    Document,
    TableBlock,
    render_html,
    render_markdown,
    to_dict,
    write_report,
)


def _document() -> Document:
    table = TableBlock(
        "Calendar years",
        [Column("Year", "text"), Column("Return")],
        [["2024", 0.12], ["2025", -0.04], ["2026", None]],
        shade=True,
    )
    return Document("Fund", blocks=[table], footer="Past performance is not a guide.")


def test_shaded_table_colours_gains_and_losses_in_html() -> None:
    html = render_html(_document())
    assert '<td class="num gain">12.00%</td>' in html
    assert '<td class="num loss">-4.00%</td>' in html
    assert '<td class="num">n/a</td>' in html
    assert "<footer>Past performance is not a guide.</footer>" in html


def test_unshaded_table_and_no_footer_are_unchanged() -> None:
    document = Document("Fund", blocks=[TableBlock("T", [Column("R")], [[0.1]])])
    html = render_html(document)
    assert '<td class="num">10.00%</td>' in html
    assert "<footer>" not in html
    assert "footer" not in to_dict(document)
    assert "shade" not in to_dict(document)["blocks"][0]


def test_footer_reaches_markdown_and_json() -> None:
    assert render_markdown(_document()).endswith("---\n\nPast performance is not a guide.\n")
    plain = to_dict(_document())
    assert plain["footer"] == "Past performance is not a guide."
    assert plain["blocks"][0]["shade"] is True


@pytest.mark.parametrize("suffix", [".html", ".md", ".json"])
def test_write_report_picks_the_format_from_the_suffix(tmp_path: Path, suffix: str) -> None:
    path = write_report(_document(), tmp_path / "out" / f"fund{suffix}")
    text = path.read_text(encoding="utf-8")
    if suffix == ".json":
        assert json.loads(text)["title"] == "Fund"
    else:
        assert "Past performance is not a guide." in text


def test_write_report_writes_pdf_and_excel(tmp_path: Path) -> None:
    pytest.importorskip("fpdf")
    pytest.importorskip("openpyxl")
    assert write_report(_document(), tmp_path / "f.pdf").read_bytes().startswith(b"%PDF")
    assert write_report(_document(), tmp_path / "f.xlsx").stat().st_size > 0


def test_write_report_refuses_an_unknown_suffix_or_charts_it_would_ignore(tmp_path: Path) -> None:
    assert ".pdf" in REPORT_FORMATS
    with pytest.raises(ValueError, match=r"not 'fund\.txt'"):
        write_report(_document(), tmp_path / "fund.txt")
    with pytest.raises(ValueError, match="applies to an HTML report"):
        write_report(_document(), tmp_path / "fund.pdf", charts="interactive")
