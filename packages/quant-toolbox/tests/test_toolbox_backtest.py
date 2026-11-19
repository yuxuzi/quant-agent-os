"""Bias tests: no look-ahead, a real trade lag, and costs that only ever cost."""

import numpy as np
import pytest

from quant_toolbox.data import random_walk_panel
from quant_toolbox.performance import sharpe_ratio, simple_returns
from quant_toolbox.research import (
    TRADE_LAG,
    dollar_neutral_weights,
    momentum,
    reversal,
    run_backtest,
    walk_forward_splits,
)


def _net(prices: np.ndarray, lag: int = 1, cost_bps: float = 5.0) -> np.ndarray:
    weights = dollar_neutral_weights(momentum(prices, 10, 1))
    return run_backtest(prices, weights, cost_bps=cost_bps, lag=lag).net


@pytest.mark.parametrize("lag", [0, 1, 2])
def test_future_prices_never_change_past_returns(lag: int) -> None:
    prices, _ = random_walk_panel(n_bars=200, n_assets=10)
    changed = prices.copy()
    changed[150:] *= np.linspace(0.5, 1.5, 10)

    np.testing.assert_array_equal(_net(prices, lag)[:150], _net(changed, lag)[:150])


def test_a_same_bar_signal_earns_nothing_but_would_if_trades_were_not_lagged() -> None:
    prices, _ = random_walk_panel(n_bars=1000, n_assets=40)
    current_return = np.full(prices.shape, np.nan)
    current_return[1:] = simple_returns(prices)
    weights = dollar_neutral_weights(current_return)

    honest = run_backtest(prices, weights, cost_bps=0.0, lag=0).gross
    asset_returns = np.zeros(prices.shape)
    asset_returns[1:] = simple_returns(prices)
    leaked = np.nansum(weights * asset_returns, axis=1)  # same-bar: what a bug would earn

    assert abs(sharpe_ratio(honest, 252)) < 1.5
    assert sharpe_ratio(leaked, 252) > 20


def test_the_trade_lag_delays_the_first_position() -> None:
    prices, _ = random_walk_panel(n_bars=40, n_assets=6)
    weights = dollar_neutral_weights(momentum(prices, 10, 1))

    held_now = run_backtest(prices, weights, cost_bps=0.0, lag=0).held
    held_later = run_backtest(prices, weights, cost_bps=0.0, lag=1).held

    first = int(np.argmax(np.abs(held_now).sum(axis=1) > 0))
    assert np.abs(held_later[first]).sum() == 0
    np.testing.assert_allclose(held_later[first + 1], held_now[first])


def test_a_next_bar_signal_is_only_worth_something_without_a_trade_lag() -> None:
    # The same planted world as the study's timing test: the two must agree.
    prices, signal = random_walk_panel(n_bars=750, n_assets=30, signal_strength=0.3, delay=0)
    weights = dollar_neutral_weights(signal)

    at_close = run_backtest(prices, weights, cost_bps=0.0, lag=0, rebalance_every=1).gross
    lagged = run_backtest(prices, weights, cost_bps=0.0, lag=1, rebalance_every=1).gross

    assert sharpe_ratio(at_close, 252) > 10
    assert abs(sharpe_ratio(lagged, 252)) < 1.5


def test_a_signal_that_lasts_past_the_lag_survives_it() -> None:
    prices, signal = random_walk_panel(signal_strength=0.3, delay=TRADE_LAG)

    gross = run_backtest(
        prices, dollar_neutral_weights(signal), cost_bps=0.0, rebalance_every=1
    ).gross

    assert sharpe_ratio(gross, 252) > 10


def test_shifting_the_signal_forward_changes_the_result() -> None:
    prices, signal = random_walk_panel(n_bars=300, n_assets=20, signal_strength=0.3)
    shifted = np.roll(signal, 1, axis=0)

    honest = run_backtest(
        prices, dollar_neutral_weights(signal), cost_bps=0.0, lag=0, rebalance_every=1
    )
    moved = run_backtest(
        prices, dollar_neutral_weights(shifted), cost_bps=0.0, lag=0, rebalance_every=1
    )

    assert not np.allclose(honest.gross, moved.gross)
    assert sharpe_ratio(honest.gross, 252) > sharpe_ratio(moved.gross, 252)


