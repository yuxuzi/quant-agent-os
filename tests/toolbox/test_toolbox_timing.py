"""One timing convention: the study and the backtest agree on what a signal can earn."""

from quant_toolbox.data import random_walk_panel
from quant_toolbox.performance import sharpe_ratio
from quant_toolbox.research import dollar_neutral_weights, ic_summary, run_backtest


def test_study_and_backtest_agree_at_each_lag() -> None:
    # An edge that lasts one bar: tradable only at the signal's own close.
    prices, signal = random_walk_panel(signal_strength=0.3, delay=0)
    weights = dollar_neutral_weights(signal)

    for lag, edge in ((0, True), (1, False)):
        ic = ic_summary(signal, prices, lag=lag)["mean_rank_ic"]
        gross = run_backtest(prices, weights, cost_bps=0.0, lag=lag, rebalance_every=1).gross
        sharpe = sharpe_ratio(gross, 252)
        if edge:
            assert ic > 0.1 and sharpe > 10
        else:
            assert abs(ic) < 0.03 and abs(sharpe) < 1.5
