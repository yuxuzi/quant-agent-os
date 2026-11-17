"""A backtest of signal-driven weights: which weights this project builds, and its results.

The engine is Quant Toolbox's `run_backtest`: lagged trades, a rebalance schedule, drift, costs,
and delistings, with one timing convention shared by studies (a signal known at the close of bar
t trades ``lag`` bars later). Its conventions are documented in `quant_toolbox.research`.
"""

from __future__ import annotations

from typing import Any

from quant_toolbox.data import FloatArray, Panel
from quant_toolbox.performance import active_metrics, beta, summarize
from quant_toolbox.research import (
    beta_neutral_weights,
    dollar_neutral_weights,
    long_only_weights,
    market_returns,
    rolling_betas,
    run_backtest,
)

from . import config, signals
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
    if parameters.exposure == "beta-neutral":
        return beta_neutral_weights(signal, rolling_betas(prices, parameters.beta_window))
    if parameters.exposure == "long-only":
        return long_only_weights(signal, parameters.quantiles)
    return dollar_neutral_weights(signal)


def run(panel: Panel, parameters: Parameters | None = None) -> dict[str, Any]:
    """Backtest the signal in ``parameters`` on ``panel`` and return a results document."""
    chosen = parameters or Parameters()
    signal_parameters = signals.parameters(
        chosen.signal, lookback=chosen.lookback, skip=chosen.skip
    )
    signal = signals.compute(chosen.signal, panel.prices, **signal_parameters)
    result = run_backtest(
        panel.prices,
        weights_for(panel.prices, signal, chosen),
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
    book = "long-only" if chosen.exposure == "long-only" else f"{chosen.exposure} long-short"
    return {
        "kind": "backtest",
        "title": f"{chosen.signal} ({signals.describe(signal_parameters)}) {book} backtest",
        "period": [panel.dates[0].isoformat(), panel.dates[-1].isoformat()],
        "bars": len(panel.dates),
        "assets": len(panel.assets),
        "parameters": {
            "signal": chosen.signal,
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
            "delistings": f"a delisted holding earns {chosen.delisting_return:+.0%} once",
            "weights": WEIGHTS[chosen.exposure],
            "market": "equal-weight mean of assets priced at the previous close",
        },
        "limitations": LIMITATIONS,
    }
