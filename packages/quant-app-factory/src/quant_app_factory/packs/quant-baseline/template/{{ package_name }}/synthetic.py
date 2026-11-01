"""Synthetic price panels for demos and tests, optionally with a planted predictive signal."""

from __future__ import annotations

import numpy as np

from .metrics import FloatArray


def generate_panel(
    n_bars: int = 750,
    n_assets: int = 30,
    *,
    seed: int = 7,
    signal_strength: float = 0.0,
    volatility: float = 0.01,
) -> tuple[FloatArray, FloatArray]:
    """Return ``(prices, signal)``, both bars by assets.

    ``signal[t]`` is known at the close of bar t and, when ``signal_strength`` is above zero,
    predicts the return of bar t + 1. With zero strength, prices are a pure random walk.
    """
    generator = np.random.default_rng(seed)
    signal = generator.standard_normal((n_bars, n_assets))
    noise = generator.standard_normal((n_bars, n_assets)) * volatility
    returns = np.zeros((n_bars, n_assets))
    returns[1:] = signal_strength * volatility * signal[:-1] + noise[1:]
    prices = 100.0 * np.cumprod(1.0 + returns, axis=0)
    return prices, signal


def dates(n_bars: int) -> list[str]:
    """Return ``n_bars`` labels for synthetic bars (not calendar dates)."""
    return [f"bar-{index:05d}" for index in range(n_bars)]
