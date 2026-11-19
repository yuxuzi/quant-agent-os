"""PDF reports: a `Document` drawn on A4 by fpdf2 (the ``pdf`` extra), charts and tables as vectors.

fpdf2 is pure Python. Importing this module never fails; rendering without fpdf2 raises
`quant_toolbox.MissingExtraError`, naming the extra to install. Charts are drawn here from a
`ChartBlock`'s data, not from pygal's SVG, whose styling fpdf2 cannot read. Text is in the PDF's
built-in Helvetica, so a character outside Latin-1 is replaced: a dash or quote by its plain form.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from quant_toolbox._optional import require
from quant_toolbox.report._document import (
    COLOURS,
    Block,
    ChartBlock,
    Document,
    TableBlock,
    format_cell,
)

WIDTH = 190.0  # A4, less 10 mm margins, in mm
CHART_HEIGHT = 70.0
_TEXT = (31, 41, 55)
_MUTED = (75, 85, 99)
_RULE = (209, 213, 219)
_GRID = (229, 231, 235)
_GAIN = (21, 128, 61)
_LOSS = (185, 28, 28)
# Minus, en and em dashes, curly quotes, an ellipsis, and a no-break space, as Latin-1 text.
_PLAIN = str.maketrans(
    {
        0x2212: "-", 0x2013: "-", 0x2014: "-", 0x2018: "'", 0x2019: "'", 0x201C: '"',
        0x201D: '"', 0x2026: "...", 0x00A0: " ",
    }
)  # fmt: skip


def render_pdf(document: Document) -> bytes:
    """Return ``document`` as a PDF: title, subtitle, then each block in order."""
    fpdf = require("fpdf", extra="pdf", purpose="writing PDF reports")
    pdf = fpdf.FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(10, 12, 10)
    pdf.set_auto_page_break(True, margin=12)
    pdf.set_title(_plain(document.title))
    pdf.set_creator("quant-toolbox")
    pdf.add_page()
    pdf.set_text_color(*_TEXT)
    pdf.set_font("helvetica", "B", 16)
    pdf.multi_cell(WIDTH, 8, _plain(document.title), new_x="LMARGIN", new_y="NEXT")
    if document.subtitle:
        _note(pdf, document.subtitle, size=9)
    for block in document.blocks:
        _block(fpdf, pdf, block)
    if document.footer:
        pdf.ln(4)
        _note(pdf, document.footer)
    return bytes(pdf.output())


def write_pdf(document: Document, path: Path) -> Path:
    """Write ``document`` to ``path`` as a PDF and return the path."""
    path.write_bytes(render_pdf(document))
    return path


def _block(fpdf: Any, pdf: Any, block: Block) -> None:
    room = CHART_HEIGHT + 14 if isinstance(block, ChartBlock) else 24
    if pdf.will_page_break(room):
        pdf.add_page()
    pdf.ln(3)
    pdf.set_text_color(*_TEXT)
    pdf.set_font("helvetica", "B", 11)
    pdf.cell(WIDTH, 7, _plain(block.title), new_x="LMARGIN", new_y="NEXT")
    if isinstance(block, TableBlock):
        _table(fpdf, pdf, block)
    elif isinstance(block, ChartBlock):
        _chart(pdf, block)
    else:
        pdf.set_font("helvetica", "", 8.5)
        for line in block.lines:
            pdf.multi_cell(WIDTH, 4.5, _plain(f"- {line}"), new_x="LMARGIN", new_y="NEXT")
    if isinstance(block, TableBlock | ChartBlock) and block.note:
        _note(pdf, block.note)


def _table(fpdf: Any, pdf: Any, block: TableBlock) -> None:
    if not block.columns:
        _note(pdf, "No rows.")
        return
    text = [column.kind == "text" for column in block.columns]
    pdf.set_font("helvetica", "", 8)
    pdf.set_draw_color(*_RULE)
    pdf.set_line_width(0.2)
    with pdf.table(
        width=WIDTH,
        col_widths=[2.2 if is_text else 1.0 for is_text in text],
        text_align=["LEFT" if is_text else "RIGHT" for is_text in text],
        borders_layout="HORIZONTAL_LINES",
        headings_style=fpdf.FontFace(emphasis="BOLD", fill_color=(243, 244, 246)),
        line_height=4.6,
        padding=0.8,
    ) as table:
        table.row([_plain(column.name) for column in block.columns])
        for values in block.rows:
            row = table.row()
            for value, column in zip(values, block.columns, strict=True):
                colour = _sign_colour(value) if block.shade and column.kind != "text" else None
                style = fpdf.FontFace(color=colour) if colour else None
                row.cell(_plain(format_cell(value, column.kind)), style=style)


def _chart(pdf: Any, block: ChartBlock) -> None:
    """Draw ``block``: axes, a light grid, the series, and a legend below."""
    left, top = pdf.l_margin, pdf.get_y() + 1
    x0, y0, width, height = left + 16, top + 2, WIDTH - 18, CHART_HEIGHT - 16
    points = [value for values in block.series.values() for value in values if math.isfinite(value)]
    pdf.set_font("helvetica", "", 7)
    pdf.set_text_color(*_MUTED)
    if not points:
        pdf.set_xy(x0, y0 + height / 2)
        pdf.cell(width, 4, "No data to draw.", align="C")
        pdf.set_y(top + CHART_HEIGHT)
        return
    low, high = min(points), max(points)
    if block.kind == "bar":
        low, high = min(low, 0.0), max(high, 0.0)
    if high == low:
        low, high = low - 1.0, high + 1.0
    span = high - low

    def y_of(value: float) -> float:
        return float(y0 + height - (value - low) / span * height)

    pdf.set_line_width(0.15)
    for step in range(5):
        value = low + span * step / 4
        pdf.set_draw_color(*_GRID)
        pdf.line(x0, y_of(value), x0 + width, y_of(value))
        pdf.set_xy(left, y_of(value) - 2)
        pdf.cell(15, 4, _tick(value, span, block.percent), align="R")
    if block.kind == "bar":
        _bars(pdf, block, x0, y0, width, height, y_of)
    else:
        _lines(pdf, block, x0, y0, width, height, y_of)
    _legend(pdf, list(block.series), x0, y0 + height + 7)
    pdf.set_y(top + CHART_HEIGHT)
    pdf.set_text_color(*_TEXT)


def _lines(pdf: Any, block: ChartBlock, x0: float, y0: float, width: float, height: float,
           y_of: Any) -> None:  # fmt: skip
    count = len(block.dates)
    step = width / max(count - 1, 1)
    pdf.set_line_width(0.35)
    for index, values in enumerate(block.series.values()):
        pdf.set_draw_color(*_rgb(COLOURS[index % len(COLOURS)]))
        run: list[tuple[float, float]] = []
        for position, value in enumerate([*values, math.nan]):  # NaN ends the last run
            if math.isfinite(value):
                run.append((x0 + position * step, y_of(value)))
                continue
            if len(run) > 1:
                pdf.polyline(run)
            run = []
    for position in sorted({0, (count - 1) // 2, count - 1}):
        pdf.set_xy(x0 + position * step - 12, y0 + height + 1)
        pdf.cell(24, 4, block.dates[position].isoformat(), align="C")


def _bars(pdf: Any, block: ChartBlock, x0: float, y0: float, width: float, height: float,
          y_of: Any) -> None:  # fmt: skip
    group = width / max(len(block.labels), 1)
    bar = group * 0.8 / max(len(block.series), 1)
    for index, values in enumerate(block.series.values()):
        pdf.set_fill_color(*_rgb(COLOURS[index % len(COLOURS)]))
        for position, value in enumerate(values):
            if math.isfinite(value):
                top, bottom = sorted((y_of(value), y_of(0.0)))
                x = x0 + position * group + group * 0.1 + index * bar
                pdf.rect(x, top, bar, max(bottom - top, 0.1), style="F")
    pdf.set_draw_color(*_MUTED)
    pdf.line(x0, y_of(0.0), x0 + width, y_of(0.0))
    for position, name in enumerate(block.labels):
        pdf.set_xy(x0 + position * group, y0 + height + 1)
        pdf.cell(group, 4, _plain(name)[:14], align="C")


def _legend(pdf: Any, names: list[str], x: float, y: float) -> None:
    pdf.set_line_width(0.8)
    for index, name in enumerate(names):
        label = _plain(name)
        room = 8 + pdf.get_string_width(label) + 4
        if x + room > pdf.l_margin + WIDTH:
            break
        pdf.set_draw_color(*_rgb(COLOURS[index % len(COLOURS)]))
        pdf.line(x, y + 2, x + 6, y + 2)
        pdf.set_xy(x + 7, y)
        pdf.cell(room - 7, 4, label)
        x += room


def _note(pdf: Any, text: str, *, size: float = 7.5) -> None:
    pdf.set_font("helvetica", "", size)
    pdf.set_text_color(*_MUTED)
    pdf.multi_cell(WIDTH, size * 0.5, _plain(text), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_TEXT)


def _sign_colour(value: object) -> tuple[int, int, int] | None:
    if not isinstance(value, int | float) or not math.isfinite(value) or value == 0:
        return None
    return _GAIN if value > 0 else _LOSS


def _tick(value: float, span: float, percent: bool) -> str:
    if percent:
        return f"{value:.1%}" if span < 0.2 else f"{value:.0%}"
    return f"{value:,.0f}" if span >= 100 else f"{value:,.2f}"


def _rgb(colour: str) -> tuple[int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def _plain(text: str) -> str:
    """``text`` in Latin-1, which the PDF's built-in fonts hold; anything else becomes ``?``."""
    return text.translate(_PLAIN).encode("latin-1", "replace").decode("latin-1")
