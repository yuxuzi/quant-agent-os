"""Risk from positions: exposures, concentration, simulated history, contributions, and stress.

Conventions:
- Positions are weights: market value over NAV, with shorts negative. Gross is the sum of absolute
  weights, and net is their sum.
- Return-based risk is historical simulation: today's weights applied to each past bar's asset
  returns, over ``lookback`` bars. It describes today's book, not what the fund earned.
- A missing asset return counts as 0 in that bar. `RiskView.coverage` is the share of gross
  exposure with prices, and an asset with no prices at all adds nothing to the risk figures.
- Volatility and value at risk follow `metrics` and `risk_metrics`; value at risk is per bar.
- A risk contribution is ``w_i * (C w)_i / (w' C w)``, with C the sample covariance of asset
  returns, so contributions sum to 1. Assets with no prices are left out.
- A stress is a return per asset applied to today's weights, linearly: no second-order effects.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

import numpy as np

from .metrics import FloatArray, annualized_volatility, bar_returns, beta
from .risk_metrics import expected_shortfall, value_at_risk
from .settings import LIMITS, Scenario, Settings
from .tables import Table

UNGROUPED = "Ungrouped"


@dataclass(frozen=True)
class Exposures:
    """The size and concentration of a book."""

    gross: float
    net: float
    long: float
    short: float
    positions: int
    largest: float  # the largest absolute weight
    top_five: float  # the sum of the five largest absolute weights
    effective_positions: float  # 1 / sum of squared shares of gross


def exposures(weights: FloatArray) -> Exposures:
    """Measure one row of weights; NaN counts as no position."""
    values = np.nan_to_num(np.asarray(weights, dtype=np.float64))
    sizes = np.sort(np.abs(values))[::-1]
    gross = float(sizes.sum())
    effective = 1.0 / float(np.sum((sizes / gross) ** 2)) if gross > 0.0 else 0.0
    return Exposures(
        gross=gross,
        net=float(values.sum()),
        long=float(values[values > 0.0].sum()),
        short=float(values[values < 0.0].sum()),
        positions=int(np.count_nonzero(values)),
        largest=float(sizes[0]) if sizes.size else 0.0,
        top_five=float(sizes[:5].sum()),
        effective_positions=effective,
    )


def group_exposures(
    assets: list[str], weights: FloatArray, groups: dict[str, str]
) -> dict[str, tuple[float, float]]:
    """Return ``{group: (net, gross)}`` for positions held, largest gross first."""
    totals: dict[str, tuple[float, float]] = {}
    for asset, weight in zip(assets, np.nan_to_num(weights), strict=True):
        if weight == 0.0:
            continue
        group = groups.get(asset, UNGROUPED)
        net, gross = totals.get(group, (0.0, 0.0))
        totals[group] = (net + float(weight), gross + abs(float(weight)))
    return dict(sorted(totals.items(), key=lambda item: -item[1][1]))


def simulated_returns(weights: FloatArray, asset_returns: FloatArray) -> FloatArray:
    """Today's ``weights`` applied to each past bar's returns (bars by assets)."""
    result: FloatArray = np.nan_to_num(asset_returns) @ np.nan_to_num(weights)
    return result


def risk_contributions(weights: FloatArray, asset_returns: FloatArray) -> FloatArray:
    """Each asset's share of the book's variance; they sum to 1, and to 0 for a riskless book."""
    held = np.nan_to_num(weights)
    if asset_returns.shape[0] < 2 or not asset_returns.shape[1]:
        return np.zeros(held.shape)
    covariance = np.atleast_2d(np.cov(np.nan_to_num(asset_returns), rowvar=False))
    marginal = covariance @ held
    variance = float(held @ marginal)
    if variance <= 0.0:
        return np.zeros(held.shape)
    shares: FloatArray = held * marginal / variance
    return shares


def stress_return(
    scenario: Scenario, assets: list[str], weights: FloatArray, groups: dict[str, str]
) -> float:
    """The book's return under ``scenario``: each weight times its asset's shock."""
    total = 0.0
    for asset, weight in zip(assets, np.nan_to_num(weights), strict=True):
        total += float(weight) * scenario.groups.get(groups.get(asset, ""), scenario.market)
    return total


@dataclass(frozen=True)
class Check:
    """One limit against its value."""

    name: str
    value: float
    limit: float
    breached: bool
    detail: str = ""


@dataclass(frozen=True)
class RiskView:
    """Everything a section or check needs, as of one date."""

    as_of: dt.date
    assets: list[str]
    weights: FloatArray
    history: Table  # all position rows up to the as-of date
    settings: Settings
    groups: dict[str, str] = field(default_factory=dict)
    asset_returns: FloatArray | None = None  # bars by ``assets``, or None without prices
    market_returns: FloatArray | None = None
    coverage: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def book_returns(self) -> FloatArray | None:
        """Today's book over past bars, or None without prices."""
        if self.asset_returns is None:
            return None
        return simulated_returns(self.weights, self.asset_returns)

    def risk_figures(self) -> dict[str, float]:
        """Volatility, value at risk, expected shortfall, and beta; empty without prices."""
        book = self.book_returns
        if book is None:
            return {}
        cutoff = self.settings.var_cutoff
        figures = {
            "volatility": annualized_volatility(book, self.settings.periods_per_year),
            "var": value_at_risk(book, cutoff),
            "expected_shortfall": expected_shortfall(book, cutoff),
        }
        if self.market_returns is not None:
            figures["beta"] = beta(book, self.market_returns)
        return figures


