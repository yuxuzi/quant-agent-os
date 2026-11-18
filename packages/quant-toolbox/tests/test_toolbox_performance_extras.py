"""Known answers for capture ratios, rolling metrics, distributions, VaR, attribution, analyze."""

from __future__ import annotations

import datetime as dt
import json
import math

import numpy as np
import pytest

from quant_toolbox._optional import installed
from quant_toolbox.data import business_days, demo_returns
from quant_toolbox.performance import (
    Analysis,
    analyze,
    annualized_return,
    annualized_volatility,
    brinson,
    capture_ratios,
    drawdowns,
    excess_kurtosis,
    parametric_expected_shortfall,
    parametric_var,
    return_distribution,
    rolling_beta,
    rolling_correlation,
    rolling_sharpe,
    rolling_volatility,
    sharpe_ratio,
    skewness,
)

Z_05 = -1.6448536269514722  # the normal 5% quantile


def _noisy(count: int = 300, seed: int = 4) -> tuple[np.ndarray, np.ndarray]:
    generator = np.random.default_rng(seed)
    market = generator.normal(0.0004, 0.01, count)
    fund = 0.5 * market + generator.normal(0.0002, 0.006, count)
    fund[[40, 41, 200]] = np.nan
    return fund, market


# Capture ratios


def test_capture_ratios_known_answer() -> None:
    fund = [0.02, -0.01, 0.03, -0.02]
    index = [0.01, -0.02, 0.02, -0.01]

    result = capture_ratios(fund, index)

    assert list(result) == ["up_capture", "down_capture", "up_beta", "down_beta", "correlation"]
    assert result["up_capture"] == pytest.approx(0.025 / 0.015)
    assert result["down_capture"] == pytest.approx(1.0)
    assert result["up_beta"] == pytest.approx(1.0)
    assert result["down_beta"] == pytest.approx(-1.0)
    assert result["correlation"] == pytest.approx(np.corrcoef(fund, index)[0, 1])


def test_capture_ratios_are_nan_for_a_side_with_no_bars() -> None:
    result = capture_ratios([0.01, 0.02, 0.03], [0.01, 0.02, 0.01])

    assert math.isnan(result["down_capture"]) and math.isnan(result["down_beta"])
    assert result["up_capture"] == pytest.approx(0.02 / (0.04 / 3))


def test_capture_ratios_need_one_value_per_bar() -> None:
    with pytest.raises(ValueError, match="one value per bar"):
        capture_ratios([0.01, 0.02], [0.01])


# Rolling metrics


def _naive(function, values: np.ndarray, window: int) -> np.ndarray:  # type: ignore[no-untyped-def]
    result = np.full(values.shape, np.nan)
    for end in range(window - 1, values.size):
        frame = values[end - window + 1 : end + 1]
        if np.isfinite(frame).all():
            result[end] = function(frame)
    return result


def test_rolling_volatility_and_sharpe_equal_the_full_metrics_on_each_window() -> None:
    fund, _ = _noisy()

    volatility = rolling_volatility(fund, 20, 252)
    sharpe = rolling_sharpe(fund, 20, 252, risk_free=0.02)

    np.testing.assert_allclose(
        volatility, _naive(lambda f: annualized_volatility(f, 252), fund, 20), rtol=1e-12
    )
    np.testing.assert_allclose(
        sharpe, _naive(lambda f: sharpe_ratio(f, 252, 0.02), fund, 20), rtol=1e-10
    )
    assert np.isnan(volatility[:19]).all()
    assert np.isnan(volatility[40:61]).all() and np.isfinite(volatility[61])


def test_rolling_beta_and_correlation_equal_a_loop() -> None:
    fund, market = _noisy()

    beta = rolling_beta(fund, market, 30)
    correlation = rolling_correlation(fund, market, 30)

    pairs = np.column_stack([fund, market])
    expected_beta = np.full(fund.shape, np.nan)
    expected_correlation = np.full(fund.shape, np.nan)
    for end in range(29, fund.size):
        frame = pairs[end - 29 : end + 1]
        if np.isfinite(frame).all():
            expected_beta[end] = np.polyfit(frame[:, 1], frame[:, 0], 1)[0]
            expected_correlation[end] = np.corrcoef(frame[:, 0], frame[:, 1])[0, 1]
    np.testing.assert_allclose(beta, expected_beta, rtol=1e-9)
    np.testing.assert_allclose(correlation, expected_correlation, rtol=1e-9)


