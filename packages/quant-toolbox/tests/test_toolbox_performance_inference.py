"""Known answers for the Sharpe ratio's standard error, PSR, DSR, and minimum track record."""

from __future__ import annotations

import math

import numpy as np
import pytest

from quant_toolbox._optional import installed
from quant_toolbox.performance import (
    deflated_sharpe_ratio,
    expected_maximum_sharpe,
    minimum_track_record,
    probabilistic_sharpe_ratio,
    sharpe_ratio,
    sharpe_standard_error,
)
from quant_toolbox.performance._inference import (
    _probabilistic,
    _spread,
    normal_cdf,
    normal_ppf,
)


def _returns(count: int = 1000, seed: int = 2) -> np.ndarray:
    return np.random.default_rng(seed).normal(0.0006, 0.01, count)


def test_the_deflated_sharpe_ratio_example_of_bailey_and_lopez_de_prado_2014() -> None:
    # Annual Sharpe 2.5 over 1250 daily bars, skew -3, kurtosis 10, best of 100 trials whose
    # annual Sharpe ratios have variance 0.5: the paper's expected maximum is 0.1132 per bar,
    # and its deflated Sharpe ratio 0.9004.
    sharpe = 2.5 / math.sqrt(250)
    threshold = expected_maximum_sharpe(100, 0.5 / 250)

    deflated = _probabilistic(sharpe, 1250, _spread(sharpe, -3.0, 10.0), threshold)

    assert threshold == pytest.approx(0.1132, abs=5e-5)
    assert deflated == pytest.approx(0.9004, abs=5e-5)


def test_the_normal_quantile_inverts_the_distribution_function() -> None:
    for p in (1e-12, 1e-6, 0.001, 0.02425, 0.05, 0.3, 0.5, 0.9, 0.97575, 0.999, 1 - 1e-9):
        assert normal_cdf(normal_ppf(p)) == pytest.approx(p, rel=1e-12)
    assert normal_ppf(0.5) == 0.0
    assert normal_ppf(0.0) == -math.inf and normal_ppf(1.0) == math.inf
    with pytest.raises(ValueError, match="between 0 and 1"):
        normal_ppf(1.5)


@pytest.mark.skipif(not installed("scipy"), reason="scipy is not installed")
def test_the_normal_functions_match_scipy() -> None:
    from scipy.stats import norm

    for p in np.concatenate([np.logspace(-15, -1, 15), np.linspace(0.05, 0.95, 19)]):
        assert normal_ppf(float(p)) == pytest.approx(norm.ppf(p), rel=1e-12, abs=1e-14)
        assert normal_ppf(float(1 - p)) == pytest.approx(norm.ppf(1 - p), rel=1e-12, abs=1e-14)
    for x in np.linspace(-8, 8, 33):
        assert normal_cdf(float(x)) == pytest.approx(norm.cdf(x), rel=1e-12)


def test_psr_against_zero_is_the_annual_sharpe_over_its_standard_error() -> None:
    returns = _returns()
    annual = sharpe_ratio(returns, 252)

    psr = probabilistic_sharpe_ratio(returns, 252)

    assert psr == pytest.approx(normal_cdf(annual / sharpe_standard_error(returns, 252)))


def test_standard_error_of_normal_returns_is_close_to_the_textbook_value() -> None:
    returns = _returns(20_000)
    per_bar = float(np.mean(returns) / np.std(returns, ddof=1))
    textbook = math.sqrt((1 + per_bar**2 / 2) / (returns.size - 1)) * math.sqrt(252)

    assert sharpe_standard_error(returns, 252) == pytest.approx(textbook, rel=0.02)


def test_psr_falls_as_the_benchmark_rises_and_stays_a_probability() -> None:
    returns = _returns()
    values = [probabilistic_sharpe_ratio(returns, 252, b) for b in (-5.0, 0.0, 1.0, 50.0)]

    assert values == sorted(values, reverse=True)
    assert all(0.0 <= value <= 1.0 for value in values)
    assert values[0] == pytest.approx(1.0) and values[-1] == pytest.approx(0.0)


def test_deflating_for_many_trials_lowers_the_probability() -> None:
    returns = _returns()

    psr = probabilistic_sharpe_ratio(returns, 252)
    few = deflated_sharpe_ratio(returns, 252, trials=2, trial_sharpe_variance=0.5)
    many = deflated_sharpe_ratio(returns, 252, trials=1000, trial_sharpe_variance=0.5)

    assert psr > few > many


def test_at_the_minimum_track_record_the_psr_reaches_the_confidence() -> None:
    returns = _returns()
    values = returns - returns.mean() + 0.0008
    per_bar = float(np.mean(values) / np.std(values, ddof=1))
    d = values - values.mean()
    m2 = np.mean(d**2)
    spread = _spread(per_bar, np.mean(d**3) / m2**1.5, np.mean(d**4) / m2**2)

    bars = minimum_track_record(values, 252, benchmark_sharpe=0.5, confidence=0.9)

    benchmark = 0.5 / math.sqrt(252)
    z = (per_bar - benchmark) * math.sqrt(bars - 1) / math.sqrt(spread)
    assert normal_cdf(z) == pytest.approx(0.9, rel=1e-12)


def test_undefined_inference_is_nan_and_bad_arguments_are_refused() -> None:
    assert math.isnan(probabilistic_sharpe_ratio([0.01, 0.02], 252))
    assert math.isnan(sharpe_standard_error([0.01] * 10, 252))
    assert math.isnan(minimum_track_record(-_returns(), 252))  # a negative Sharpe never beats 0
    with pytest.raises(ValueError, match="at least 2"):
        deflated_sharpe_ratio(_returns(), 252, trials=1, trial_sharpe_variance=0.5)
    with pytest.raises(ValueError, match="cannot be negative"):
        deflated_sharpe_ratio(_returns(), 252, trials=5, trial_sharpe_variance=-1.0)
    with pytest.raises(ValueError, match="confidence"):
        minimum_track_record(_returns(), 252, confidence=1.0)
