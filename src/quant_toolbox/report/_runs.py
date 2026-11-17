"""Run reports: one run, or several compared, as Markdown, with an equity curve chart.

A run is a results document: ``kind``, ``title``, ``period``, ``bars``, ``assets``,
``parameters``, ``metrics``, ``series`` (``dates`` and ``net_returns``), ``conventions``,
``limitations``, and, once saved, ``provenance``. Computed figures and interpretation stay apart:
everything the report prints comes from the results, and the interpretation section is left for
a person to write.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

from quant_toolbox.report._document import ChartBlock
from quant_toolbox.report._format import format_metric

INTERPRETATION = [
    "",
    "## Interpretation",
    "",
    "_Not written yet. Nothing above this section is interpretation._",
]


def run_markdown(results: dict[str, Any], chart: str | None = None) -> str:
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
        lines.append(f"| {name.replace('_', ' ')} | {format_metric(name, value)} |")
    years = run_calendar_years(results)
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
            score = format_metric("training_score", fold.get("training_score"))
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


def comparison_markdown(runs: list[tuple[str, dict[str, Any]]], chart: str | None = None) -> str:
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
        ("Computed results", "metrics", format_metric),
    ):
        keys = sorted({k for _, results in runs for k in (results.get(key) or {})})
        lines += ["", f"## {title}", "", *header]
        for name in keys:
            values = [(results.get(key) or {}).get(name) for _, results in runs]
            cells = ["" if value is None else formatter(name, value) for value in values]
            lines.append(row(name.replace("_", " "), cells))
    years = [run_calendar_years(results) for _, results in runs]
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


def run_calendar_years(results: dict[str, Any]) -> dict[str, float]:
    """Compounded net return per calendar year, from the results' dated return series."""
    series = results.get("series") or {}
    years: dict[str, float] = {}
    pairs = zip(series.get("dates") or [], series.get("net_returns") or [], strict=False)
    for day, value in pairs:
        if value is not None and math.isfinite(value):
            years[day[:4]] = (1.0 + years.get(day[:4], 0.0)) * (1.0 + value) - 1.0
    return years


def equity_chart(runs: list[tuple[str, dict[str, Any]]]) -> ChartBlock | None:
    """Each run's compounded net wealth over the union of their dates, or None without series."""
    curves: dict[str, dict[dt.date, float]] = {}
    for name, results in runs:
        series = results.get("series") or {}
        days = [dt.date.fromisoformat(day) for day in series.get("dates") or []]
        values = series.get("net_returns") or []
        if len(days) != len(values) or len(days) < 2:
            continue
        level, curve = 1.0, {}
        for day, value in zip(days, values, strict=True):
            level *= 1.0 + (value if value is not None and math.isfinite(value) else 0.0)
            curve[day] = level
        curves[name] = curve
    if not curves:
        return None
    dates = sorted({day for curve in curves.values() for day in curve})
    return ChartBlock(
        title="Equity curve, net of costs" if len(curves) == 1 else "Equity curves, net of costs",
        dates=dates,
        series={
            name: [curve.get(day, math.nan) for day in dates] for name, curve in curves.items()
        },
    )


def _code_version(results: dict[str, Any]) -> str:
    code = (results.get("provenance") or {}).get("code_version") or {}
    return str(code.get("git") or code.get("package") or "?")


def _short(fingerprint: object) -> str:
    text = str(fingerprint or "?")
    return text[:19] if text.startswith("sha256:") else text