def test_rolling_ratios_of_a_constant_window_are_nan_and_its_volatility_zero() -> None:
    flat = [0.01] * 10

    assert np.isnan(rolling_sharpe(flat, 5, 252)[4:]).all()
    assert np.isnan(rolling_beta(flat, flat, 5)[4:]).all()
    assert np.isnan(rolling_correlation(flat, flat, 5)[4:]).all()
    np.testing.assert_allclose(rolling_volatility(flat, 5, 252)[4:], 0.0, atol=1e-15)


def test_rolling_windows_shorter_than_the_series_or_two_bars() -> None:
    assert np.isnan(rolling_volatility([0.01, 0.02], 5, 252)).all()
    with pytest.raises(ValueError, match="at least 2 bars"):
        rolling_volatility([0.01, 0.02, 0.03], 1, 252)
    with pytest.raises(ValueError, match="one value per bar"):
        rolling_beta([0.01, 0.02, 0.03], [0.01, 0.02], 2)


# Distribution


def test_return_distribution_known_answer() -> None:
    result = return_distribution([0.01, -0.02, 0.03, 0.0, float("nan")])

    assert result.observations == 4
    assert result.best == 0.03 and result.worst == -0.02
    assert result.average_gain == pytest.approx(0.02)
    assert result.average_loss == pytest.approx(-0.02)
    assert result.gain_loss_ratio == pytest.approx(1.0)
    assert result.profit_factor == pytest.approx(2.0)
    assert result.positive_share == 0.5


def test_skewness_and_kurtosis_by_formula() -> None:
    values = np.array([0.01, -0.03, 0.02, 0.05, -0.01, 0.0, 0.04])
    n, d = values.size, values - values.mean()
    m2, m3, m4 = (np.mean(d**k) for k in (2, 3, 4))

    assert skewness(values) == pytest.approx(math.sqrt(n * (n - 1)) / (n - 2) * m3 / m2**1.5)
    g2 = m4 / m2**2 - 3.0
    assert excess_kurtosis(values) == pytest.approx(
        ((n + 1) * g2 + 6) * (n - 1) / ((n - 2) * (n - 3))
    )


@pytest.mark.skipif(not installed("scipy"), reason="scipy is not installed")
def test_skewness_and_kurtosis_match_scipy() -> None:
    from scipy import stats

    fund, _ = _noisy()
    clean = fund[np.isfinite(fund)]

    assert skewness(fund) == pytest.approx(stats.skew(clean, bias=False), rel=1e-12)
    assert excess_kurtosis(fund) == pytest.approx(stats.kurtosis(clean, bias=False), rel=1e-12)


def test_distribution_of_nothing_or_a_constant_is_nan() -> None:
    empty = return_distribution([])
    flat = return_distribution([0.01] * 5)

    assert empty.observations == 0 and math.isnan(empty.best) and math.isnan(empty.skewness)
    assert math.isnan(flat.skewness) and math.isnan(flat.excess_kurtosis)
    assert math.isnan(flat.profit_factor)  # no losses
    assert math.isnan(skewness([0.01, 0.02])) and math.isnan(excess_kurtosis([0.01, 0.0, 0.02]))


# Parametric value at risk


def test_gaussian_var_and_expected_shortfall_known_answer() -> None:
    values = [0.01, -0.01]
    deviation = math.sqrt(2.0) * 0.01
    density = math.exp(-(Z_05**2) / 2) / math.sqrt(2 * math.pi)

    assert parametric_var(values) == pytest.approx(Z_05 * deviation, rel=1e-12)
    assert parametric_expected_shortfall(values) == pytest.approx(
        -deviation * density / 0.05, rel=1e-12
    )


def test_cornish_fisher_var_adjusts_the_quantile_for_skew_and_kurtosis() -> None:
    fund, _ = _noisy()
    clean = fund[np.isfinite(fund)]
    s, k, z = skewness(clean), excess_kurtosis(clean), Z_05
    adjusted = z + (z**2 - 1) * s / 6 + (z**3 - 3 * z) * k / 24 - (2 * z**3 - 5 * z) * s**2 / 36

    expected = clean.mean() + adjusted * clean.std(ddof=1)

    assert parametric_var(fund, method="cornish_fisher") == pytest.approx(expected, rel=1e-12)


def test_cornish_fisher_is_deeper_for_negative_skew() -> None:
    generator = np.random.default_rng(1)
    crashes = np.where(generator.random(2000) < 0.03, -0.08, generator.normal(0.002, 0.01, 2000))

    assert parametric_var(crashes, 0.01, method="cornish_fisher") < parametric_var(crashes, 0.01)


def test_parametric_var_refuses_unknown_methods_and_cutoffs() -> None:
    with pytest.raises(ValueError, match="gaussian or cornish_fisher"):
        parametric_var([0.01, -0.01], method="historical")
    with pytest.raises(ValueError, match="between 0 and 1"):
        parametric_var([0.01, -0.01], 1.5)
    assert math.isnan(parametric_var([0.01]))
    assert math.isnan(parametric_var([0.01, 0.0, 0.02], method="cornish_fisher"))


