"""A backtest of signal-driven weights: which weights this application builds, and its results.

The engine is Quant Toolbox's `run_backtest`: lagged trades, a rebalance schedule, drift, costs,
and delistings, with one timing convention shared by studies (a signal known at the close of bar
t trades ``lag`` bars later). Its conventions are documented in `quant_toolbox.research`. This
module chooses the weights, from a built-in rule, a firm rule, a signal, or a weights file, and
packages the results and the files for the next stage.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from quant_toolbox.data import FloatArray, Panel, Table, aligned, load_table
from quant_toolbox.performance import active_metrics, beta, summarize
from quant_toolbox.research import (
    BacktestResult,
    beta_neutral_weights,
    cost_drag,
    dollar_neutral_weights,
    long_only_weights,
    market_returns,
    rolling_betas,
    run_backtest,
)

from . import config, signals
from .firm.loaders import LOADERS
from .firm.rules import RULES
from .parameters import Parameters

WEIGHTS = {
    "dollar-neutral": "signal ranks, zero net dollars, gross exposure 1",
    "beta-neutral": (
        "signal ranks with zero net dollars and zero beta to the market (rolling betas), "
        "gross exposure 1"
    ),
    "long-only": "equal weights on the top signal quantile, against the equal-weight market",
}

LIMITATIONS = [
    "Prices are taken as given: survivorship, corporate actions, and point-in-time data are "
    "the data provider's responsibility.",
    "Costs are linear in turnover; there is no market impact, borrow cost, or capacity limit.",
    "This is a simulation. It places no orders and connects to no broker.",
]


def weights_for(prices: FloatArray, signal: FloatArray, parameters: Parameters) -> FloatArray:
    """Target weights for ``parameters.exposure``."""
    if parameters.exposure in RULES:
        return RULES[parameters.exposure](prices, signal, parameters)
    if parameters.exposure == "beta-neutral":
        return beta_neutral_weights(signal, rolling_betas(prices, parameters.beta_window))
    if parameters.exposure == "long-only":
        return long_only_weights(signal, parameters.quantiles)
    return dollar_neutral_weights(signal)


def inputs_for(panel: Panel, chosen: Parameters) -> tuple[FloatArray, dict[str, int], str, str]:
    """Return target weights, signal parameters, a name, and how the weights were built."""
    if chosen.weights_file:
        table = load_table(Path(chosen.weights_file), LOADERS)
        weights = np.nan_to_num(aligned(table, panel, "weights"))
        name = Path(chosen.weights_file).stem
        return weights, {}, name, f"target weights from the file {Path(chosen.weights_file).name}"
    if chosen.signal_file:
        table = load_table(Path(chosen.signal_file), LOADERS)
        signal = aligned(table, panel, "signal")
        signal_parameters: dict[str, int] = {}
        name = Path(chosen.signal_file).stem
    else:
        if chosen.signal not in signals.PARAMETERS:
            bad = signals.look_ahead_bars(chosen.signal, panel.prices)
            if bad:
                raise ValueError(
                    f"Signal {chosen.signal} looks ahead: its value at bar {bad[0]} changes when "
                    "later prices are removed. Fix it before trusting any result."
                )
        signal_parameters = signals.parameters(
            chosen.signal, lookback=chosen.lookback, skip=chosen.skip
        )
        signal = signals.compute(chosen.signal, panel.prices, **signal_parameters)
        name = chosen.signal
    described = WEIGHTS.get(chosen.exposure, f"the firm rule {chosen.exposure}")
    return weights_for(panel.prices, signal, chosen), signal_parameters, name, described


def tables_from(panel: Panel, result: BacktestResult) -> dict[str, Table]:
    """The files for the next stage: net and gross returns, and the weights held."""
    returns = np.column_stack([result.net, result.gross])
    return {
        "returns.csv": Table(panel.dates, ["net", "gross"], returns),
        "weights.csv": Table(panel.dates, panel.assets, result.held),
    }


def run(panel: Panel, parameters: Parameters | None = None) -> dict[str, Any]:
    """Backtest ``parameters`` on ``panel`` and return a results document."""
    results, _ = run_with_tables(panel, parameters)
    return results


def run_with_tables(
    panel: Panel, parameters: Parameters | None = None
) -> tuple[dict[str, Any], dict[str, Table]]:
    """Return the results document, and the tables for the next stage."""
    chosen = parameters or Parameters()
    weights, signal_parameters, name, described = inputs_for(panel, chosen)
    result = run_backtest(
        panel.prices,
        weights,
        cost_bps=chosen.cost_bps,
        lag=chosen.lag,
        rebalance_every=chosen.rebalance_every,
        delisting_return=chosen.delisting_return,
    )
    periods = config.PERIODS_PER_YEAR
    market = market_returns(panel.prices, chosen.delisting_return)
    exposure_metrics = {"beta": beta(result.net[1:], market[1:])}
    if chosen.exposure == "long-only":
        exposure_metrics |= active_metrics(result.net[1:], market[1:], periods)
    drag = cost_drag(result, periods)
    book = "long-only" if chosen.exposure == "long-only" else f"{chosen.exposure} long-short"
    details = f" ({signals.describe(signal_parameters)})" if signal_parameters else ""
    results = {
        "kind": "backtest",
        "title": f"{name}{details} {book} backtest",
        "period": [panel.dates[0].isoformat(), panel.dates[-1].isoformat()],
        "bars": len(panel.dates),
        "assets": len(panel.assets),
        "parameters": {
            "signal": name,
            **signal_parameters,
            "cost_bps": chosen.cost_bps,
            "lag": chosen.lag,
            "rebalance_every": chosen.rebalance_every,
            "delisting_return": chosen.delisting_return,
            "exposure": chosen.exposure,
        },
        "metrics": {
            **summarize(result.net, periods),
            **exposure_metrics,
            "gross_annualized_return": summarize(result.gross, periods)["annualized_return"],
            "average_turnover": float(result.turnover[1:].mean()),
            "annual_turnover": float(result.turnover[1:].mean()) * periods,
            "forced_exits": float(result.forced_exits.sum()),
            "cost_drag": drag.cost_drag,
            "cost_per_turnover_bps": drag.cost_per_turnover_bps,
            "break_even_cost_bps": drag.break_even_bps,
        },
        "series": {
            "dates": [day.isoformat() for day in panel.dates],
            "net_returns": [float(value) for value in result.net],
        },
        "conventions": {
            "periods_per_year": periods,
            "timing": f"signal at close t, traded at close t + {chosen.lag}, as in a study",
            "rebalance": f"every {chosen.rebalance_every} bars; holdings drift in between",
            "costs": f"{chosen.cost_bps} bps per unit of one-way turnover, on actual trades",
            "cost_drag": (
                "gross minus net mean return per bar, times periods per year (arithmetic); "
                "break_even_cost_bps is the one rate that takes the mean net return to zero"
            ),
            "delistings": f"a delisted holding earns {chosen.delisting_return:+.0%} once",
            "weights": described,
            "market": "equal-weight mean of assets priced at the previous close",
        },
        "limitations": LIMITATIONS,
    }
    return results, tables_from(panel, result)
