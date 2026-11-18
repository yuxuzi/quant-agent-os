"""Known answers and identities for the risk measures added in 0.6.0."""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pytest

from quant_toolbox._hac import newey_west_covariance
from quant_toolbox.performance import expected_shortfall
from quant_toolbox.risk import (
    LIQUIDITY_BUCKETS,
    NO_ADV,
    SCENARIOS,
    correlation_summary,
    euler_contributions,
    ewma_covariance,
    expected_shortfall_contributions,
    exposures,
    factor_risk,
    historical_stress,
    liquidity,
    scenario_shocks,
    simulated_returns,
    style_regression,
    volatility_contributions,
)


def test_liquidity_known_answers() -> None:
    # 20% of ADV a day: A sells 100 of 1000 a day (10 days); B 50 of 50 (1 day); C has no ADV.
    found = liquidity(
        {"A": 1000.0, "B": -50.0, "C": 150.0}, {"A": 500.0, "B": 250.0, "C": 0.0}, 0.20
    )

    assert found.gross == 1200.0
    assert found.days == {"A": pytest.approx(10.0), "B": pytest.approx(1.0)}
    assert found.no_adv == ["C"]
    assert found.within[1] == pytest.approx((100.0 + 50.0) / 1200.0)
    assert found.within[7] == pytest.approx((700.0 + 50.0) / 1200.0)
    assert found.within[30] == pytest.approx(1050.0 / 1200.0)
    assert found.buckets["1 day or less"] == pytest.approx(150.0 / 1200.0)
    assert found.buckets["2-7 days"] == pytest.approx(600.0 / 1200.0)
    assert found.buckets["8-30 days"] == pytest.approx(300.0 / 1200.0)
    assert found.buckets["more than 365 days"] == pytest.approx(0.0)
    assert found.buckets[NO_ADV] == pytest.approx(150.0 / 1200.0)
    assert sum(found.buckets.values()) == pytest.approx(1.0)
    assert len(found.buckets) == len(LIQUIDITY_BUCKETS) + 2


def test_liquidity_beyond_the_last_bucket_and_an_empty_book() -> None:
    found = liquidity({"A": 1000.0}, {"A": 1.0}, 0.1, buckets=(1, 5))

    assert found.buckets == {
        "1 day or less": pytest.approx(0.0001),
        "2-5 days": pytest.approx(0.0004),
        "more than 5 days": pytest.approx(0.9995),
        NO_ADV: 0.0,
    }
    empty = liquidity({}, {})
    assert empty.gross == 0.0 and sum(empty.buckets.values()) == 0.0


@pytest.mark.parametrize(
    ("participation", "buckets"), [(0.0, (1, 7)), (1.5, (1, 7)), (0.2, (7, 1)), (0.2, ())]
)
def test_liquidity_refuses_bad_arguments(participation: float, buckets: tuple[int, ...]) -> None:
    with pytest.raises(ValueError):
        liquidity({"A": 1.0}, {"A": 1.0}, participation, buckets)


def test_herfindahl_is_the_reciprocal_of_effective_positions() -> None:
    found = exposures(np.array([0.5, -0.3, 0.2, np.nan]))

    assert found.herfindahl == pytest.approx(0.25 + 0.09 + 0.04)
    assert found.effective_positions == pytest.approx(1.0 / found.herfindahl)
    assert exposures(np.zeros(3)).herfindahl == 0.0


def _days(count: int, start: dt.date) -> list[dt.date]:
    return [start + dt.timedelta(days=i) for i in range(count)]


def test_scenario_shocks_compound_inside_the_window_only() -> None:
    dates = _days(6, dt.date(2020, 1, 1))
    returns = np.array(
        [[0.5, 0.0], [0.1, -0.1], [0.1, np.nan], [-0.5, 0.2], [0.0, 0.0], [9.0, 9.0]]
    )

    shocks = scenario_shocks(dates, returns, dt.date(2020, 1, 1), dt.date(2020, 1, 4))

    assert shocks is not None
    np.testing.assert_allclose(shocks, [1.1 * 1.1 * 0.5 - 1.0, 0.9 * 1.2 - 1.0])
    assert scenario_shocks(dates, returns, dt.date(2019, 12, 31), dt.date(2020, 1, 3)) is None
    assert scenario_shocks(dates, returns, dt.date(2020, 1, 2), dt.date(2020, 1, 7)) is None
    with pytest.raises(ValueError):
        scenario_shocks(dates, returns, dt.date(2020, 1, 4), dt.date(2020, 1, 4))


def test_historical_stress_applies_shocks_to_todays_weights() -> None:
    dates = _days(6, dt.date(2020, 1, 1))
    returns = np.full((6, 2), 0.01)
    windows = {
        "in": (dt.date(2020, 1, 2), dt.date(2020, 1, 4)),
        "out": SCENARIOS["Lehman (Sep-Oct 2008)"],
    }

    found = historical_stress(np.array([1.0, -0.5]), dates, returns, windows)

    assert found["in"] == pytest.approx(0.5 * (1.01**2 - 1.0))
    assert found["out"] is None
    assert all(start < end for start, end in SCENARIOS.values())


