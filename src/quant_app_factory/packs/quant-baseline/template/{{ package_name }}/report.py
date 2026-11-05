"""Reports: render one run, or compare several, as Markdown with an SVG equity curve.

Computed figures and interpretation stay apart: everything the report prints comes from the
results files, and the interpretation section is left for a person to write.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any
from xml.sax.saxutils import escape

PERCENT_METRICS = frozenset(
    {
        "annualized_return",
        "annualized_volatility",
        "max_drawdown",
        "hit_rate",
        "gross_annualized_return",
        "average_turnover",
        "annual_turnover",
        "positive_rank_ic_share",
        "active_return",
        "tracking_error",
    }
)
COUNT_METRICS = frozenset({"bars", "forced_exits", "delistings", "folds", "out_of_sample_bars"})


COLOURS = ("#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2")
INTERPRETATION = [
    "",
    "## Interpretation",
    "",
    "_Not written yet. Nothing above this section is interpretation._",
]


def render_markdown(results: dict[str, Any], chart: str | None = None) -> str:
    """Return a Markdown report of ``results``; ``chart`` names an equity-curve image to show."""
    title = str(results.get("title") or f"{results.get('kind', 'research')} results")
    start, end = results.get("period") or ["?", "?"]
    lines = [
        f"# {title}",
        "",
        "## Data",
        "",
        f"- Kind: {results.get('kind', 'unknown')}",
        f"- Period: {start} to {end} ({results.get('bars', '?')} bars, "
        f"{results.get('assets', '?')} assets)",
    ]
    provenance = results.get("provenance") or {}
    if provenance:
        code = provenance.get("code_version") or {}
        git = f" (git {code['git']})" if code.get("git") else ""
        lines += [
            f"- Data fingerprint: {provenance.get('data_fingerprint', '?')}",
            f"- Code version: {code.get('package', '?')}{git}",
            f"- Run at: {provenance.get('created', '?')}",
        ]
    parameters = results.get("parameters") or {}
    if parameters:
        lines.append(
            "- Parameters: " + ", ".join(f"{key} = {value}" for key, value in parameters.items())
        )
    lines += ["", "## Computed results", "", "| Metric | Value |", "| --- | --- |"]
    for name, value in sorted((results.get("metrics") or {}).items()):
        lines.append(f"| {name.replace('_', ' ')} | {format_value(name, value)} |")
    years = calendar_years(results)
    if years:
        lines += ["", "## Calendar years", "", "| Year | Net return |", "| --- | --- |"]
        lines += [f"| {year} | {value:.2%} |" for year, value in years.items()]
    if chart:
        lines += ["", f"![Equity curve]({chart})"]
    folds = results.get("folds") or []
    if folds:
        lines += ["", "## Folds", "", "| Train | Test | Lookback | Training score |"]
        lines.append("| --- | --- | --- | --- |")
        for fold in folds:
            score = format_value("training_score", fold.get("training_score"))
            lines.append(
                f"| {' to '.join(fold['train'])} | {' to '.join(fold['test'])} | "
                f"{fold['lookback']} | {score} |"
            )
    conventions = results.get("conventions") or {}
    lines += ["", "## Conventions", ""]
    lines += [f"- {key}: {value}" for key, value in sorted(conventions.items())]
    lines += [*INTERPRETATION, "", "## Limitations", ""]
    lines += [f"- {item}" for item in results.get("limitations") or ["None recorded."]]
    return "\n".join(lines) + "\n"


def render_comparison(runs: list[tuple[str, dict[str, Any]]], chart: str | None = None) -> str:
    """Return a Markdown report comparing ``runs`` (name, results) side by side."""
    names = [name for name, _ in runs]
    header = ["| | " + " | ".join(names) + " |", "| --- |" + " --- |" * len(runs)]

    def row(label: str, values: list[str]) -> str:
        return f"| {label} | " + " | ".join(values) + " |"

    lines = [f"# Comparison of {len(runs)} runs", "", "## Runs", "", *header]
    for label, read in (
        ("title", lambda r: str(r.get("title", "?"))),
        ("kind", lambda r: str(r.get("kind", "?"))),
        ("period", lambda r: " to ".join(r.get("period") or ["?", "?"])),
        ("data", lambda r: _short((r.get("provenance") or {}).get("data_fingerprint"))),
        ("code", _code_version),
    ):
        lines.append(row(label, [read(results) for _, results in runs]))
    fingerprints = {(r.get("provenance") or {}).get("data_fingerprint") for _, r in runs}
    if len(fingerprints) > 1:
        lines += ["", "_These runs used different data (the fingerprints differ)._"]
    for title, key, formatter in (
        ("Parameters", "parameters", lambda _name, value: str(value)),
        ("Computed results", "metrics", format_value),
    ):
        keys = sorted({k for _, results in runs for k in (results.get(key) or {})})
        lines += ["", f"## {title}", "", *header]
        for name in keys:
            values = [(results.get(key) or {}).get(name) for _, results in runs]
            cells = ["" if value is None else formatter(name, value) for value in values]
            lines.append(row(name.replace("_", " "), cells))
    years = [calendar_years(results) for _, results in runs]
    all_years = sorted({year for table in years for year in table})
    if all_years:
        lines += ["", "## Calendar years", "", *header]
        for year in all_years:
            lines.append(row(year, [f"{t[year]:.2%}" if year in t else "" for t in years]))
    if chart:
        lines += ["", f"![Equity curves]({chart})"]
    limitations = list(dict.fromkeys(item for _, r in runs for item in r.get("limitations") or []))
    lines += [*INTERPRETATION, "", "## Limitations", ""]
    lines += [f"- {item}" for item in limitations or ["None recorded."]]
    return "\n".join(lines) + "\n"


def calendar_years(results: dict[str, Any]) -> dict[str, float]:
    """Compounded net return per calendar year, from the results' dated return series."""
    series = results.get("series") or {}
    years: dict[str, float] = {}
    pairs = zip(series.get("dates") or [], series.get("net_returns") or [], strict=False)
    for day, value in pairs:
        if value is not None and math.isfinite(value):
            years[day[:4]] = (1.0 + years.get(day[:4], 0.0)) * (1.0 + value) - 1.0
    return years


