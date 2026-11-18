"""Standard signals. Each uses only prices up to and including its own bar, so it never looks ahead.

12-1 momentum (Jegadeesh and Titman, 1993) is the return over the past year skipping the latest
month; short-term reversal (Jegadeesh, 1990) is minus the return over the last month. Lookbacks
are in bars, so a project scales them to its frequency.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from quant_toolbox._types import FloatArray


def momentum(prices: FloatArray, lookback: int, skip: int) -> FloatArray:
    """Return from bar t - ``lookback`` to bar t - ``skip`` (12-1 momentum by default)."""
    if not 0 <= skip < lookback:
        raise ValueError(f"momentum needs 0 <= skip < lookback; got {skip} and {lookback}.")
    signal = np.full(prices.shape, np.nan)
    if prices.shape[0] > lookback:
        recent = prices[lookback - skip : prices.shape[0] - skip]
        signal[lookback:] = recent / prices[:-lookback] - 1.0
    return signal


def reversal(prices: FloatArray, lookback: int) -> FloatArray:
    """Minus the return over the last ``lookback`` bars (recent losers score high)."""
    if lookback < 1:
        raise ValueError(f"reversal needs a lookback of at least 1 bar, got {lookback}.")
    signal = np.full(prices.shape, np.nan)
    if prices.shape[0] > lookback:
        signal[lookback:] = -(prices[lookback:] / prices[:-lookback] - 1.0)
    return signal


def look_ahead_bars(signal: Callable[[FloatArray], FloatArray], prices: FloatArray) -> list[int]:
    """Return bars where ``signal`` changes when later prices are removed: it must be empty.

    A signal at bar t may use prices up to bar t only. This recomputes the signal on prices cut
    off at several bars and compares; a signal that passes can still leak in ways prices cannot
    show, such as restated fundamentals.
    """
    full = signal(prices)
    bad: list[int] = []
    bars = prices.shape[0]
    for cut in sorted({bars // 4, bars // 2, 3 * bars // 4, bars - 1}):
        if cut < 1:
            continue
        partial = signal(prices[: cut + 1])
        same = np.isclose(partial, full[: cut + 1], equal_nan=True, rtol=1e-12, atol=1e-12)
        if not bool(same.all()):
            bad.append(cut)
    return bad
