"""A buy-side risk report: exposures, tail risk, risk contributions, stress, liquidity, and factors.

Each section lays out the result of a `quant_toolbox.risk` or `quant_toolbox.performance` function
and computes no measure itself. `risk_report` runs those functions on a book's weights and asset
returns, in the order most risk reports use: size and concentration, exposure by group, value at
risk and expected shortfall, contribution to risk, historical stress, liquidity, factor risk, and
correlation among the largest positions. A section whose inputs are not given is left out. The
result is a `Document`, which renders like any other report.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence

import numpy as np

from quant_toolbox._types import FloatArray
from quant_toolbox.performance import (
    Series,
    expected_shortfall,
    parametric_expected_shortfall,
    parametric_var,
    value_at_risk,
)
from quant_toolbox.report._document import (
    Block,
    Cell,
    Column,
    Document,
    TableBlock,
    format_cell,
)
from quant_toolbox.risk import (
    NO_ADV,
    UNGROUPED,
    CorrelationSummary,
    Exposures,
    FactorRisk,
    Liquidity,
    VolatilityBreakdown,
    correlation_summary,
    factor_risk,
    group_exposures,
    historical_stress,
    liquidity,
    simulated_returns,
    volatility_contributions,
)
from quant_toolbox.risk import exposures as measure_exposures


def exposure_table(exposures: Exposures, *, title: str = "Exposure") -> TableBlock:
    """The book's size and concentration: gross, net, long and short, and how many positions."""
    rows: list[list[Cell]] = [
        ["Gross", format_cell(exposures.gross, "percent")],
        ["Net", format_cell(exposures.net, "percent")],
        ["Long", format_cell(exposures.long, "percent")],
        ["Short", format_cell(exposures.short, "percent")],
        ["Positions", format_cell(exposures.positions, "number")],
        ["Largest position", format_cell(exposures.largest, "percent")],
        ["Top five positions", format_cell(exposures.top_five, "percent")],
        ["Effective positions", format_cell(exposures.effective_positions, "ratio")],
    ]
    return TableBlock(
        title,
        [Column("Measure", "text"), Column("Value", "text")],
        rows,
        note="Shares of NAV, shorts negative. Effective positions is 1 over the sum of squared "
        "shares of gross.",
    )


def group_exposure_table(
    assets: Sequence[str],
    weights: Sequence[float] | FloatArray,
    groups: Mapping[str, str],
    *,
    title: str = "Exposure by group",
) -> TableBlock:
    """Net and gross exposure by group, largest gross first; the book's held positions only."""
    totals = group_exposures(list(assets), np.asarray(weights, dtype=np.float64), dict(groups))
    rows: list[list[Cell]] = [[group, net, gross] for group, (net, gross) in totals.items()]
    return TableBlock(
        title,
        [Column("Group", "text"), Column("Net"), Column("Gross")],
        rows,
        note=f"Shares of NAV. Assets with no group are listed as {UNGROUPED}.",
        shade=True,
    )


def tail_risk_table(
    returns: Series,
    *,
    levels: Sequence[float] = (0.95, 0.99),
    title: str = "Value at risk and expected shortfall",
) -> TableBlock:
    """Historical and parametric value at risk and expected shortfall, one column per level.

    The cutoff of a level is one minus it. Values are per bar, not annualised, and are returns:
    a loss is negative.
    """
    for level in levels:
        if not 0.0 < level < 1.0:
            raise ValueError(f"A confidence level is between 0 and 1; got {level}.")
    cutoffs = [1.0 - level for level in levels]
    columns = [Column("Measure", "text"), *(Column(f"{level * 100:g}%") for level in levels)]
    rows: list[list[Cell]] = [
        ["Historical VaR", *(value_at_risk(returns, cutoff) for cutoff in cutoffs)],
        ["Historical ES", *(expected_shortfall(returns, cutoff) for cutoff in cutoffs)],
        ["Parametric VaR", *(parametric_var(returns, cutoff) for cutoff in cutoffs)],
        ["Parametric ES", *(parametric_expected_shortfall(returns, cutoff) for cutoff in cutoffs)],
    ]
    return TableBlock(
        title,
        columns,
        rows,
        note="Per bar, as returns: a loss is negative, and the 95% column reads the 5% cutoff. "
        "Historical VaR is the cutoff percentile of the returns; expected shortfall is their mean "
        "at or below it. Parametric figures assume normal returns.",
    )


