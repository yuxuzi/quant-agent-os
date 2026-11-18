"""A signal study: evaluate one signal on a price panel and package the results."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from quant_toolbox.data import FloatArray, Panel, aligned, load_table
from quant_toolbox.performance import bar_returns, summarize
from quant_toolbox.research import (
    ic_decay,
    ic_summary,
    quantile_returns,
    quantile_spread,
    signal_autocorrelation,
    winsorize,
    zscore,
)

from . import config, signals
from .firm.loaders import LOADERS
from .parameters import Parameters

LIMITATIONS = [
    "Prices are taken as given: survivorship, corporate actions, and point-in-time data are "
    "the data provider's responsibility.",
    "The quantile spread is a gross, hypothetical one-bar return after the trade, before costs.",
]


def cleaned(signal: FloatArray, chosen: Parameters) -> FloatArray:
    """``signal`` winsorized, then z-scored, per bar, as ``chosen`` asks; unchanged by default."""
    if chosen.winsorize:
        signal = winsorize(signal, chosen.winsorize, 1.0 - chosen.winsorize)
    if chosen.zscore:
        signal = zscore(signal)
    return signal


def signal_for(panel: Panel, chosen: Parameters) -> tuple[FloatArray, dict[str, int], str]:
    """Return the scores, their parameters, and their name: from a file or a named signal."""
    if chosen.signal_file:
        table = load_table(Path(chosen.signal_file), LOADERS)
        return cleaned(aligned(table, panel, "signal"), chosen), {}, Path(chosen.signal_file).stem
    bad = (
        []
        if chosen.signal in signals.PARAMETERS
        else signals.look_ahead_bars(chosen.signal, panel.prices)
    )
    if bad:
        raise ValueError(
            f"Signal {chosen.signal} looks ahead: its value at bar {bad[0]} changes when later "
            "prices are removed. Fix it before trusting any result."
        )
    chosen_parameters = signals.parameters(
        chosen.signal, lookback=chosen.lookback, skip=chosen.skip
    )
    return (
        cleaned(signals.compute(chosen.signal, panel.prices, **chosen_parameters), chosen),
        chosen_parameters,
        chosen.signal,
    )


def run_study(panel: Panel, parameters: Parameters | None = None) -> dict[str, Any]:
    """Evaluate the signal in ``parameters`` on ``panel`` and return a results document.

    ``parameters.lag`` is the trade lag a backtest of this signal would use, so the study scores
    what a lagged trade could actually earn.
    """
    chosen = parameters or Parameters()
    lag, delisting = chosen.lag, chosen.delisting_return
    signal, signal_parameters, name = signal_for(panel, chosen)
    spread = summarize(
        quantile_spread(signal, panel.prices, chosen.quantiles, lag, delisting),
        config.PERIODS_PER_YEAR,
    )
    decay = ic_decay(signal, panel.prices, chosen.max_horizon, lag, delisting)
    by_quantile = quantile_returns(signal, panel.prices, chosen.quantiles, lag, delisting)
    quantile_means = {
        f"quantile_{number}_return": _annual_mean(by_quantile[:, number - 1])
        for number in range(1, chosen.quantiles + 1)
    }
    return {
        "kind": "study",
        "title": f"{name} ({signals.describe(signal_parameters)}) signal study",
        "period": [panel.dates[0].isoformat(), panel.dates[-1].isoformat()],
        "bars": len(panel.dates),
        "assets": len(panel.assets),
        "parameters": {
            "signal": name,
            **signal_parameters,
            "quantiles": chosen.quantiles,
            "lag": lag,
            "max_horizon": chosen.max_horizon,
            "delisting_return": delisting,
            "winsorize": chosen.winsorize,
            "zscore": chosen.zscore,
        },
        "metrics": {
            "delistings": float(bar_returns(panel.prices)[1].sum()),
            **ic_summary(signal, panel.prices, lag=lag, delisting_return=delisting),
            "signal_autocorrelation": signal_autocorrelation(signal),
            **{f"spread_{key}": value for key, value in spread.items()},
            **quantile_means,
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
            "quantiles": (
                f"quantile_k_return: mean one-bar return of signal quantile k (1 lowest, "
                f"{chosen.quantiles} highest) after the trade, times periods per year, gross"
            ),
            "rank_ic_ir": "mean rank IC over its standard deviation, per bar, not annualised",
            "cleaning": _cleaning(chosen),
            "delistings": f"an asset whose prices stop earns {delisting:+.0%} once",
        },
        "limitations": LIMITATIONS,
    }


def _annual_mean(values: FloatArray) -> float:
    finite = values[np.isfinite(values)]
    return float(finite.mean()) * config.PERIODS_PER_YEAR if finite.size else float("nan")


def _cleaning(chosen: Parameters) -> str:
    steps = []
    if chosen.winsorize:
        steps.append(f"winsorized at {chosen.winsorize:.0%} per tail")
    if chosen.zscore:
        steps.append("z-scored")
    return ", then ".join(steps) + " per bar" if steps else "none: the raw scores"
