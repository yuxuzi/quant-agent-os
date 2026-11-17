"""Static charts: a `ChartBlock` drawn as SVG by pygal, with no script and no external link.

pygal comes with the ``toolbox`` extra. Importing this module never fails; drawing without pygal
raises `quant_toolbox.MissingExtraError`.
"""

from __future__ import annotations

import math
import re
from typing import Any

from quant_toolbox._optional import require
from quant_toolbox.report._document import COLOURS, ChartBlock

WIDTH, HEIGHT = 720, 320
LABELS = 5  # date labels on a line chart's axis
_XML_DECLARATION = re.compile(r"^<\?xml[^>]*\?>\s*")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_SCRIPT = re.compile(r"<script\b.*?</script>", re.DOTALL)


def chart_svg(
    block: ChartBlock, *, width: int = WIDTH, height: int = HEIGHT, standalone: bool = False
) -> str:
    """``block`` as SVG markup: inline for an HTML page, or a ``.svg`` file with ``standalone``."""
    pygal = require("pygal", purpose="drawing static charts")
    chart = _chart(pygal, block, width, height)
    if standalone:
        chart.title = block.title
    svg = str(chart.render(is_unicode=True))
    svg = _COMMENT.sub("", svg)  # pygal's credit comments carry links
    svg = _SCRIPT.sub("", svg)  # pygal always writes its configuration as a script; with js=[]
    # nothing reads it, so the chart is the same without it
    if not standalone:
        svg = _XML_DECLARATION.sub("", svg)
    return svg.strip() + "\n"


def _chart(pygal: Any, block: ChartBlock, width: int, height: int) -> Any:
    style = pygal.style.Style(
        background="transparent",
        plot_background="transparent",
        foreground="#374151",
        foreground_strong="#111827",
        foreground_subtle="#9ca3af",
        colors=COLOURS,
        font_family="system-ui, sans-serif",
        label_font_size=11,
        major_label_font_size=11,
        legend_font_size=11,
        title_font_size=13,
        value_font_size=10,
    )
    formatter = (lambda value: f"{value:.1%}") if block.percent else (lambda value: f"{value:.3g}")
    options: dict[str, Any] = {
        "js": [],  # no script: the page stays static and loads nothing
        "width": width,
        "height": height,
        "style": style,
        "show_legend": len(block.series) > 1,
        "legend_at_bottom": True,
        "value_formatter": formatter,
        "disable_xml_declaration": False,
        "explicit_size": False,
        "truncate_label": -1,
        "truncate_legend": -1,
    }
    if block.kind == "bar":
        chart = pygal.Bar(**options)
        chart.x_labels = block.labels
        for name, values in block.series.items():
            chart.add(name, [_point(value) for value in values])
        return chart
    labels = [day.isoformat() for day in block.dates]  # unique, so only the picks are major
    count = len(labels)
    picks = sorted({round(i * (count - 1) / (LABELS - 1)) for i in range(LABELS)}) if count else []
    chart = pygal.Line(
        show_dots=False,
        allow_interruptions=True,
        show_minor_x_labels=False,
        x_label_rotation=0,
        **options,
    )
    chart.x_labels = labels
    chart.x_labels_major = [labels[i] for i in picks]
    for name, values in block.series.items():
        chart.add(name, [_point(value) for value in values])
    return chart


def _point(value: float) -> float | None:
    return float(value) if value is not None and math.isfinite(value) else None
