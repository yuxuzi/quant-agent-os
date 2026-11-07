"""A report as data: titled tables, charts, and text, rendered to Markdown, HTML, or JSON.

Sections return blocks and know nothing about formats, so a firm's own section appears in every
format without extra work. Every value comes from a section's calculation; nothing here computes.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from html import escape
from typing import Any

Cell = float | int | str | None
COLOURS = ("#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2")


@dataclass(frozen=True)
class Column:
    """A table column; ``kind`` is percent, ratio, number, or text."""

    name: str
    kind: str = "percent"


@dataclass(frozen=True)
class TableBlock:
    """A table of cells, with None shown as n/a."""

    title: str
    columns: list[Column]
    rows: list[list[Cell]]
    note: str = ""


@dataclass(frozen=True)
class ChartBlock:
    """Line series over dates; NaN values leave a gap."""

    title: str
    dates: list[dt.date]
    series: dict[str, list[float]]
    note: str = ""


@dataclass(frozen=True)
class TextBlock:
    """A short titled list of statements."""

    title: str
    lines: list[str]


Block = TableBlock | ChartBlock | TextBlock


@dataclass(frozen=True)
class Document:
    """A rendered report's content."""

    title: str
    subtitle: str = ""
    blocks: list[Block] = field(default_factory=list)


