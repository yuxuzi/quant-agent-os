"""A report as data: titled tables, charts, and text, rendered to Markdown, HTML, or JSON.

Sections return blocks and know nothing about formats, so a firm's own section appears in every
format without extra work. Every value comes from a section's calculation; nothing here computes.
A chart is data until it is written: `render_html` draws it as static SVG (pygal) by default, or
as an interactive plotly chart with ``charts="interactive"``.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from html import escape
from typing import Any

Cell = float | int | str | None
COLOURS = ("#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2")
CHART_MODES = ("static", "interactive", "interactive-cdn")


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
    """Series to draw: lines over ``dates`` (NaN leaves a gap), or bars over ``labels``.

    ``kind`` is ``line`` (the default) or ``bar``. A bar chart names its categories in
    ``labels``, one value per label in each series, and leaves ``dates`` empty.
    """

    title: str
    dates: list[dt.date]
    series: dict[str, list[float]]
    note: str = ""
    kind: str = "line"
    labels: list[str] = field(default_factory=list)
    percent: bool = False  # format the values as percentages

    def __post_init__(self) -> None:
        if self.kind not in {"line", "bar"}:
            raise ValueError(f"A chart is a line or a bar chart, not {self.kind!r}.")
        width = len(self.labels) if self.kind == "bar" else len(self.dates)
        for name, values in self.series.items():
            if len(values) != width:
                raise ValueError(f"Series {name!r} has {len(values)} values for {width} points.")


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


def render_html(document: Document, *, charts: str = "static") -> str:
    """Return ``document`` as one HTML page; every value is escaped.

    ``charts`` is ``static`` (SVG drawn by pygal: the page is self-contained and runs no
    script), ``interactive`` (plotly, with plotly.js inline once, so the page works offline), or
    ``interactive-cdn`` (plotly.js from its CDN: a small file that needs the viewer online).
    """
    if charts not in CHART_MODES:
        raise ValueError(f"charts must be one of {', '.join(CHART_MODES)}, not {charts!r}.")
    drawn = 0
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
            parts.append(_draw(block, charts, first=drawn == 0))
            drawn += 1
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
            chart: dict[str, Any] = {
                "type": "chart",
                "title": block.title,
                "dates": [day.isoformat() for day in block.dates],
                "series": {
                    name: [_plain(value) for value in values]
                    for name, values in block.series.items()
                },
            }
            if block.kind == "bar":
                chart |= {"kind": "bar", "labels": block.labels}
            blocks.append(chart)
        else:
            blocks.append({"type": "text", "title": block.title, "lines": block.lines})
    return {"title": document.title, "subtitle": document.subtitle, "blocks": blocks}


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


def _draw(block: ChartBlock, charts: str, *, first: bool) -> str:
    if charts == "static":
        from quant_toolbox.report.svg import chart_svg

        return chart_svg(block)
    from quant_toolbox.report.interactive import chart_html

    return chart_html(
        block, include_plotlyjs=("cdn" if charts.endswith("cdn") else True) if first else False
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
svg { width: 100%; height: auto; }
@media print { body { max-width: none; } section { break-inside: avoid; } }
"""
