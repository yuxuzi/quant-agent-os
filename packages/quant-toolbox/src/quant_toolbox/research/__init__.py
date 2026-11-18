"""Research: signal statistics and evaluation, standard signals, portfolio weights, and backtests.

One timing convention runs through all of it: a signal is known at the close of bar t and traded
``lag`` bars later (`TRADE_LAG`, one bar, by default), so a study's information coefficient
describes what a backtest of the same signal can earn. A delisted asset earns
``delisting_return`` (`DELISTING_RETURN`, -30%) once in both.

- Signals: 12-1 momentum and reversal, and cross-sectional hygiene (`zscore`, `winsorize`,
  `demean_by_group`, `neutralize`), each per bar, so none looks ahead.
- Evaluation: information coefficients and their HAC t-statistics, IC decay and IR, quantile
  returns and spreads, and signal autocorrelation.
- Portfolios and backtests: dollar-neutral, beta-neutral, and long-only weights; the backtest
  engine, with one cost rate or one per asset, and `cost_drag`; walk-forward and purged k-fold
  splits, with a purge and an embargo.
"""

from quant_toolbox._hac import newey_west_lags, newey_west_t_stat
from quant_toolbox.research._backtest import (
    BacktestResult,
    CostDrag,
    cost_drag,
    purged_kfold_splits,
    run_backtest,
    walk_forward_splits,
)
from quant_toolbox.research._evaluation import (
    DELISTING_RETURN,
    TRADE_LAG,
    forward_returns,
    hac_t_stat,
    ic_decay,
    ic_summary,
    quantile_returns,
    quantile_spread,
    signal_autocorrelation,
)
from quant_toolbox.research._signals import look_ahead_bars, momentum, reversal
from quant_toolbox.research._stats import MIN_PAIRS, information_coefficient, ranks
from quant_toolbox.research._transforms import demean_by_group, neutralize, winsorize, zscore
from quant_toolbox.research._weights import (
    beta_neutral_weights,
    dollar_neutral_weights,
    long_only_weights,
    market_returns,
    rolling_betas,
)

__all__ = [
    "DELISTING_RETURN",
    "MIN_PAIRS",
    "TRADE_LAG",
    "BacktestResult",
    "CostDrag",
    "beta_neutral_weights",
    "cost_drag",
    "demean_by_group",
    "dollar_neutral_weights",
    "forward_returns",
    "hac_t_stat",
    "ic_decay",
    "ic_summary",
    "information_coefficient",
    "long_only_weights",
    "look_ahead_bars",
    "market_returns",
    "momentum",
    "neutralize",
    "newey_west_lags",
    "newey_west_t_stat",
    "purged_kfold_splits",
    "quantile_returns",
    "quantile_spread",
    "ranks",
    "reversal",
    "rolling_betas",
    "run_backtest",
    "signal_autocorrelation",
    "walk_forward_splits",
    "winsorize",
    "zscore",
]