def test_a_held_position_that_delists_loses_once_without_a_trading_cost() -> None:
    nan = np.nan
    prices = np.array([[100.0, 50.0]] * 3 + [[100.0, nan]] * 3)
    weights = np.tile([0.5, 0.5], (6, 1))  # long both from the first close

    result = run_backtest(
        prices, weights, cost_bps=100.0, lag=0, rebalance_every=100, delisting_return=-0.4
    )

    assert result.gross.tolist() == pytest.approx([0.0, 0.0, 0.0, -0.2, 0.0, 0.0])
    assert result.forced_exits.sum() == 1
    np.testing.assert_allclose(result.held[4:, 1], 0.0)
    assert result.turnover[4:].tolist() == [0.0, 0.0]  # the write-off is not a trade


def test_a_position_is_carried_through_a_gap_and_earns_the_move_over_it() -> None:
    nan = np.nan
    prices = np.array([[100.0, 10.0], [100.0, nan], [100.0, nan], [100.0, 12.0]])
    weights = np.array([[0.0, 1.0], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]])

    result = run_backtest(prices, weights, cost_bps=0.0, lag=0, rebalance_every=1)

    # The sell signal at bar 1 cannot trade until a price returns at bar 3.
    assert result.held[:, 1].tolist() == [0.0, 1.0, 1.0, 1.0]
    assert result.gross.tolist() == pytest.approx([0.0, 0.0, 0.0, 0.2])
    assert result.forced_exits.sum() == 0


def test_holdings_drift_and_a_rebalance_trades_the_drift_back() -> None:
    prices = np.array([[100.0, 100.0], [110.0, 100.0], [110.0, 100.0]])
    weights = np.tile([0.5, 0.5], (3, 1))  # constant targets

    daily = run_backtest(prices, weights, cost_bps=0.0, lag=0, rebalance_every=1)
    held_on = run_backtest(prices, weights, cost_bps=0.0, lag=0, rebalance_every=2)

    # After +10% on one asset the book is 0.55 / 1.05 and 0.50 / 1.05; rebalancing sells
    # 0.025 / 1.05 of the winner into the loser.
    assert daily.turnover.tolist() == pytest.approx([0.0, 0.5, 0.025 / 1.05])
    np.testing.assert_allclose(daily.held[2], [0.5, 0.5])
    assert held_on.turnover.tolist() == pytest.approx([0.0, 0.5, 0.0])
    np.testing.assert_allclose(held_on.held[2], [0.55 / 1.05, 0.5 / 1.05])


def test_trades_happen_only_on_the_rebalance_schedule() -> None:
    prices, _ = random_walk_panel(n_bars=200, n_assets=10)
    weights = dollar_neutral_weights(momentum(prices, 20, 1))

    turnover = run_backtest(prices, weights, cost_bps=0.0, lag=1, rebalance_every=21).turnover

    trading_bars = np.nonzero(turnover > 0.0)[0]
    assert len(trading_bars) > 3
    assert set(np.diff(trading_bars)) == {21}


def test_rebalancing_monthly_trades_far_less_than_daily() -> None:
    prices, _ = random_walk_panel(n_bars=500, n_assets=20)
    weights = dollar_neutral_weights(momentum(prices, 20, 1))

    daily = run_backtest(prices, weights, cost_bps=0.0, rebalance_every=1).turnover.sum()
    monthly = run_backtest(prices, weights, cost_bps=0.0, rebalance_every=21).turnover.sum()

    assert monthly < daily / 3


def test_no_edge_on_a_random_walk_across_seeds() -> None:
    # A biased engine (look-ahead, dropped losses) shows an edge where none can exist.
    returns = []
    for seed in range(40):
        prices, _ = random_walk_panel(n_bars=500, n_assets=20, seed=seed)
        weights = dollar_neutral_weights(reversal(prices, 5))
        returns.append(run_backtest(prices, weights, cost_bps=0.0).gross.mean())

    standard_error = np.std(returns, ddof=1) / np.sqrt(len(returns))
    assert abs(np.mean(returns)) < 3 * standard_error


