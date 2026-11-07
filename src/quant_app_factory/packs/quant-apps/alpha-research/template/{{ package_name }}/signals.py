"""Signals: each uses only prices up to and including its own bar, so it never looks ahead.

The defaults are the standard academic definitions, scaled to the data's frequency in
`config`: 12-1 momentum (the return over the past year, skipping the latest month) and
one-month short-term reversal. A firm's own signals, in ``firm/signals.py``, sit beside them.
"""

from __future__ import annotations

import numpy as np

from . import config
from .firm.signals import SIGNALS as FIRM_SIGNALS
from .metrics import FloatArray

PARAMETERS = {
    "momentum": {"lookback": config.MOMENTUM_LOOKBACK, "skip": config.MOMENTUM_SKIP},
    "reversal": {"lookback": config.REVERSAL_LOOKBACK},
}


def momentum(
    prices: FloatArray, lookback: int = config.MOMENTUM_LOOKBACK, skip: int = config.MOMENTUM_SKIP
) -> FloatArray:
    """Return from bar t - ``lookback`` to bar t - ``skip`` (12-1 momentum by default)."""
    if not 0 <= skip < lookback:
        raise ValueError(f"momentum needs 0 <= skip < lookback; got {skip} and {lookback}.")
    signal = np.full(prices.shape, np.nan)
    if prices.shape[0] > lookback:
        recent = prices[lookback - skip : prices.shape[0] - skip]
        signal[lookback:] = recent / prices[:-lookback] - 1.0
    return signal


def reversal(prices: FloatArray, lookback: int = config.REVERSAL_LOOKBACK) -> FloatArray:
    """Minus the return over the last ``lookback`` bars (recent losers score high)."""
    if lookback < 1:
        raise ValueError(f"reversal needs a lookback of at least 1 bar, got {lookback}.")
    signal = np.full(prices.shape, np.nan)
    if prices.shape[0] > lookback:
        signal[lookback:] = -(prices[lookback:] / prices[:-lookback] - 1.0)
    return signal


def parameters(name: str, **overrides: int | None) -> dict[str, int]:
    """The parameters ``name`` will use: its defaults, updated by any non-None ``overrides``."""
    if name in FIRM_SIGNALS:
        given = sorted(key for key, value in overrides.items() if value is not None)
        if given:
            raise ValueError(f"{name} takes no {', '.join(given)}; it is your firm's own signal.")
        return {}
    if name not in PARAMETERS:
        known = ", ".join(sorted({*PARAMETERS, *FIRM_SIGNALS}))
        raise ValueError(f"Unknown signal {name!r}; choose one of {known}.")
    chosen = {key: value for key, value in overrides.items() if value is not None}
    unknown = sorted(set(chosen) - set(PARAMETERS[name]))
    if unknown:
        raise ValueError(f"{name} does not take {', '.join(unknown)}.")
    return PARAMETERS[name] | chosen


def compute(name: str, prices: FloatArray, **overrides: int | None) -> FloatArray:
    """Compute the signal called ``name`` with `parameters` (``lookback``, ``skip``)."""
    chosen = parameters(name, **overrides)
    if name in FIRM_SIGNALS:
        return FIRM_SIGNALS[name](prices)
    if name == "momentum":
        return momentum(prices, chosen["lookback"], chosen["skip"])
    return reversal(prices, chosen["lookback"])


def describe(chosen: dict[str, int]) -> str:
    """Parameters as text for a title, such as ``lookback 252, skip 21``."""
    return ", ".join(f"{key} {value}" for key, value in chosen.items()) or "firm signal"


def look_ahead_bars(name: str, prices: FloatArray) -> list[int]:
    """Return bars where ``name`` changes when later prices are removed: it must be empty.

    A signal at bar t may use prices up to bar t only. This recomputes the signal on prices cut
    off at several bars and compares; a signal that passes can still leak in ways prices cannot
    show, such as restated fundamentals.
    """
    full = compute(name, prices)
    bad: list[int] = []
    bars = prices.shape[0]
    for cut in sorted({bars // 4, bars // 2, 3 * bars // 4, bars - 1}):
        if cut < 1:
            continue
        partial = compute(name, prices[: cut + 1])
        same = np.isclose(partial, full[: cut + 1], equal_nan=True, rtol=1e-12, atol=1e-12)
        if not bool(same.all()):
            bad.append(cut)
    return bad
