"""Research parameters: the defaults in `config`, then ``config.toml``, then command-line flags.

``config.toml`` is where a project's parameters live. Every key is optional, and unknown keys
or wrong types are refused with a message that names them, so a typo never goes unnoticed.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quant_toolbox.data import dump_config, load_config

from . import config

EXPOSURES = ("dollar-neutral", "beta-neutral", "long-only")


class ParameterError(ValueError):
    """Raised when parameters are invalid; the message says which one and why."""


@dataclass(frozen=True)
class Parameters:
    """Everything a study or backtest needs besides the prices."""

    data: str | None = None  # "demo", "database", or a price file; flags override it
    signal: str = "momentum"
    lookback: int | None = None  # None: the signal's standard lookback
    skip: int | None = None  # momentum only; None: the standard skip
    lag: int = config.TRADE_LAG
    quantiles: int = 5
    max_horizon: int = config.MAX_HORIZON
    cost_bps: float = config.COST_BPS
    rebalance_every: int = config.REBALANCE_EVERY
    delisting_return: float = config.DELISTING_RETURN
    exposure: str = "dollar-neutral"  # dollar-neutral, beta-neutral, or long-only
    beta_window: int = config.PERIODS_PER_YEAR  # bars of history for each rolling beta
    # Walk-forward: rolling training and test windows, and the lookbacks to choose from.
    objective: str = "sharpe"  # sharpe (net, training window) or ic (mean rank IC)
    train_bars: int = config.PERIODS_PER_YEAR
    test_bars: int = 3 * config.BARS_PER_MONTH
    lookbacks: tuple[int, ...] | None = None  # None: 0.5, 0.75, and 1 times the standard

    def __post_init__(self) -> None:
        problems = [
            message
            for failed, message in (
                (self.lookback is not None and self.lookback < 1, "lookback must be at least 1"),
                (self.skip is not None and self.skip < 0, "skip must be zero or more"),
                (self.lag < 0, "lag must be zero or more bars"),
                (self.quantiles < 2, "quantiles must be at least 2"),
                (self.max_horizon < 1, "max_horizon must be at least 1 bar"),
                (self.cost_bps < 0, "cost_bps must be zero or more"),
                (self.rebalance_every < 1, "rebalance_every must be at least 1 bar"),
                (self.delisting_return < -1, "delisting_return cannot lose more than -1 (100%)"),
                (
                    self.exposure not in EXPOSURES,
                    "exposure must be dollar-neutral, beta-neutral, or long-only",
                ),
                (self.beta_window < 3, "beta_window must be at least 3 bars"),
                (self.objective not in {"sharpe", "ic"}, "objective must be sharpe or ic"),
                (self.train_bars < 2, "train_bars must be at least 2"),
                (self.test_bars < 1, "test_bars must be at least 1"),
                (
                    self.lookbacks is not None and (not self.lookbacks or min(self.lookbacks) < 1),
                    "lookbacks must list whole numbers of at least 1",
                ),
            )
            if failed
        ]
        if problems:
            raise ParameterError("; ".join(problems) + ".")

    def updated(self, **changes: Any) -> Parameters:
        """A copy with every change that is not None (for command-line overrides)."""
        return dataclasses.replace(
            self, **{key: value for key, value in changes.items() if value is not None}
        )


def load_parameters(path: Path) -> Parameters:
    """Read ``path`` (TOML, flat keys named like `Parameters` fields) over the defaults."""
    return load_config(path, Parameters, ParameterError)


def dump_parameters(parameters: Parameters) -> str:
    """``parameters`` as TOML that `load_parameters` reads back unchanged."""
    return dump_config(
        parameters, "The parameters this run used. Re-run it with --config pointing here."
    )