def test_higher_costs_never_raise_returns() -> None:
    prices, _ = random_walk_panel(n_bars=300, n_assets=20)

    totals = [_net(prices, cost_bps=cost).sum() for cost in (0.0, 5.0, 50.0)]

    assert totals[0] > totals[1] > totals[2]


def test_weights_are_dollar_neutral_with_unit_gross() -> None:
    _, signal = random_walk_panel(n_bars=5, n_assets=7)

    weights = dollar_neutral_weights(signal)

    np.testing.assert_allclose(weights.sum(axis=1), 0.0, atol=1e-12)
    np.testing.assert_allclose(np.abs(weights).sum(axis=1), 1.0)


def test_walk_forward_tests_always_follow_their_training_range() -> None:
    splits = walk_forward_splits(100, train=40, test=20)

    assert [(s.start, s.stop, t.start, t.stop) for s, t in splits] == [
        (0, 40, 40, 60),
        (20, 60, 60, 80),
        (40, 80, 80, 100),
    ]


def test_a_mask_of_every_bar_rebalances_as_daily_does() -> None:
    prices, _ = random_walk_panel(n_bars=120, n_assets=6)
    weights = dollar_neutral_weights(momentum(prices, 10, 1))

    daily = run_backtest(prices, weights, cost_bps=5.0, lag=1, rebalance_every=1)
    masked = run_backtest(
        prices, weights, cost_bps=5.0, lag=1, rebalance_on=np.ones(120, dtype=bool)
    )

    np.testing.assert_array_equal(masked.held, daily.held)
    np.testing.assert_array_equal(masked.net, daily.net)


def test_a_mask_marking_every_fifth_bar_rebalances_as_every_fifth_does() -> None:
    prices = np.array(
        [[100.0, 50.0], [102.0, 51.0], [101.0, 53.0], [104.0, 52.0], [103.0, 55.0]] * 4
    )
    weights = np.zeros_like(prices)
    weights[:, 0] = 0.5  # the first non-zero row is row 0
    weights[:, 1] = -0.5
    mask = np.zeros(prices.shape[0], dtype=bool)
    mask[::5] = True  # every 5th bar from the first position

    scheduled = run_backtest(prices, weights, cost_bps=3.0, lag=0, rebalance_every=5)
    marked = run_backtest(prices, weights, cost_bps=3.0, lag=0, rebalance_on=mask)

    np.testing.assert_array_equal(marked.held, scheduled.held)
    np.testing.assert_array_equal(marked.turnover, scheduled.turnover)


def test_a_wrong_length_mask_or_a_mask_with_a_schedule_is_refused() -> None:
    prices, _ = random_walk_panel(n_bars=30, n_assets=4)
    weights = dollar_neutral_weights(momentum(prices, 5, 1))

    with pytest.raises(ValueError, match="one flag per bar"):
        run_backtest(prices, weights, cost_bps=0.0, rebalance_on=np.ones(29, dtype=bool))
    with pytest.raises(ValueError, match="rebalance_on and rebalance_every"):
        run_backtest(
            prices,
            weights,
            cost_bps=0.0,
            rebalance_every=2,
            rebalance_on=np.ones(30, dtype=bool),
        )


def test_weights_that_do_not_match_the_prices_are_refused() -> None:
    prices, _ = random_walk_panel(n_bars=30, n_assets=4)
    weights = dollar_neutral_weights(momentum(prices, 5, 1))

    with pytest.raises(ValueError, match=r"shapes \(30, 3\) and \(30, 4\)"):
        run_backtest(prices[:, :3], weights, cost_bps=0.0)
    with pytest.raises(ValueError, match="one row per bar"):
        run_backtest(prices, weights[:, 0], cost_bps=0.0)


def test_a_missing_weight_is_refused_rather_than_written_off() -> None:
    prices, _ = random_walk_panel(n_bars=30, n_assets=4)
    weights = dollar_neutral_weights(momentum(prices, 5, 1))
    weights[12, 2] = np.nan

    with pytest.raises(ValueError, match="first at bar 12, asset column 2: give 0"):
        run_backtest(prices, weights, cost_bps=0.0)