def test_factor_risk_known_answer_and_identities() -> None:
    weights = {"A": 0.5, "B": 0.3, "C": -0.2}
    loadings = {"A": {"mkt": 1.0, "val": 0.2}, "B": {"mkt": 0.8}, "C": {"mkt": 1.2, "val": -0.5}}
    f_cov = {"mkt": {"mkt": 0.04, "val": 0.002}, "val": {"val": 0.01}}
    specific = {"A": 0.09, "B": 0.04, "C": 0.16}

    found = factor_risk(weights, loadings, f_cov, specific)

    x = np.array([0.5 + 0.24 - 0.24, 0.1 + 0.1])  # exposures: mkt 0.5, val 0.2
    f = np.array([[0.04, 0.002], [0.002, 0.01]])
    factor_var = float(x @ f @ x)
    specific_var = 0.25 * 0.09 + 0.09 * 0.04 + 0.04 * 0.16
    assert found.exposures == {"mkt": pytest.approx(0.5), "val": pytest.approx(0.2)}
    assert found.factor_volatility == pytest.approx(math.sqrt(factor_var), abs=1e-12)
    assert found.specific_volatility == pytest.approx(math.sqrt(specific_var), abs=1e-12)
    assert found.volatility == pytest.approx(math.sqrt(factor_var + specific_var), abs=1e-12)
    assert found.factor_shares is not None and found.specific_share is not None
    assert sum(found.factor_shares.values()) + found.specific_share == pytest.approx(1.0, abs=1e-12)
    assert found.contributions is not None
    assert sum(found.contributions.values()) == pytest.approx(found.volatility, abs=1e-12)


def test_factor_risk_refuses_an_incomplete_model() -> None:
    f_cov = {"mkt": {"mkt": 0.04}}
    with pytest.raises(ValueError, match="B"):
        factor_risk({"A": 1.0, "B": 1.0}, {}, f_cov, {"A": 0.1})
    with pytest.raises(ValueError, match="val"):
        factor_risk({"A": 1.0}, {"A": {"val": 1.0}}, f_cov, {"A": 0.1})
    with pytest.raises(ValueError, match="symmetric"):
        factor_risk(
            {"A": 1.0},
            {},
            {"a": {"a": 1.0, "b": 0.5}, "b": {"a": 0.1, "b": 1.0}},
            {"A": 0.1},
        )
    flat = factor_risk({"A": 0.0}, {}, f_cov, {"A": 0.1})
    assert flat.volatility == 0.0 and flat.contributions is None


def _regression_data(n: int = 400) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    rng = np.random.default_rng(7)
    market, size = rng.normal(0.0004, 0.01, n), rng.normal(0.0, 0.005, n)
    noise = rng.normal(0.0, 0.004, n)
    return 0.0002 + 1.2 * market - 0.5 * size + noise, {"market": market, "size": size}


def test_style_regression_recovers_betas_and_matches_ols() -> None:
    returns, factors = _regression_data()

    found = style_regression(returns, factors, 252)

    design = np.column_stack([np.ones(returns.size), factors["market"], factors["size"]])
    coefficients, *_ = np.linalg.lstsq(design, returns, rcond=None)
    assert found.alpha == pytest.approx(coefficients[0] * 252, abs=1e-12)
    assert found.betas == {
        "market": pytest.approx(coefficients[1], abs=1e-12),
        "size": pytest.approx(coefficients[2], abs=1e-12),
    }
    assert found.betas["market"] == pytest.approx(1.2, abs=0.05)
    assert found.t_stats["market"] > 20
    assert 0.8 < found.r_squared < 1.0 and found.adjusted_r_squared < found.r_squared
    assert found.observations == 400 and not found.collinear
    residuals = returns - design @ coefficients
    assert found.residual_volatility == pytest.approx(
        math.sqrt(residuals @ residuals / 397 * 252), abs=1e-12
    )


def test_hac_covariance_with_no_lags_is_whites() -> None:
    returns, factors = _regression_data(120)
    x = np.column_stack([np.ones(returns.size), factors["market"], factors["size"]])
    coefficients, *_ = np.linalg.lstsq(x, returns, rcond=None)
    e = returns - x @ coefficients

    bread = np.linalg.inv(x.T @ x)
    white = bread @ (x.T @ np.diag(e**2) @ x) @ bread

    np.testing.assert_allclose(newey_west_covariance(x, e, 0), white, rtol=1e-10)
    found = style_regression(returns, factors, 252, lags=0)
    assert found.t_stats["market"] == pytest.approx(
        coefficients[1] / math.sqrt(white[1, 1]), rel=1e-10
    )


def test_hac_covariance_adds_bartlett_weighted_autocovariances() -> None:
    returns, factors = _regression_data(60)
    x = np.column_stack([np.ones(returns.size), factors["market"]])
    e = returns - x @ np.linalg.lstsq(x, returns, rcond=None)[0]
    u = x * e[:, None]
    meat = u.T @ u
    for lag, weight in ((1, 2 / 3), (2, 1 / 3)):
        gamma = sum(np.outer(u[t], u[t - lag]) for t in range(lag, len(u)))
        meat = meat + weight * (gamma + gamma.T)
    bread = np.linalg.inv(x.T @ x)

    np.testing.assert_allclose(newey_west_covariance(x, e, 2), bread @ meat @ bread, rtol=1e-10)


