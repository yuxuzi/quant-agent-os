"""Signals: each uses only prices up to and including its own bar, so it never looks ahead.

The defaults are the standard academic definitions, scaled to the data's frequency in
`config`: 12-1 momentum (the return over the past year, skipping the latest month) and
one-month short-term reversal.
"""

from __future__ import annotations

from quant_toolbox import research
from quant_toolbox.data import FloatArray

from . import config

PARAMETERS = {
    "momentum": {"lookback": config.MOMENTUM_LOOKBACK, "skip": config.MOMENTUM_SKIP},
    "reversal": {"lookback": config.REVERSAL_LOOKBACK},
}


def momentum(
    prices: FloatArray, lookback: int = config.MOMENTUM_LOOKBACK, skip: int = config.MOMENTUM_SKIP
) -> FloatArray:
    """Return from bar t - ``lookback`` to bar t - ``skip`` (12-1 momentum by default)."""
    return research.momentum(prices, lookback, skip)


def reversal(prices: FloatArray, lookback: int = config.REVERSAL_LOOKBACK) -> FloatArray:
    """Minus the return over the last ``lookback`` bars (recent losers score high)."""
    return research.reversal(prices, lookback)


def parameters(name: str, **overrides: int | None) -> dict[str, int]:
    """The parameters ``name`` will use: its defaults, updated by any non-None ``overrides``."""
    if name not in PARAMETERS:
        known = ", ".join(sorted(PARAMETERS))
        raise ValueError(f"Unknown signal {name!r}; choose one of {known}.")
    chosen = {key: value for key, value in overrides.items() if value is not None}
    unknown = sorted(set(chosen) - set(PARAMETERS[name]))
    if unknown:
        raise ValueError(f"{name} does not take {', '.join(unknown)}.")
    return PARAMETERS[name] | chosen


def compute(name: str, prices: FloatArray, **overrides: int | None) -> FloatArray:
    """Compute the signal called ``name`` with `parameters` (``lookback``, ``skip``)."""
    chosen = parameters(name, **overrides)
    if name == "momentum":
        return momentum(prices, chosen["lookback"], chosen["skip"])
    return reversal(prices, chosen["lookback"])


def describe(chosen: dict[str, int]) -> str:
    """Parameters as text for a title, such as ``lookback 252, skip 21``."""
    return ", ".join(f"{key} {value}" for key, value in chosen.items())