def contribution_table(
    breakdown: VolatilityBreakdown,
    *,
    top: int | None = None,
    title: str = "Contribution to risk",
) -> TableBlock:
    """Each asset's Euler contribution to volatility, largest in absolute terms first.

    Marginal is the change in volatility for a unit of weight; component is weight times
    marginal, and the components sum to volatility. Shown as n/a where volatility is zero.
    """
    if top is not None and top < 1:
        raise ValueError(f"top is a number of assets, at least 1; got {top}.")
    ordered = sorted(
        breakdown.contributions.items(),
        key=lambda item: (item[1].component is None, -abs(item[1].component or 0.0)),
    )
    shown = ordered if top is None else ordered[:top]
    rows: list[list[Cell]] = [
        [name, c.weight, c.volatility, c.marginal, c.component, c.percent] for name, c in shown
    ]
    columns = [
        Column("Asset", "text"),
        Column("Weight"),
        Column("Volatility"),
        Column("Marginal"),
        Column("Component"),
        Column("Share"),
    ]
    trimmed = (
        f" Showing the top {len(shown)} of {len(ordered)}." if len(shown) < len(ordered) else ""
    )
    note = (
        f"Total volatility {format_cell(breakdown.volatility, 'percent')}; diversification ratio "
        f"{format_cell(breakdown.diversification_ratio, 'ratio')}. Components sum to volatility, "
        f"shares to one; a negative share is a hedge.{trimmed}"
    )
    return TableBlock(title, columns, rows, note=note)


def stress_table(
    results: Mapping[str, float | None], *, title: str = "Historical stress"
) -> TableBlock:
    """Today's book over each historical window; n/a where the history does not cover it."""
    rows: list[list[Cell]] = [[name, value] for name, value in results.items()]
    return TableBlock(
        title,
        [Column("Scenario", "text"), Column("Return")],
        rows,
        note="Today's weights applied to each window's compounded asset returns, without "
        "rebalancing. n/a means the history does not cover the window.",
        shade=True,
    )


def liquidity_table(result: Liquidity, *, title: str = "Liquidity") -> TableBlock:
    """Share of gross by liquidity bucket, with the cumulative share sold within each horizon.

    Cumulative is `Liquidity.within` at each bucket's last day; the last timed bucket carries
    everything timed. Positions with no ADV cannot be timed and are shown on their own row.
    """
    horizons = list(result.within)
    timed = [(name, share) for name, share in result.buckets.items() if name != NO_ADV]
    rows: list[list[Cell]] = []
    cumulative = 0.0
    for index, (name, share) in enumerate(timed):
        cumulative += share
        within = result.within[horizons[index]] if index < len(horizons) else cumulative
        rows.append([name, share, within])
    rows.append([NO_ADV, result.buckets[NO_ADV], None])
    listed = ", ".join(result.no_adv) or "none"
    return TableBlock(
        title,
        [Column("Bucket", "text"), Column("Share of gross"), Column("Cumulative")],
        rows,
        note=f"Each position is sold at {format_cell(result.participation, 'percent')} of its "
        f"average daily volume, all positions in parallel. Positions with no positive ADV: "
        f"{listed}.",
    )


def factor_table(result: FactorRisk, *, title: str = "Factor risk") -> TableBlock:
    """Each factor's exposure and share of variance, then the specific share.

    Contribution is a factor's share times total volatility (Euler), so the rows sum to
    volatility.
    """
    shares = result.factor_shares or {}
    volatility = result.volatility
    rows: list[list[Cell]] = [
        [
            factor,
            exposure,
            shares.get(factor),
            None if shares.get(factor) is None else shares[factor] * volatility,
        ]
        for factor, exposure in result.exposures.items()
    ]
    specific = result.specific_share
    rows.append(["Specific", None, specific, None if specific is None else specific * volatility])
    note = (
        f"Total volatility {format_cell(volatility, 'percent')}; factor volatility "
        f"{format_cell(result.factor_volatility, 'percent')}; specific volatility "
        f"{format_cell(result.specific_volatility, 'percent')}. Shares of variance sum to one; "
        "contribution is the share times total volatility."
    )
    return TableBlock(
        title,
        [
            Column("Factor", "text"),
            Column("Exposure", "ratio"),
            Column("Share of variance"),
            Column("Contribution"),
        ],
        rows,
        note=note,
    )


