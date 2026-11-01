"""Walk-forward evaluation: choose parameters on training data only, report out of sample.

Each fold has a training window and the test window right after it. The fold's lookback is the
grid value with the best training score, computed from prices up to the end of the training
window only (earlier bars serve as signal warm-up). The chosen lookback is then backtested over
the test window. Only test windows are reported, so no reported number influenced its own
parameters.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from . import config, signals
from .backtest import run_backtest, walk_forward_splits, weights_for
from .data import Panel
from .evaluation import forward_returns
from .metrics import FloatArray, information_coefficient, sharpe_ratio, summarize
from .parameters import Parameters

LIMITATIONS = [
    "Each test window is backtested with its fold's lookback from the start of the data, so "
    "the switch between folds costs nothing; real switches trade the difference.",
    "A small grid limits overfitting but does not remove it: report how many grids you tried.",
]


def candidate_lookbacks(parameters: Parameters) -> list[int]:
    """The lookbacks to choose from: ``parameters.lookbacks``, or 0.5, 0.75, and 1 times the
    signal's lookback (its standard one unless ``parameters.lookback`` is set)."""
    if parameters.lookbacks:
        return sorted(set(parameters.lookbacks))
    base = signals.parameters(parameters.signal, lookback=parameters.lookback, skip=parameters.skip)
    floor = base.get("skip", 0) + 1
    return sorted({max(floor, round(base["lookback"] * share)) for share in (0.5, 0.75, 1.0)})


def training_score(prices: FloatArray, train: range, parameters: Parameters) -> float:
    """The objective over ``train``, from ``prices`` that end where the training window ends."""
    signal = signals.compute(
        parameters.signal, prices, lookback=parameters.lookback, skip=parameters.skip
    )
    if parameters.objective == "ic":
        forward = forward_returns(prices, 1, parameters.lag, parameters.delisting_return)
        ic = information_coefficient(signal, forward, rank=True)[train.start : train.stop]
        return float(np.nanmean(ic)) if np.isfinite(ic).any() else math.nan
    net = _net(prices, signal, parameters)[train.start : train.stop]
    return sharpe_ratio(net, config.PERIODS_PER_YEAR)


def run_walk_forward(panel: Panel, parameters: Parameters | None = None) -> dict[str, Any]:
    """Walk ``parameters.signal`` forward over ``panel`` and return a results document."""
    chosen = parameters or Parameters()
    candidates = candidate_lookbacks(chosen)
    warmup = max(candidates) + chosen.lag + 1
    n_bars = panel.prices.shape[0]
    splits = walk_forward_splits(n_bars, chosen.train_bars, chosen.test_bars, start=warmup)
    if not splits:
        raise ValueError(
            f"Walk-forward needs {warmup} warm-up bars, {chosen.train_bars} training bars, and "
            f"{chosen.test_bars} test bars; the data has {n_bars}. Lower train_bars or "
            "test_bars in config.toml."
        )
    out_of_sample = np.full(n_bars, np.nan)
    folds = []
    for train, test in splits:
        history = panel.prices[: train.stop]  # nothing after the training window
        scores = [training_score(history, train, chosen.updated(lookback=lb)) for lb in candidates]
        best = max(range(len(candidates)), key=lambda index: _rankable(scores[index]))
        fold = chosen.updated(lookback=candidates[best])
        through_test = panel.prices[: test.stop]
        signal = signals.compute(fold.signal, through_test, lookback=fold.lookback, skip=fold.skip)
        out_of_sample[test.start : test.stop] = _net(through_test, signal, fold)[test.start :]
        folds.append(
            {
                "train": _span(panel, train),
                "test": _span(panel, test),
                "lookback": candidates[best],
                "training_score": scores[best],
            }
        )
    tested = np.isfinite(out_of_sample)
    periods = config.PERIODS_PER_YEAR
    return {
        "kind": "walk-forward",
        "title": f"{chosen.signal} walk-forward (lookbacks {', '.join(map(str, candidates))})",
        "period": [panel.dates[0].isoformat(), panel.dates[-1].isoformat()],
        "bars": n_bars,
        "assets": len(panel.assets),
        "parameters": {
            "signal": chosen.signal,
            "lookbacks": candidates,
            "objective": chosen.objective,
            "train_bars": chosen.train_bars,
            "test_bars": chosen.test_bars,
            "cost_bps": chosen.cost_bps,
            "lag": chosen.lag,
            "rebalance_every": chosen.rebalance_every,
            "delisting_return": chosen.delisting_return,
            "exposure": chosen.exposure,
        },
        "metrics": {
            **summarize(out_of_sample[tested], periods),
            "out_of_sample_bars": float(tested.sum()),
            "folds": float(len(folds)),
        },
        "folds": folds,
        "series": {
            "dates": [
                day.isoformat() for day, used in zip(panel.dates, tested, strict=True) if used
            ],
            "net_returns": [float(value) for value in out_of_sample[tested]],
        },
        "conventions": {
            "periods_per_year": periods,
            "choice": f"best training {chosen.objective} over the lookback grid, per fold",
            "windows": f"{chosen.train_bars} training bars, then {chosen.test_bars} test bars",
            "reported": "test windows only (out of sample), net of costs",
            "timing": f"signal at close t, traded at close t + {chosen.lag}",
        },
        "limitations": LIMITATIONS,
    }


def _net(prices: FloatArray, signal: FloatArray, parameters: Parameters) -> FloatArray:
    return run_backtest(
        prices,
        weights_for(prices, signal, parameters),
        cost_bps=parameters.cost_bps,
        lag=parameters.lag,
        rebalance_every=parameters.rebalance_every,
        delisting_return=parameters.delisting_return,
    )["net"]


def _span(panel: Panel, window: range) -> list[str]:
    return [panel.dates[window.start].isoformat(), panel.dates[window.stop - 1].isoformat()]


def _rankable(score: float) -> float:
    return score if math.isfinite(score) else -math.inf