def equity_svg(
    runs: list[tuple[str, dict[str, Any]]], width: int = 640, height: int = 320
) -> str | None:
    """An SVG of each run's compounded net wealth over time, or None without return series."""
    curves = []
    for name, results in runs:
        series = results.get("series") or {}
        days = [dt.date.fromisoformat(day).toordinal() for day in series.get("dates") or []]
        wealth, level = [], 1.0
        for value in series.get("net_returns") or []:
            level *= 1.0 + (value if value is not None and math.isfinite(value) else 0.0)
            wealth.append(level)
        if len(days) == len(wealth) >= 2:
            curves.append((name, days, wealth))
    if not curves:
        return None
    left, right, top, bottom = 56, 16, 16, 48 + 16 * len(curves)
    first = min(c[1][0] for c in curves)
    last = max(c[1][-1] for c in curves)
    low = min(min(c[2]) for c in curves)
    high = max(max(c[2]) for c in curves)
    span_x, span_y = max(last - first, 1), max(high - low, 1e-9)
    plot_w, plot_h = width - left - right, height - top - bottom

    def point(day: int, value: float) -> str:
        x = left + (day - first) / span_x * plot_w
        y = top + (high - value) / span_y * plot_h
        return f"{x:.1f},{y:.1f}"

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" font-family="sans-serif" font-size="12">',
        f'<rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" fill="none" '
        'stroke="#9ca3af"/>',
        f'<text x="{left - 6}" y="{top + 4}" text-anchor="end" fill="#6b7280">{high:.2f}</text>',
        f'<text x="{left - 6}" y="{top + plot_h}" text-anchor="end" fill="#6b7280">'
        f"{low:.2f}</text>",
        f'<text x="{left}" y="{top + plot_h + 16}" fill="#6b7280">'
        f"{dt.date.fromordinal(first).isoformat()}</text>",
        f'<text x="{left + plot_w}" y="{top + plot_h + 16}" text-anchor="end" fill="#6b7280">'
        f"{dt.date.fromordinal(last).isoformat()}</text>",
    ]
    if low <= 1.0 <= high:  # break-even
        y = top + (high - 1.0) / span_y * plot_h
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" '
            'stroke="#9ca3af" stroke-dasharray="4 4"/>'
        )
    for index, (name, days, wealth) in enumerate(curves):
        colour = COLOURS[index % len(COLOURS)]
        points = " ".join(point(day, value) for day, value in zip(days, wealth, strict=True))
        parts.append(
            f'<polyline fill="none" stroke="{colour}" stroke-width="1.5" points="{points}"/>'
        )
        y = top + plot_h + 36 + 16 * index
        parts.append(f'<rect x="{left}" y="{y - 9}" width="10" height="10" fill="{colour}"/>')
        parts.append(f'<text x="{left + 16}" y="{y}" fill="#374151">{escape(name)}</text>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _code_version(results: dict[str, Any]) -> str:
    code = (results.get("provenance") or {}).get("code_version") or {}
    return str(code.get("git") or code.get("package") or "?")


def _short(fingerprint: object) -> str:
    text = str(fingerprint or "?")
    return text[:19] if text.startswith("sha256:") else text


def format_value(name: str, value: Any) -> str:
    """Format a metric: percentages for return-like metrics, whole numbers for counts, and four
    decimals otherwise."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    if isinstance(value, int | float) and name in COUNT_METRICS:
        return f"{value:.0f}"
    if isinstance(value, int | float):
        return f"{value:.2%}" if name in PERCENT_METRICS else f"{value:.4f}"
    return str(value)