def correlation_table(summary: CorrelationSummary, *, title: str = "Correlation") -> TableBlock:
    """The correlation matrix, with its average and its highest and lowest pairs."""
    rows: list[list[Cell]] = [
        [name, *(float(value) for value in summary.matrix[index])]
        for index, name in enumerate(summary.names)
    ]
    columns = [Column("Name", "text"), *(Column(name, "ratio") for name in summary.names)]

    def pair(found: tuple[str, str, float] | None) -> str:
        if found is None:
            return "n/a"
        return f"{found[0]} and {found[1]} ({format_cell(found[2], 'ratio')})"

    note = (
        f"Average {format_cell(summary.average, 'ratio')} over distinct pairs. Highest "
        f"{pair(summary.highest)}; lowest {pair(summary.lowest)}. Pearson correlation over "
        f"{summary.observations} bars where every series is finite."
    )
    return TableBlock(title, columns, rows, note=note, shade=True)


def risk_report(
    *,
    weights: Mapping[str, float],
    dates: Sequence[dt.date],
    asset_returns: Mapping[str, Series],
    periods_per_year: int,
    groups: Mapping[str, str] | None = None,
    adv: Mapping[str, float] | None = None,
    positions: Mapping[str, float] | None = None,
    scenarios: Mapping[str, tuple[dt.date, dt.date]] | None = None,
    factor_exposures: Mapping[str, Mapping[str, float]] | None = None,
    factor_covariance: Mapping[str, Mapping[str, float]] | None = None,
    specific_variance: Mapping[str, float] | None = None,
    top: int = 10,
    title: str = "Risk report",
    subtitle: str | None = None,
    disclaimer: str = "",
) -> Document:
    """A risk report on a book of ``weights`` (shares of NAV, shorts negative), as a `Document`.

    ``asset_returns`` holds one series per held asset, one return per date. Volatility and
    correlation use missing returns as 0 in that bar, as the simulation does. Sections added when
    their inputs are given: exposure by group (``groups``), liquidity (``adv`` and ``positions``,
    which go together; positions are money), and factor risk (the three factor inputs, which go
    together, as `factor_risk` takes them). Correlation covers the ``top`` held assets by absolute
    weight, and is left out when fewer than two are held.
    """
    if (adv is None) != (positions is None):
        raise ValueError("Liquidity needs both adv and positions, or neither.")
    factor_inputs = (factor_exposures, factor_covariance, specific_variance)
    if any(item is not None for item in factor_inputs) and not all(
        item is not None for item in factor_inputs
    ):
        raise ValueError(
            "Factor risk needs factor_exposures, factor_covariance and specific_variance together."
        )
    if len(dates) < 2:
        raise ValueError(f"A risk report needs at least two dates; got {len(dates)}.")
    if top < 1:
        raise ValueError(f"top is a number of assets, at least 1; got {top}.")
    held = [name for name, weight in weights.items() if math.isfinite(weight) and weight != 0.0]
    if not held:
        raise ValueError("The book holds no positions to report on.")
    for name in held:
        if name not in asset_returns:
            raise ValueError(f"No returns for {name}, which the book holds.")
        count = len(asset_returns[name])
        if count != len(dates):
            raise ValueError(f"{name} has {count} returns for {len(dates)} dates.")

    held_weights = {name: float(weights[name]) for name in held}
    vector = np.array([held_weights[name] for name in held], dtype=np.float64)
    returns = np.column_stack([np.asarray(asset_returns[name], dtype=np.float64) for name in held])
    clean = np.nan_to_num(returns)
    blocks: list[Block] = [exposure_table(measure_exposures(vector))]
    if groups is not None:
        blocks.append(group_exposure_table(held, vector, groups))
    blocks.append(tail_risk_table(simulated_returns(vector, returns)))
    breakdown = volatility_contributions(
        held_weights,
        {name: clean[:, index].tolist() for index, name in enumerate(held)},
        periods_per_year,
    )
    blocks.append(contribution_table(breakdown, top=top))
    blocks.append(stress_table(historical_stress(vector, dates, returns, scenarios)))
    if adv is not None and positions is not None:
        blocks.append(liquidity_table(liquidity(positions, adv)))
    if (
        factor_exposures is not None
        and factor_covariance is not None
        and specific_variance is not None
    ):
        result = factor_risk(held_weights, factor_exposures, factor_covariance, specific_variance)
        blocks.append(factor_table(result))
    chosen = sorted(held, key=lambda name: -abs(held_weights[name]))[:top]
    if len(chosen) >= 2:
        series = {name: asset_returns[name] for name in chosen}
        blocks.append(correlation_table(correlation_summary(series)))
    default = (
        f"{len(dates)} returns from {dates[0].isoformat()} to {dates[-1].isoformat()}, "
        f"{periods_per_year} per year; {len(held)} positions."
    )
    return Document(
        title,
        subtitle=default if subtitle is None else subtitle,
        blocks=blocks,
        footer=disclaimer,
    )
