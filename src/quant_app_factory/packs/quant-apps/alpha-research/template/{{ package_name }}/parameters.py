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


class ParameterError(ValueError):
    """Raised when parameters are invalid; the message says which one and why."""


@dataclass(frozen=True)
class Parameters:
    """Everything a study or backtest needs besides the prices."""

    data: str | None = None  # "demo" or a price file; flags override it
    signal: str = "momentum"
    signal_file: str | None = None  # scores from elsewhere, instead of a named signal
    lookback: int | None = None  # None: the signal's standard lookback
    skip: int | None = None  # momentum only; None: the standard skip
    lag: int = config.TRADE_LAG
    quantiles: int = 5
    max_horizon: int = config.MAX_HORIZON
    delisting_return: float = config.DELISTING_RETURN
    # Signal clean-up, per bar and across assets, so it never looks ahead. Off by default.
    winsorize: float = 0.0  # share clipped at each tail, such as 0.01; 0 clips nothing
    zscore: bool = False  # each bar's scores minus their mean, over their standard deviation

    def __post_init__(self) -> None:
        problems = [
            message
            for failed, message in (
                (self.lookback is not None and self.lookback < 1, "lookback must be at least 1"),
                (self.skip is not None and self.skip < 0, "skip must be zero or more"),
                (self.lag < 0, "lag must be zero or more bars"),
                (self.quantiles < 2, "quantiles must be at least 2"),
                (self.max_horizon < 1, "max_horizon must be at least 1 bar"),
                (self.delisting_return < -1, "delisting_return cannot lose more than -1 (100%)"),
                (
                    not 0.0 <= self.winsorize < 0.5,
                    "winsorize must be at least 0 and below 0.5 (a share of each tail)",
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