# Drawdown episodes


def test_drawdown_length_and_recovery_in_bars() -> None:
    days = business_days(5)
    (episode,) = drawdowns(days, np.array([0.1, -0.5, 0.0, 1.0, 0.1]))

    assert episode.peak == days[0] and episode.trough == days[1] and episode.recovered == days[3]
    assert episode.length == 3 and episode.recovery_bars == 2


def test_an_unrecovered_drawdown_runs_to_the_last_bar() -> None:
    days = business_days(3)
    (episode,) = drawdowns(days, np.array([0.1, -0.5, 0.1]))

    assert episode.recovered is None
    assert episode.length == 2 and episode.recovery_bars is None


# Brinson attribution


def test_brinson_fachler_known_answer_and_identity() -> None:
    groups = ["Tech", "Energy", "Cash"]
    wp = {"Tech": 0.6, "Energy": 0.3, "Cash": 0.1}
    wb = {"Tech": 0.5, "Energy": 0.5}
    rp = {"Tech": 0.10, "Energy": -0.02, "Cash": 0.001}
    rb = {"Tech": 0.08, "Energy": 0.01}

    result = brinson(groups, wp, rp, wb, rb)

    benchmark = 0.5 * 0.08 + 0.5 * 0.01
    assert result.benchmark_return == pytest.approx(benchmark)
    assert result.portfolio_return == pytest.approx(0.06 - 0.006 + 0.0001)
    tech = result.groups["Tech"]
    assert tech.allocation == pytest.approx(0.1 * (0.08 - benchmark))
    assert tech.selection == pytest.approx(0.5 * 0.02)
    assert tech.interaction == pytest.approx(0.1 * 0.02)
    assert result.groups["Cash"].allocation == pytest.approx(0.1 * (0.0 - benchmark))
    total = sum(group.total for group in result.groups.values())
    assert abs(total - result.active_return) < 1e-12
    assert abs(result.allocation + result.selection + result.interaction - total) < 1e-12


def test_brinson_refuses_unmatched_weights_and_unknown_groups() -> None:
    with pytest.raises(ValueError, match="add cash as a group"):
        brinson(["A", "B"], {"A": 0.9}, {"A": 0.01}, {"A": 0.5, "B": 0.5}, {"A": 0.0})
    with pytest.raises(ValueError, match="not in groups: C"):
        brinson(["A"], {"A": 1.0}, {"C": 0.01}, {"A": 1.0}, {"A": 0.0})


# analyze


def test_analyze_gathers_every_measure_and_is_json_safe() -> None:
    table = demo_returns(800)
    fund, index = table.column("Equity L/S"), table.column("Index")

    result = analyze(table.dates, fund, 252, benchmark=index)

    assert isinstance(result, Analysis)
    assert result.as_of == table.dates[-1] and result.observations == 800
    assert result.metrics["sharpe_ratio"] == pytest.approx(sharpe_ratio(fund, 252))
    assert result.benchmark is not None and set(capture_ratios(fund, index)) <= set(
        result.benchmark
    )
    assert set(result.horizons) == {"MTD", "QTD", "YTD", "1Y", "3Y", "5Y", "ITD"}
    assert result.horizons["5Y"] is None  # 800 bars do not cover five years
    assert 0.0 <= result.sharpe_inference["probabilistic_sharpe_ratio"] <= 1.0
    assert "up_capture" in result.conventions and result.backend in {"numpy", "empyrical"}
    payload = json.dumps(result.to_dict(), allow_nan=False)
    assert '"as_of": "' in payload
    assert result.notes == []


def test_analyze_notes_short_and_percent_like_data() -> None:
    days = [dt.date(2024, 1, d) for d in range(1, 6)]

    result = analyze(days, [1.0, -0.5, 3.0, 0.5, 2.0], 252)

    assert any("Only 5 observations" in note for note in result.notes)
    assert any("look like percentages" in note for note in result.notes)
    assert result.benchmark is None


def test_a_loss_beyond_everything_has_no_annual_rate_on_either_backend() -> None:
    assert math.isnan(annualized_return([-2.0, 0.1], 252))


def test_analyze_needs_one_date_per_return() -> None:
    with pytest.raises(ValueError, match="one date per return"):
        analyze([dt.date(2024, 1, 1)], [0.01, 0.02], 252)
    with pytest.raises(ValueError, match="one return per date"):
        analyze(business_days(3), [0.01, 0.02, 0.0], 252, benchmark=[0.01])
