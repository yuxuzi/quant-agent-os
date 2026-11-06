"""Generated `metrics.py` must agree with `quant_analytics.metrics` where conventions match.

A generated project owns a numpy copy of its metrics (decision 0009), and the platform computes
through empyrical (decision 0010), so nothing else stops them drifting apart. The two differ on
purpose in the ways `docs/products/quant-app-factory.md` lists: an undefined metric is NaN in one
and None in the other, and only the generated module drops missing values itself.
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

from quant_analytics import metrics as analytics
from quant_app_factory.domain.planner import BUILT_IN_PACKS

RETURNS = np.random.default_rng(7).normal(0.0005, 0.01, 504)
BENCHMARK = 0.8 * RETURNS + np.random.default_rng(8).normal(0.0002, 0.006, 504)
EXACT = 1e-12


@pytest.fixture(scope="module")
def generated() -> ModuleType:
    """The pack's metrics module, loaded from its file as a generated project would have it."""
    template = Path(BUILT_IN_PACKS["quant-baseline"]) / "template"
    path = next(template.glob("*/metrics.py"))
    spec = importlib.util.spec_from_file_location("generated_metrics", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("periods", [252, 52, 12])
@pytest.mark.parametrize("name", ["annualized_return", "annualized_volatility", "sharpe_ratio"])
def test_annualised_metrics_agree(generated: ModuleType, name: str, periods: int) -> None:
    ours = getattr(generated, name)(RETURNS, periods)
    theirs = getattr(analytics, name)(list(RETURNS), periods)

    assert ours == pytest.approx(theirs, abs=EXACT)


def test_sharpe_agrees_on_the_risk_free_conversion(generated: ModuleType) -> None:
    assert generated.sharpe_ratio(RETURNS, 252, 0.03) == pytest.approx(
        analytics.sharpe_ratio(list(RETURNS), 252, 0.03), abs=EXACT
    )


@pytest.mark.parametrize("name", ["max_drawdown", "hit_rate"])
def test_unannualised_metrics_agree(generated: ModuleType, name: str) -> None:
    ours = getattr(generated, name)(RETURNS)

    assert ours == pytest.approx(getattr(analytics, name)(list(RETURNS)), abs=EXACT)


def test_benchmark_metrics_agree(generated: ModuleType) -> None:
    theirs = analytics.benchmark_metrics(list(RETURNS), list(BENCHMARK), 252)
    ours = generated.active_metrics(RETURNS, BENCHMARK, 252)

    assert generated.beta(RETURNS, BENCHMARK) == pytest.approx(theirs["beta"], abs=EXACT)
    for name in ("active_return", "tracking_error", "information_ratio"):
        assert ours[name] == pytest.approx(theirs[name], abs=EXACT)


def test_missing_values_agree_once_the_platform_has_dropped_them(generated: ModuleType) -> None:
    gappy = RETURNS.copy()
    gappy[[3, 50, 200]] = np.nan
    kept = list(gappy[np.isfinite(gappy)])  # what the analytics tools pass to their metrics

    for name in ("annualized_return", "annualized_volatility", "sharpe_ratio"):
        ours = getattr(generated, name)(gappy, 252)
        assert ours == pytest.approx(getattr(analytics, name)(kept, 252), abs=EXACT)


@pytest.mark.parametrize(
    "returns",
    [
        [0.01] * 50,  # a constant series, whose deviation is floating-point noise
        [0.1] * 7,
        [0.0] * 50,
        list(RETURNS[:1]),  # too short to have a deviation
    ],
)
def test_a_sharpe_undefined_in_one_is_undefined_in_the_other(
    generated: ModuleType, returns: list[float]
) -> None:
    assert math.isnan(generated.sharpe_ratio(np.array(returns), 252))
    assert analytics.sharpe_ratio(returns, 252) is None


@pytest.mark.parametrize("level", [0.001, 0.01, 0.0])
def test_a_constant_market_leaves_beta_undefined_in_both(
    generated: ModuleType, level: float
) -> None:
    market = np.full(RETURNS.size, level)

    assert math.isnan(generated.beta(RETURNS, market))
    assert analytics.benchmark_metrics(list(RETURNS), list(market), 252)["beta"] is None


def test_a_constant_gap_leaves_the_information_ratio_undefined_in_both(
    generated: ModuleType,
) -> None:
    shifted = RETURNS + 0.001

    assert math.isnan(generated.active_metrics(RETURNS, shifted, 252)["information_ratio"])
    theirs = analytics.benchmark_metrics(list(RETURNS), list(shifted), 252)
    assert theirs["information_ratio"] is None
