"""Risk from positions: exposures, concentration, simulated history, contributions, and stress.

Conventions:
- Positions are weights: market value over NAV, with shorts negative. Gross is the sum of absolute
  weights, and net is their sum.
- Return-based risk is historical simulation: today's weights applied to each past bar's asset
  returns, over ``lookback`` bars. It describes today's book, not what the fund earned.
- A missing asset return counts as 0 in that bar. `RiskView.coverage` is the share of gross
  exposure with prices, and an asset with no prices at all adds nothing to the risk figures.
- Volatility, value at risk, and beta follow Quant Toolbox's stated conventions
  (`quant_toolbox.performance`); value at risk is per bar.
- A risk contribution is ``w_i * (C w)_i / (w' C w)``, with C the sample covariance of asset
  returns, so contributions sum to 1 (`quant_toolbox.risk.risk_shares`). Assets with no prices
  are left out.
- A stress is a return per asset applied to today's weights, linearly: no second-order effects.
- A historical scenario applies each asset's compounded return over a named window
  (`quant_toolbox.risk.SCENARIOS`) to today's weights. It needs daily prices across the window;
  otherwise it is not covered (None), never a shorter window under the scenario's name.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

import numpy as np
from quant_toolbox.data import FloatArray, Table
from quant_toolbox.performance import (
    annualized_volatility,
    bar_returns,
    beta,
    expected_shortfall,
    value_at_risk,
)
from quant_toolbox.risk import (
    exposures,
    group_exposures,
    historical_stress,
    simulated_returns,
    stress_return,
)

from .settings import LIMITS, Scenario, Settings


def scenario_shocks(scenario: Scenario, assets: list[str], groups: dict[str, str]) -> FloatArray:
    """Each asset's return under ``scenario``: its group's shock, or the market's."""
    return np.array(
        [scenario.groups.get(groups.get(asset, ""), scenario.market) for asset in assets],
        dtype=np.float64,
    )


def scenario_return(
    scenario: Scenario, assets: list[str], weights: FloatArray, groups: dict[str, str]
) -> float:
    """The book's return under ``scenario``: each weight times its asset's shock."""
    return stress_return(weights, scenario_shocks(scenario, assets, groups))


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
    # Every past bar's asset returns, not only the lookback, with the date each bar ends.
    return_dates: list[dt.date] = field(default_factory=list)
    all_returns: FloatArray | None = None

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

    def historical_scenarios(self) -> dict[str, float | None]:
        """Today's book over each named historical window; empty without prices."""
        if self.all_returns is None or not self.return_dates:
            return {}
        return historical_stress(self.weights, self.return_dates, self.all_returns)


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
    all_returns: FloatArray | None = None
    return_dates: list[dt.date] = []
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
        every = _returns(known)
        by_asset = np.zeros((every.shape[0], len(history.columns)))
        for asset in priced:
            by_asset[:, history.columns.index(asset)] = every[:, known.columns.index(asset)]
        all_returns, return_dates = by_asset, list(known.dates[1:])
        returns = every[-settings.lookback :]
        asset_returns = by_asset[-settings.lookback :]
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
        return_dates=return_dates,
        all_returns=all_returns,
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
            scenario_return(item, view.assets, view.weights, view.groups)
            for item in view.settings.scenarios
        )
        measured["stress_loss_max"] = -worst
    return measured


def _returns(prices: Table) -> FloatArray:
    returns, _ = bar_returns(prices.values)
    result: FloatArray = returns[1:]  # the first row has no return
    return result
