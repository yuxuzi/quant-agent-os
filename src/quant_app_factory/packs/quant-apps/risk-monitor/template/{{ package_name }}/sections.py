"""The report sections to choose from. Name them under ``sections`` in ``config.toml``.

A section takes the `RiskView` and returns blocks. Add your own in ``firm/sections.py``; a firm's
section with the same name as one here replaces it.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from .document import Block, ChartBlock, Column, TableBlock, TextBlock
from .firm.checks import CHECKS
from .firm.sections import SECTIONS as FIRM_SECTIONS
from .risk import (
    Check,
    RiskView,
    check_limits,
    group_exposures,
    risk_contributions,
    stress_return,
)
from .risk import (
    exposures as measure,
)

Section = Callable[[RiskView], list[Block]]


def all_checks(view: RiskView) -> list[Check]:
    """The limits in the settings and the firm's own checks."""
    return [*check_limits(view), *(c for check in CHECKS.values() for c in check(view))]


def limits(view: RiskView) -> list[Block]:
    """Each limit and firm check: its value, its limit, and whether it is breached."""
    checks = all_checks(view)
    if not checks:
        return [TextBlock("Limits", ["No limits are set. Add them under [limits] in config.toml."])]
    breaches = sum(check.breached for check in checks)
    return [
        TableBlock(
            title="Limits",
            columns=[
                Column("Check", "text"),
                Column("Value"),
                Column("Limit"),
                Column("Status", "text"),
                Column("Detail", "text"),
            ],
            rows=[
                [c.name, c.value, c.limit, "BREACH" if c.breached else "ok", c.detail]
                for c in checks
            ],
            note=f"{breaches} of {len(checks)} breached.",
        )
    ]


def exposures(view: RiskView) -> list[Block]:
    """Gross, net, long, short, and concentration."""
    book = measure(view.weights)
    return [
        TableBlock(
            title="Exposures",
            columns=[
                Column("Gross"),
                Column("Net"),
                Column("Long"),
                Column("Short"),
                Column("Positions", "number"),
                Column("Largest"),
                Column("Top five"),
                Column("Effective positions", "ratio"),
            ],
            rows=[
                [
                    book.gross,
                    book.net,
                    book.long,
                    book.short,
                    book.positions,
                    book.largest,
                    book.top_five,
                    book.effective_positions,
                ]
            ],
            note="As a share of NAV.",
        )
    ]


def groups(view: RiskView) -> list[Block]:
    """Net and gross exposure by group, such as sector, largest first."""
    if not view.groups:
        return [
            TextBlock("Exposure by group", ["No groups file is set: set groups in config.toml."])
        ]
    rows: list[list[float | str | None]] = [
        [name, net, gross]
        for name, (net, gross) in group_exposures(view.assets, view.weights, view.groups).items()
    ]
    return [
        TableBlock(
            title="Exposure by group",
            columns=[Column("Group", "text"), Column("Net"), Column("Gross")],
            rows=rows,
        )
    ]


def top_positions(view: RiskView) -> list[Block]:
    """The largest positions by size."""
    order = np.argsort(-np.abs(view.weights), kind="mergesort")[: view.settings.top_positions]
    rows: list[list[float | str | None]] = [
        [view.assets[int(i)], float(view.weights[int(i)])]
        for i in order
        if view.weights[int(i)] != 0.0
    ]
    return [
        TableBlock(
            title="Largest positions",
            columns=[Column("Asset", "text"), Column("Weight")],
            rows=rows,
        )
    ]


def risk(view: RiskView) -> list[Block]:
    """Return-based risk of today's book, by historical simulation."""
    figures = view.risk_figures()
    if not figures:
        return [
            TextBlock("Return-based risk", ["No prices file is set: set prices in config.toml."])
        ]
    cutoff = view.settings.var_cutoff
    columns = [Column("Ann. vol"), Column(f"VaR {cutoff:.0%}"), Column(f"ES {cutoff:.0%}")]
    row: list[float | str | None] = [
        figures["volatility"],
        figures["var"],
        figures["expected_shortfall"],
    ]
    if "beta" in figures:
        columns.append(Column("Beta", "ratio"))
        row.append(figures["beta"])
    columns.append(Column("Gross with prices"))
    row.append(view.coverage)
    assert view.asset_returns is not None
    return [
        TableBlock(
            title="Return-based risk",
            columns=columns,
            rows=[row],
            note=f"Today's weights over the past {view.asset_returns.shape[0]} bars. VaR and ES "
            "are per bar. The past may not describe the future.",
        )
    ]


def contributors(view: RiskView) -> list[Block]:
    """The positions adding most to the book's variance."""
    if view.asset_returns is None:
        return [TextBlock("Risk contributors", ["No prices file is set."])]
    shares = risk_contributions(view.weights, view.asset_returns)
    order = np.argsort(-np.abs(shares), kind="mergesort")[: view.settings.top_positions]
    rows: list[list[float | str | None]] = [
        [view.assets[int(i)], float(view.weights[int(i)]), float(shares[int(i)])]
        for i in order
        if shares[int(i)] != 0.0
    ]
    return [
        TableBlock(
            title="Risk contributors",
            columns=[Column("Asset", "text"), Column("Weight"), Column("Share of variance")],
            rows=rows,
            note="Shares of variance sum to 100% over all positions; a hedge shows as negative.",
        )
    ]


def stress(view: RiskView) -> list[Block]:
    """The book's return under each scenario in the settings."""
    if not view.settings.scenarios:
        return [
            TextBlock(
                "Stress scenarios", ["No scenarios are set: add [[scenarios]] in config.toml."]
            )
        ]
    rows: list[list[float | str | None]] = [
        [item.name, stress_return(item, view.assets, view.weights, view.groups)]
        for item in view.settings.scenarios
    ]
    return [
        TableBlock(
            title="Stress scenarios",
            columns=[Column("Scenario", "text"), Column("Book return")],
            rows=rows,
            note="Each weight times its asset's shock, linearly. Scenarios are your assumptions.",
        )
    ]


def exposure_history(view: RiskView) -> list[Block]:
    """Gross and net exposure over the position history."""
    values = np.nan_to_num(view.history.values)
    return [
        ChartBlock(
            title="Exposure over time",
            dates=view.history.dates,
            series={
                "Gross": np.abs(values).sum(axis=1).tolist(),
                "Net": values.sum(axis=1).tolist(),
            },
        )
    ]


def notes(view: RiskView) -> list[Block]:
    """What a reader should know about the data."""
    return [TextBlock("Data notes", view.notes or ["Nothing unusual was found."])]


BUILT_IN: dict[str, Section] = {
    "limits": limits,
    "exposures": exposures,
    "groups": groups,
    "top_positions": top_positions,
    "risk": risk,
    "contributors": contributors,
    "stress": stress,
    "exposure_history": exposure_history,
    "notes": notes,
}


def available() -> dict[str, Section]:
    """Every section that can be chosen: the built-in ones and the firm's own."""
    return {**BUILT_IN, **FIRM_SECTIONS}


def describe(section: Section) -> str:
    """The first line of a section's docstring."""
    return (section.__doc__ or "").strip().splitlines()[0] if section.__doc__ else ""