def build_view(
    positions: Table,
    settings: Settings,
    *,
    prices: Table | None = None,
    groups: dict[str, str] | None = None,
    as_of: dt.date | None = None,
) -> RiskView:
    """Take the last position row up to ``as_of``, and align prices with it, with no later data."""
    history = positions.until(as_of) if as_of else positions
    last = history.dates[-1]
    weights = np.nan_to_num(history.values[-1])
    notes: list[str] = []
    asset_returns: FloatArray | None = None
    market_returns: FloatArray | None = None
    coverage = 0.0
    gross = float(np.abs(weights).sum())
    if prices is not None:
        known = prices.until(last)
        if known.dates[-1] != last:
            notes.append(
                f"The latest prices are from {known.dates[-1]}, before the positions' date {last}."
            )
        priced = [asset for asset in history.columns if asset in known.columns]
        missing = [
            asset
            for asset, weight in zip(history.columns, weights, strict=True)
            if weight != 0.0 and asset not in known.columns
        ]
        if missing:
            shown = ", ".join(missing[:5]) + (", ..." if len(missing) > 5 else "")
            notes.append(
                f"No prices for {len(missing)} held assets ({shown}): not in return-based risk."
            )
        returns = _returns(known)[-settings.lookback :]
        by_asset = np.zeros((returns.shape[0], len(history.columns)))
        for asset in priced:
            by_asset[:, history.columns.index(asset)] = returns[:, known.columns.index(asset)]
        asset_returns = by_asset
        if returns.shape[0] < settings.lookback:
            notes.append(
                f"Only {returns.shape[0]} bars of returns, of {settings.lookback} asked for."
            )
        if gross > 0.0:
            covered = [
                abs(float(weight))
                for asset, weight in zip(history.columns, weights, strict=True)
                if asset in known.columns
            ]
            coverage = sum(covered) / gross
        if settings.market:
            if settings.market not in known.columns:
                notes.append(f"No market column {settings.market!r} in the prices: no beta.")
            else:
                market_returns = returns[:, known.columns.index(settings.market)]
    return RiskView(
        as_of=last,
        assets=history.columns,
        weights=weights,
        history=history,
        settings=settings,
        groups=groups or {},
        asset_returns=asset_returns,
        market_returns=market_returns,
        coverage=coverage,
        notes=notes,
    )


def check_limits(view: RiskView) -> list[Check]:
    """Compare the view with every limit in the settings; a figure with no data is a breach."""
    measured = _measured(view)
    checks = []
    for key, limit in view.settings.limits.items():
        value = measured.get(key)
        name = LIMITS[key]
        if value is None or math.isnan(value):
            checks.append(Check(name, math.nan, limit, True, "No data to measure this."))
            continue
        breached = value < limit if key == "net_min" else value > limit
        checks.append(Check(name, value, limit, breached))
    return checks


def _measured(view: RiskView) -> dict[str, float]:
    book = exposures(view.weights)
    measured = {
        "gross_max": book.gross,
        "net_max": book.net,
        "net_min": book.net,
        "single_name_max": book.largest,
        "top_five_max": book.top_five,
    }
    if view.groups:
        by_group = group_exposures(view.assets, view.weights, view.groups)
        measured["group_gross_max"] = max((gross for _, gross in by_group.values()), default=0.0)
    figures = view.risk_figures()
    if figures:
        measured["volatility_max"] = figures["volatility"]
        measured["var_max"] = -figures["var"]
    if "beta" in figures:
        measured["beta_max"] = abs(figures["beta"])
    if view.settings.scenarios:
        worst = min(
            stress_return(item, view.assets, view.weights, view.groups)
            for item in view.settings.scenarios
        )
        measured["stress_loss_max"] = -worst
    return measured


def _returns(prices: Table) -> FloatArray:
    returns, _ = bar_returns(prices.values)
    result: FloatArray = returns[1:]  # the first row has no return
    return result
