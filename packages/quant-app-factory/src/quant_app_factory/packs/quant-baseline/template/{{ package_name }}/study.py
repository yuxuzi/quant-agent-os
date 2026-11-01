"""A signal study: evaluate one signal on a price panel and package the results."""

from __future__ import annotations

from typing import Any

from . import config, signals
from .data import Panel
from .evaluation import ic_decay, ic_summary, quantile_spread, signal_autocorrelation
from .metrics import bar_returns, summarize
from .parameters import Parameters

LIMITATIONS = [
    "Prices are taken as given: survivorship, corporate actions, and point-in-time data are "
    "the data provider's responsibility.",
    "The quantile spread is a gross, hypothetical one-bar return after the trade, before costs.",
]


def run_study(panel: Panel, parameters: Parameters | None = None) -> dict[str, Any]:
    """Evaluate the signal in ``parameters`` on ``panel`` and return a results document.

    ``parameters.lag`` is the trade lag a backtest of this signal would use, so the study scores
    what a lagged trade could actually earn.
    """
    chosen = parameters or Parameters()
    lag, delisting = chosen.lag, chosen.delisting_return
    signal_parameters = signals.parameters(
        chosen.signal, lookback=chosen.lookback, skip=chosen.skip
    )
    signal = signals.compute(chosen.signal, panel.prices, **signal_parameters)
    spread = summarize(
        quantile_spread(signal, panel.prices, chosen.quantiles, lag, delisting),
        config.PERIODS_PER_YEAR,
    )
    decay = ic_decay(signal, panel.prices, chosen.max_horizon, lag, delisting)
    return {
        "kind": "study",
        "title": f"{chosen.signal} ({signals.describe(signal_parameters)}) signal study",
        "period": [panel.dates[0].isoformat(), panel.dates[-1].isoformat()],
        "bars": len(panel.dates),
        "assets": len(panel.assets),
        "parameters": {
            "signal": chosen.signal,
            **signal_parameters,
            "quantiles": chosen.quantiles,
            "lag": lag,
            "max_horizon": chosen.max_horizon,
            "delisting_return": delisting,
        },
        "metrics": {
            "delistings": float(bar_returns(panel.prices)[1].sum()),
            **ic_summary(signal, panel.prices, lag=lag, delisting_return=delisting),
            "signal_autocorrelation": signal_autocorrelation(signal),
            **{f"spread_{key}": value for key, value in spread.items()},
        },
        "series": {f"ic_decay_{key}": values for key, values in decay.items()},
        "conventions": {
            "periods_per_year": config.PERIODS_PER_YEAR,
            "timing": f"signal at close t, traded at close t + {lag}, as in a backtest",
            "ic": f"signal at bar t against the return from t + {lag} to t + {lag} + h",
            "t_stat": "Newey-West (HAC), lags at least the horizon minus 1",
            "spread": (
                f"top minus bottom of {chosen.quantiles} signal quantiles, one bar after the trade"
            ),
            "delistings": f"an asset whose prices stop earns {delisting:+.0%} once",
        },
        "limitations": LIMITATIONS,
    }