def test_hac_matches_statsmodels_when_installed() -> None:
    sm = pytest.importorskip("statsmodels.api")
    returns, factors = _regression_data(250)
    x = np.column_stack([np.ones(returns.size), factors["market"], factors["size"]])
    fit = sm.OLS(returns, x).fit(cov_type="HAC", cov_kwds={"maxlags": 5, "use_correction": False})
    e = returns - x @ fit.params

    np.testing.assert_allclose(newey_west_covariance(x, e, 5), fit.cov_params(), rtol=1e-8)


def test_style_regression_edge_cases() -> None:
    few = style_regression([0.01, 0.02], {"m": [0.01, 0.03]}, 252)
    assert few.observations == 2 and math.isnan(few.alpha) and math.isnan(few.betas["m"])
    market = np.linspace(-0.01, 0.01, 50)
    collinear = style_regression(market * 2, {"a": market, "b": market * 3}, 252)
    assert collinear.collinear
    gappy = style_regression(
        [0.01, np.nan, 0.02, 0.0, 0.01], {"m": [0.0, 0.1, 0.01, -0.01, 0.02]}, 12
    )
    assert gappy.observations == 4
    with pytest.raises(ValueError):
        style_regression([0.01, 0.02], {"m": [0.01]}, 252)
    with pytest.raises(ValueError):
        style_regression([0.01, 0.02], {}, 252)


def test_correlation_summary() -> None:
    rng = np.random.default_rng(3)
    a = rng.normal(size=300)
    b = a + rng.normal(scale=0.5, size=300)
    c = -a + rng.normal(scale=2.0, size=300)
    b[5] = np.nan

    found = correlation_summary({"a": a, "b": b, "c": c, "flat": np.ones(300)})

    assert found.observations == 299
    keep = np.isfinite(b)
    expected = np.corrcoef(np.vstack([a[keep], b[keep], c[keep]]))
    np.testing.assert_allclose(found.matrix[:3, :3], expected, atol=1e-12)
    assert np.isnan(found.matrix[3]).all()
    assert found.highest is not None and found.highest[:2] == ("a", "b")
    assert found.lowest is not None and found.lowest[:2] == ("a", "c")
    assert found.average == pytest.approx(
        np.mean([expected[0, 1], expected[0, 2], expected[1, 2]]), abs=1e-12
    )
    with pytest.raises(ValueError):
        correlation_summary({"a": a})


def test_expected_shortfall_contributions_sum_to_the_books() -> None:
    rng = np.random.default_rng(11)
    returns = rng.normal(0.0, 0.01, (500, 4))
    returns[3, 2] = np.nan
    weights = np.array([0.4, -0.2, 0.5, 0.3])

    found = expected_shortfall_contributions(weights, returns, 0.05)

    book = expected_shortfall(simulated_returns(weights, returns), 0.05)
    assert float(found.sum()) == pytest.approx(book, abs=1e-15)
    assert found[1] > 0.0 or found[1] < 0.0
    assert np.isnan(expected_shortfall_contributions(weights, np.zeros((0, 4)))).all()


def test_ewma_covariance_known_answer() -> None:
    returns = np.array([[0.01, 0.02], [-0.02, 0.01], [0.03, -0.01]])
    decay = 0.9
    w = np.array([decay**2, decay, 1.0])
    w /= w.sum()
    expected = sum(w[t] * np.outer(returns[t], returns[t]) for t in range(3)) * 252

    np.testing.assert_allclose(ewma_covariance(returns, 252, decay=decay), expected, rtol=1e-12)
    halved = ewma_covariance(returns, 252, halflife=1.0)
    np.testing.assert_allclose(halved, ewma_covariance(returns, 252, decay=0.5), rtol=1e-12)
    for bad in ({"decay": 1.0}, {"halflife": 0.0}):
        with pytest.raises(ValueError):
            ewma_covariance(returns, 252, **bad)  # type: ignore[arg-type]


def test_euler_contributions_match_volatility_contributions() -> None:
    rng = np.random.default_rng(5)
    returns = {name: rng.normal(0.0, 0.01, 200) for name in "abc"}
    weights = {"a": 0.5, "b": -0.2, "c": 0.4}
    matrix = np.column_stack([returns[n] for n in "abc"])

    sample = volatility_contributions(weights, returns, 252)
    direct = euler_contributions(list("abc"), [0.5, -0.2, 0.4], np.cov(matrix, rowvar=False) * 252)

    assert direct == sample
    ewma = euler_contributions(list("abc"), [0.5, -0.2, 0.4], ewma_covariance(matrix, 252))
    components = [c.component for c in ewma.contributions.values()]
    assert sum(c for c in components if c is not None) == pytest.approx(ewma.volatility)
    with pytest.raises(ValueError):
        euler_contributions(["a"], [1.0, 2.0], np.eye(2))
