"""The vectorised research functions equal the 0.5.0 loops they replaced, kept as oracles."""

from __future__ import annotations

import _research_oracles as oracle
import numpy as np
import pytest

from quant_toolbox.data import random_walk_panel
from quant_toolbox.research import (
    dollar_neutral_weights,
    forward_returns,
    information_coefficient,
    market_returns,
    momentum,
    quantile_spread,
    rolling_betas,
)

TOLERANCE = 1e-12
# The oracles warn on empty buckets; the vectorised versions answer NaN quietly.
pytestmark = pytest.mark.filterwarnings(
    "ignore:Mean of empty slice", "ignore:invalid value encountered"
)


def _panel(seed: int) -> tuple[np.ndarray, np.ndarray]:
    prices, _ = random_walk_panel(300, 25, seed=seed)
    rng = np.random.default_rng(seed)
    prices[rng.random(prices.shape) < 0.05] = np.nan  # gaps
    prices[200:, 3] = np.nan  # a delisting
    signal = momentum(prices, 20, 2)
    signal[:, 5] = np.round(signal[:, 5], 2)  # ties
    signal[:, 6] = signal[:, 7]  # exact ties
    signal[150, :] = 0.5  # a constant bar
    signal[151, :20] = np.nan  # a bar with too few values
    return prices, signal


def _same(actual: np.ndarray, expected: np.ndarray) -> None:
    np.testing.assert_allclose(actual, expected, rtol=0.0, atol=TOLERANCE, equal_nan=True)


@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("rank", [False, True])
def test_information_coefficient_equals_the_loop(seed: int, rank: bool) -> None:
    prices, signal = _panel(seed)
    forward = forward_returns(prices, 3)

    _same(
        information_coefficient(signal, forward, rank=rank),
        oracle.information_coefficient(signal, forward, rank=rank),
    )


@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("quantiles", [1, 3, 5])
def test_quantile_spread_equals_the_loop(seed: int, quantiles: int) -> None:
    prices, signal = _panel(seed)

    _same(
        quantile_spread(signal, prices, quantiles),
        oracle.quantile_spread(signal, prices, quantiles),
    )


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_weights_and_market_returns_equal_the_loops(seed: int) -> None:
    prices, signal = _panel(seed)

    _same(dollar_neutral_weights(signal), oracle.dollar_neutral_weights(signal))
    _same(market_returns(prices), oracle.market_returns(prices))
    _same(market_returns(prices, 0.0), oracle.market_returns(prices, 0.0))


@pytest.mark.parametrize("window", [1, 2, 20, 299, 300, 400])
def test_rolling_betas_equal_the_loop(window: int) -> None:
    prices, _ = _panel(4)

    _same(rolling_betas(prices, window), oracle.rolling_betas(prices, window))
    flat = np.zeros(prices.shape[0])
    _same(rolling_betas(prices, 10, flat), oracle.rolling_betas(prices, 10, flat))


def test_a_constant_bar_has_no_coefficient() -> None:
    signal = np.tile(np.arange(10.0), (3, 1))
    forward = np.ones((3, 10))
    forward[1] = np.arange(10.0)

    result = information_coefficient(signal, forward)

    assert np.isnan(result[0]) and result[1] == pytest.approx(1.0) and np.isnan(result[2])
