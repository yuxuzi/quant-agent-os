"""Purged splits, per-asset costs and cost drag, quantile returns, IC IR, and signal hygiene."""

from __future__ import annotations

import math

import numpy as np
import pytest

from quant_toolbox.data import random_walk_panel
from quant_toolbox.research import (
    cost_drag,
    demean_by_group,
    dollar_neutral_weights,
    ic_summary,
    momentum,
    neutralize,
    purged_kfold_splits,
    quantile_returns,
    quantile_spread,
    reversal,
    run_backtest,
    walk_forward_splits,
    winsorize,
    zscore,
)
from quant_toolbox.testing import assert_no_look_ahead

# Splits


def test_walk_forward_purge_drops_the_end_of_each_training_range() -> None:
    plain = walk_forward_splits(100, 40, 20)
    purged = walk_forward_splits(100, 40, 20, purge=5, embargo=3)

    assert [test for _, test in plain] == [test for _, test in purged]
    for (train, test), (full, _) in zip(purged, plain, strict=True):
        assert train == range(full.start, full.stop - 5)
        assert test.start - train.stop == 5


def test_walk_forward_refuses_a_purge_as_long_as_training() -> None:
    with pytest.raises(ValueError, match="purge"):
        walk_forward_splits(100, 10, 5, purge=10)
    with pytest.raises(ValueError, match="zero or more"):
        walk_forward_splits(100, 10, 5, embargo=-1)


def test_purged_kfold_tests_every_bar_once_and_keeps_a_gap() -> None:
    splits = purged_kfold_splits(103, 5, purge=4, embargo=2)

    tested = np.concatenate([np.arange(test.start, test.stop) for _, test in splits])
    np.testing.assert_array_equal(tested, np.arange(103))
    assert [len(test) for _, test in splits] == [21, 21, 21, 20, 20]
    for train, test in splits:
        assert np.all(np.diff(train) > 0)
        before, after = train[train < test.start], train[train >= test.stop]
        assert before.size == 0 or test.start - before[-1] == 5  # 4 purged bars between
        assert after.size == 0 or after[0] - test.stop == 2  # 2 embargoed bars
        assert len(train) + len(test) + min(4, test.start) + min(2, 103 - test.stop) == 103


def test_purged_kfold_without_purge_is_plain_kfold() -> None:
    for train, test in purged_kfold_splits(10, 2):
        assert sorted([*train.tolist(), *test]) == list(range(10))


@pytest.mark.parametrize("folds", [1, 11])
def test_purged_kfold_refuses_impossible_folds(folds: int) -> None:
    with pytest.raises(ValueError, match="folds"):
        purged_kfold_splits(10, folds)


# Costs


def _book() -> tuple[np.ndarray, np.ndarray]:
    prices, _ = random_walk_panel(300, 8, seed=9)
    return prices, dollar_neutral_weights(momentum(prices, 20, 1))


def test_one_rate_per_asset_equal_to_a_scalar_gives_the_same_backtest() -> None:
    prices, weights = _book()
    scalar = run_backtest(prices, weights, cost_bps=7.5)
    vector = run_backtest(prices, weights, cost_bps=np.full(8, 7.5))

    np.testing.assert_allclose(vector.net, scalar.net, rtol=0.0, atol=1e-15)
    np.testing.assert_array_equal(scalar.net, scalar.gross - scalar.costs)
    np.testing.assert_array_equal(scalar.costs, scalar.turnover * 7.5 / 10_000.0)


def test_per_asset_rates_add_up_to_one_rate_for_all() -> None:
    prices, weights = _book()
    one = run_backtest(prices, weights, cost_bps=20.0)
    each = [run_backtest(prices, weights, cost_bps=20.0 * np.eye(8)[i]) for i in range(8)]

    np.testing.assert_allclose(sum(r.costs for r in each), one.costs, rtol=1e-12, atol=1e-18)
    assert all(np.all(r.costs >= 0.0) for r in each)
    np.testing.assert_array_equal(each[0].gross, one.gross)


def test_rates_must_match_the_assets() -> None:
    prices, weights = _book()
    with pytest.raises(ValueError, match="one rate per asset"):
        run_backtest(prices, weights, cost_bps=np.ones(3))


