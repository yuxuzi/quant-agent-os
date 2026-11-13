"""Known answers for `analyze_risk` (B-21): volatility contributions and factor exposures.

The series below are built to be exactly orthogonal and zero-mean over 12 periods, so the
expected numbers follow from the conventions by hand.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from quant_agent_os.testing import check_tool
from quant_analytics import metrics, risk
from quant_analytics.computed import RiskInput, portfolio_risk
from quant_analytics.tools import analytics_tools

MONTHLY = 12
# Orthogonal, zero-mean patterns: sum(a * b) = sum(a * e) = sum(b * e) = 0 over 12 periods.
A = [0.02, -0.02] * 6
B = [0.01, 0.01, -0.01, -0.01] * 3
E = [0.005, -0.005, -0.005, 0.005] * 3


def _variance(series: list[float]) -> float:
    return sum(value * value for value in series) / (len(series) - 1) * MONTHLY  # zero mean


def test_uncorrelated_assets_contribute_in_proportion_to_their_variance() -> None:
    result = risk.volatility_contributions({"a": 0.5, "b": 0.5}, {"a": A, "b": B}, MONTHLY)

    expected = math.sqrt(0.25 * _variance(A) + 0.25 * _variance(B))
    assert result.volatility == pytest.approx(expected)
    assert result.contributions["a"].percent == pytest.approx(0.8)  # 0.0004 / 0.0005
    assert result.contributions["b"].percent == pytest.approx(0.2)
    assert result.contributions["a"].volatility == pytest.approx(math.sqrt(_variance(A)))
    assert result.diversification_ratio == pytest.approx(3 / math.sqrt(5))
    assert (result.gross_exposure, result.net_exposure) == (1.0, 1.0)


def test_a_short_position_in_the_same_asset_is_a_negative_contribution() -> None:
    result = risk.volatility_contributions(
        {"long": 1.0, "short": -0.5}, {"long": A, "short": A}, 12
    )

    assert result.volatility == pytest.approx(0.5 * math.sqrt(_variance(A)))
    assert result.contributions["long"].percent == pytest.approx(2.0)
    assert result.contributions["short"].percent == pytest.approx(-1.0)
    assert (result.gross_exposure, result.net_exposure) == (1.5, 0.5)


def test_euler_contributions_add_up_and_match_the_weighted_series() -> None:
    rng = np.random.default_rng(7)
    returns = {name: list(rng.normal(0.005, 0.04, 60)) for name in "wxyz"}
    weights = {"w": 0.4, "x": 0.3, "y": -0.2, "z": 0.5}

    result = risk.volatility_contributions(weights, returns, MONTHLY)

    weighted = [sum(weights[n] * returns[n][t] for n in weights) for t in range(60)]
    assert result.volatility == pytest.approx(metrics.annualized_volatility(weighted, MONTHLY))
    items = result.contributions.values()
    assert sum(item.component or 0.0 for item in items) == pytest.approx(result.volatility)
    assert sum(item.percent or 0.0 for item in items) == pytest.approx(1.0)
    step = 1e-6
    for name in weights:
        bumped = weights | {name: weights[name] + step}
        slope = (
            risk.volatility_contributions(bumped, returns, MONTHLY).volatility - result.volatility
        ) / step
        assert result.contributions[name].marginal == pytest.approx(slope, rel=1e-4)


def test_factor_exposures_and_variance_shares_are_recovered_exactly() -> None:
    first = [0.001 + 1.2 * m + 0.3 * v + e for m, v, e in zip(A, B, E, strict=True)]
    second = [0.8 * m - 0.5 * v for m, v in zip(A, B, strict=True)]

    result = risk.factor_exposures(
        {"one": 0.6, "two": 0.4}, {"one": first, "two": second}, {"market": A, "value": B}
    )

    assert result.asset_exposures["one"] == pytest.approx({"market": 1.2, "value": 0.3})
    assert result.asset_exposures["two"] == pytest.approx({"market": 0.8, "value": -0.5})
    assert result.exposures == pytest.approx({"market": 1.04, "value": -0.02})
    parts = {
        "market": 1.04**2 * _variance(A),
        "value": 0.02**2 * _variance(B),
        "specific": 0.6**2 * _variance(E),
    }
    total = sum(parts.values())
    assert result.variance_shares == pytest.approx(
        {"market": parts["market"] / total, "value": parts["value"] / total}
    )
    assert result.specific_share == pytest.approx(parts["specific"] / total)
    assert result.r_squared == pytest.approx(1 - parts["specific"] / total)
    assert not result.collinear


def test_a_single_factor_beta_matches_empyrical() -> None:
    rng = np.random.default_rng(11)
    market = list(rng.normal(0.006, 0.04, 48))
    fund = [
        0.002 + 0.9 * m + noise for m, noise in zip(market, rng.normal(0, 0.01, 48), strict=True)
    ]

    result = risk.factor_exposures({"fund": 1.0}, {"fund": fund}, {"market": market})

    beta = metrics.benchmark_metrics(fund, market, MONTHLY)["beta"]
    assert result.exposures["market"] == pytest.approx(beta)


def test_shares_sum_to_one_on_random_data() -> None:
    rng = np.random.default_rng(3)
    factors = {name: list(rng.normal(0, 0.03, 40)) for name in ("mkt", "smb", "hml")}
    assets = {name: list(rng.normal(0.004, 0.05, 40)) for name in ("p", "q")}

    result = risk.factor_exposures({"p": 0.7, "q": 0.3}, assets, factors)

    assert result.variance_shares is not None and result.specific_share is not None
    assert sum(result.variance_shares.values()) + result.specific_share == pytest.approx(1.0)


def test_analyze_risk_honours_the_contract_and_states_conventions() -> None:
    record = check_tool(
        analytics_tools().get("analyze_risk"),
        {
            "portfolio_id": "book",
            "weights": {"a": 0.5, "b": 0.5},
            "asset_returns": {"a": A, "b": B},
            "factor_returns": {"market": A},
            "periods_per_year": MONTHLY,
        },
    )

    assert record.output is not None
    output: dict[str, Any] = record.output
    assert output["contributions"]["a"]["percent_contribution"] == pytest.approx(0.8)
    assert output["factors"]["exposures"]["market"] == pytest.approx(0.5)
    assert {"volatility", "contributions", "exposures", "variance_shares"} <= set(
        output["conventions"]
    )
    statements = [item["statement"] for item in output["evidence"]]
    assert all(item["kind"] == "computed" for item in output["evidence"])
    assert "a contributes 80.0% of the volatility of book." in statements
    assert "Exposure of book to market is 0.50." in statements
    assert output["warnings"] == []


def test_without_factors_there_are_no_factor_results_or_conventions() -> None:
    output = portfolio_risk(
        RiskInput(
            portfolio_id="book", weights={"a": 1.0}, asset_returns={"a": A}, periods_per_year=12
        )
    )

    assert output.factors is None
    assert "exposures" not in output.conventions


def test_flat_short_and_collinear_inputs_are_flagged() -> None:
    flat = portfolio_risk(
        RiskInput(
            portfolio_id="cash",
            weights={"c": 1.0},
            asset_returns={"c": [0.001] * 4},
            periods_per_year=12,
        )
    )
    collinear = portfolio_risk(
        RiskInput(
            portfolio_id="book",
            weights={"a": 1.0},
            asset_returns={"a": B},
            factor_returns={"market": A, "double": [2 * value for value in A]},
            periods_per_year=12,
        )
    )

    assert flat.contributions["c"].percent_contribution is None
    assert flat.diversification_ratio is None
    assert any("unreliable" in warning for warning in flat.warnings)
    assert any("does not vary" in warning for warning in flat.warnings)
    assert any("linearly dependent" in warning for warning in collinear.warnings)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"weights": {"a": 1.0, "b": 0.0}, "asset_returns": {"a": A}}, "no returns for b"),
        ({"weights": {"a": 1.0}, "asset_returns": {"a": A, "b": B}}, "no weight for b"),
        ({"weights": {"a": 1.0, "b": 0.0}, "asset_returns": {"a": A, "b": B[:5]}}, "same number"),
        ({"weights": {"a": 1.0}, "asset_returns": {"a": [0.01, float("nan")]}}, "finite"),
        ({"weights": {"a": float("inf")}, "asset_returns": {"a": A}}, "weights must be finite"),
        (
            {"weights": {"a": 1.0}, "asset_returns": {"a": A}, "factor_returns": {"m": A[:6]}},
            "same periods",
        ),
        (
            {"weights": {"a": 1.0}, "asset_returns": {"a": A[:2]}, "factor_returns": {"m": A[:2]}},
            "1 factors need more than 2 periods",
        ),
    ],
)
def test_bad_inputs_are_refused_with_the_reason(fields: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        RiskInput(portfolio_id="x", periods_per_year=12, **fields)


def test_an_exact_hedge_has_no_volatility_rather_than_rounding_noise() -> None:
    # 0.3 of a and -3 of a tenth of a cancel exactly, except for floating-point rounding.
    tenth = [0.1 * value for value in A]
    weights, returns = {"a": 0.3, "b": -3.0}, {"a": A, "b": tenth}

    breakdown = risk.volatility_contributions(weights, returns, 12)
    factors = risk.factor_exposures(weights, returns, {"market": B})

    assert breakdown.volatility == 0.0
    assert breakdown.diversification_ratio is None
    assert all(item.percent is None for item in breakdown.contributions.values())
    assert factors.variance_shares is None and factors.r_squared is None