def format_cell(value: Cell, kind: str) -> str:
    """Return a cell as text: None and NaN are n/a, and text columns are left as they are."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    if isinstance(value, str) or kind == "text":
        return str(value)
    if kind == "percent":
        return f"{value:.2%}"
    if kind == "number":
        return f"{value:,.0f}"
    return f"{value:.2f}"


def render_markdown(document: Document) -> str:
    """Return ``document`` as Markdown. Charts are not drawn here; the HTML report has them."""
    lines = [f"# {document.title}", ""]
    if document.subtitle:
        lines += [document.subtitle, ""]
    for block in document.blocks:
        lines += [f"## {block.title}", ""]
        if isinstance(block, TableBlock):
            lines.append("| " + " | ".join(_md(c.name) for c in block.columns) + " |")
            lines.append("|" + " --- |" * len(block.columns))
            for row in block.rows:
                cells = [
                    _md(format_cell(value, column.kind))
                    for value, column in zip(row, block.columns, strict=True)
                ]
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("")
            if block.note:
                lines += [f"_{_md(block.note)}_", ""]
        elif isinstance(block, ChartBlock):
            lines += ["_A chart: see the HTML report._", ""]
        else:
            lines += [f"- {_md(line)}" for line in block.lines] + [""]
    return "\n".join(lines).rstrip() + "\n"


def render_html(document: Document) -> str:
    """Return ``document`` as one self-contained HTML page; every value is escaped."""
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{escape(document.title)}</title>",
        f"<style>{_STYLE}</style></head><body>",
        f"<h1>{escape(document.title)}</h1>",
    ]
    if document.subtitle:
        parts.append(f'<p class="subtitle">{escape(document.subtitle)}</p>')
    for block in document.blocks:
        parts.append(f"<section><h2>{escape(block.title)}</h2>")
        if isinstance(block, TableBlock):
            parts.append(_html_table(block))
            if block.note:
                parts.append(f'<p class="note">{escape(block.note)}</p>')
        elif isinstance(block, ChartBlock):
            parts.append(line_chart(block))
            if block.note:
                parts.append(f'<p class="note">{escape(block.note)}</p>')
        else:
            items = "".join(f"<li>{escape(line)}</li>" for line in block.lines)
            parts.append(f"<ul>{items}</ul>")
        parts.append("</section>")
    parts.append("</body></html>")
    return "\n".join(parts) + "\n"


def to_dict(document: Document) -> dict[str, Any]:
    """Return ``document`` as plain data for JSON, with NaN as None."""
    blocks: list[dict[str, Any]] = []
    for block in document.blocks:
        if isinstance(block, TableBlock):
            blocks.append(
                {
                    "type": "table",
                    "title": block.title,
                    "columns": [column.name for column in block.columns],
                    "rows": [[_plain(value) for value in row] for row in block.rows],
                    "note": block.note,
                }
            )
        elif isinstance(block, ChartBlock):
            blocks.append(
                {
                    "type": "chart",
                    "title": block.title,
                    "dates": [day.isoformat() for day in block.dates],
                    "series": {
                        name: [_plain(value) for value in values]
                        for name, values in block.series.items()
                    },
                }
            )
        else:
            blocks.append({"type": "text", "title": block.title, "lines": block.lines})
    return {"title": document.title, "subtitle": document.subtitle, "blocks": blocks}


def line_chart(block: ChartBlock, width: int = 720, height: int = 300) -> str:
    """Return ``block`` as an inline SVG, with a labelled line per series."""
    finite = [v for values in block.series.values() for v in values if math.isfinite(v)]
    if not finite or len(block.dates) < 2:
        return "<p>Not enough data to draw.</p>"
    left, right, top, bottom = 64, 16, 12, 44 + 18 * len(block.series)
    low, high = min(finite), max(finite)
    span = max(high - low, 1e-9)
    plot_w, plot_h = width - left - right, height - top - bottom
    first = block.dates[0].toordinal()
    days = max(block.dates[-1].toordinal() - first, 1)

    def x_of(day: dt.date) -> float:
        return left + (day.toordinal() - first) / days * plot_w

    def y_of(value: float) -> float:
        return top + (high - value) / span * plot_h

    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(block.title)}">',
        f'<rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" class="frame"/>',
        f'<text x="{left - 6}" y="{top + 10}" text-anchor="end">{high:.3g}</text>',
        f'<text x="{left - 6}" y="{top + plot_h}" text-anchor="end">{low:.3g}</text>',
        f'<text x="{left}" y="{top + plot_h + 16}">{block.dates[0].isoformat()}</text>',
        f'<text x="{left + plot_w}" y="{top + plot_h + 16}" text-anchor="end">'
        f"{block.dates[-1].isoformat()}</text>",
    ]
    for index, (name, values) in enumerate(block.series.items()):
        colour = COLOURS[index % len(COLOURS)]
        for run in _runs(block.dates, values):
            points = " ".join(f"{x_of(day):.1f},{y_of(value):.1f}" for day, value in run)
            parts.append(
                f'<polyline fill="none" stroke="{colour}" stroke-width="1.5" points="{points}"/>'
            )
        y = top + plot_h + 34 + 18 * index
        parts.append(f'<rect x="{left}" y="{y - 9}" width="10" height="10" fill="{colour}"/>')
        parts.append(f'<text x="{left + 16}" y="{y}">{escape(name)}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def _runs(dates: list[dt.date], values: list[float]) -> list[list[tuple[dt.date, float]]]:
    runs: list[list[tuple[dt.date, float]]] = [[]]
    for day, value in zip(dates, values, strict=True):
        if math.isfinite(value):
            runs[-1].append((day, value))
        elif runs[-1]:
            runs.append([])
    return [run for run in runs if len(run) > 1]


def _html_table(block: TableBlock) -> str:
    head = "".join(f"<th>{escape(column.name)}</th>" for column in block.columns)
    rows = []
    for row in block.rows:
        cells = []
        for value, column in zip(row, block.columns, strict=True):
            kind = "text" if column.kind == "text" else "num"
            cells.append(f'<td class="{kind}">{escape(format_cell(value, column.kind))}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        '<div class="scroll"><table><thead><tr>'
        + head
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _plain(value: Cell) -> Cell:
    return None if isinstance(value, float) and not math.isfinite(value) else value


_STYLE = """
body { font: 14px/1.5 system-ui, sans-serif; color: #1f2937; background: #fff;
  max-width: 960px; margin: 0 auto; padding: 24px 16px; }
h1 { font-size: 24px; margin: 0 0 4px; } h2 { font-size: 16px; margin: 28px 0 8px; }
.subtitle, .note { color: #4b5563; } .note { font-size: 12px; }
.scroll { overflow-x: auto; } table { border-collapse: collapse; width: 100%; }
th, td { padding: 4px 8px; border-bottom: 1px solid #e5e7eb; text-align: right;
  white-space: nowrap; }
th:first-child, td.text { text-align: left; } td.num { font-variant-numeric: tabular-nums; }
svg { width: 100%; height: auto; font-size: 11px; fill: #4b5563; }
svg .frame { fill: none; stroke: #d1d5db; }
@media print { body { max-width: none; } section { break-inside: avoid; } }
"""