def test_cost_drag_of_a_known_backtest() -> None:
    prices, weights = _book()
    result = run_backtest(prices, weights, cost_bps=10.0)

    drag = cost_drag(result, 252)

    assert drag.gross_return == pytest.approx(np.mean(result.gross) * 252)
    assert drag.cost_drag == pytest.approx(drag.gross_return - drag.net_return)
    assert drag.annual_turnover == pytest.approx(np.mean(result.turnover) * 252)
    assert drag.cost_per_turnover_bps == pytest.approx(10.0)
    at_break_even = run_backtest(prices, weights, cost_bps=drag.break_even_bps)
    assert np.mean(at_break_even.net) == pytest.approx(0.0, abs=1e-15)


def test_cost_drag_without_trades_has_no_break_even() -> None:
    prices, _ = _book()
    drag = cost_drag(run_backtest(prices, np.zeros(prices.shape), cost_bps=5.0), 252)

    assert drag.cost_drag == 0.0 and math.isnan(drag.break_even_bps)


# Evaluation


def test_quantile_returns_split_a_known_bar() -> None:
    prices = np.vstack([np.ones(10), np.ones(10), 1.0 + np.arange(10) / 100.0])
    signal = np.vstack([np.arange(10.0), np.full(10, np.nan), np.full(10, np.nan)])

    means = quantile_returns(signal, prices, quantiles=5, lag=1)

    np.testing.assert_allclose(means[0], [0.005, 0.025, 0.045, 0.065, 0.085])
    assert np.isnan(means[1:]).all()
    np.testing.assert_allclose(quantile_spread(signal, prices, 5, 1)[0], 0.08)


def test_ic_summary_reports_the_spread_and_ir_of_rank_ics() -> None:
    prices, _ = random_walk_panel(400, 30, seed=2)
    summary = ic_summary(reversal(prices, 5), prices)

    assert summary["rank_ic_std"] > 0.0
    assert summary["rank_ic_ir"] == pytest.approx(summary["mean_rank_ic"] / summary["rank_ic_std"])


# Signal hygiene


def test_zscore_has_zero_mean_and_unit_spread_per_bar() -> None:
    signal = np.array(
        [[1.0, 2.0, 3.0, np.nan], [5.0, 5.0, 5.0, 5.0], [1.0, np.nan, np.nan, np.nan]]
    )

    scores = zscore(signal)

    np.testing.assert_allclose(scores[0, :3], [-math.sqrt(1.5), 0.0, math.sqrt(1.5)])
    assert np.isnan(scores[0, 3]) and np.isnan(scores[1:]).all()


def test_winsorize_clips_to_each_bars_quantiles() -> None:
    signal = np.vstack([np.arange(101.0), np.arange(101.0) * 10])
    signal[0, 50] = np.nan

    clipped = winsorize(signal, 0.05, 0.95)

    assert clipped[1].min() == pytest.approx(50.0) and clipped[1].max() == pytest.approx(950.0)
    assert np.isnan(clipped[0, 50])
    with pytest.raises(ValueError, match="lower < upper"):
        winsorize(signal, 0.9, 0.1)


def test_demean_by_group_centres_each_group() -> None:
    signal = np.array([[1.0, 3.0, 10.0, 20.0, np.nan]])

    result = demean_by_group(signal, ["a", "a", "b", "b", "b"])

    np.testing.assert_allclose(result[0, :4], [-1.0, 1.0, -5.0, 5.0])
    assert np.isnan(result[0, 4])
    with pytest.raises(ValueError, match="one label per asset"):
        demean_by_group(signal, ["a"])


def test_neutralize_removes_the_exposure() -> None:
    rng = np.random.default_rng(0)
    betas = rng.normal(1.0, 0.3, size=(50, 40))
    signal = 2.0 * betas + rng.normal(size=(50, 40))
    signal[3, 7] = np.nan

    residual = neutralize(signal, betas[:, :, None])

    for bar in range(50):
        keep = np.isfinite(residual[bar])
        assert abs(residual[bar, keep].mean()) < 1e-10
        assert abs(residual[bar, keep] @ betas[bar, keep]) < 1e-9
    assert np.isnan(residual[3, 7])
    constant = neutralize(signal, betas[0][:, None])
    assert np.isfinite(constant[0]).sum() == 40
    with pytest.raises(ValueError, match="exposures"):
        neutralize(signal, np.ones((3, 3)))


def test_hygiene_never_looks_ahead() -> None:
    groups = [f"g{index % 3}" for index in range(12)]
    exposures = np.linspace(0.5, 1.5, 12)[:, None]

    assert_no_look_ahead(lambda prices: zscore(winsorize(momentum(prices, 20, 1))))
    assert_no_look_ahead(lambda prices: demean_by_group(reversal(prices, 5), groups))
    assert_no_look_ahead(lambda prices: neutralize(reversal(prices, 5), exposures))
