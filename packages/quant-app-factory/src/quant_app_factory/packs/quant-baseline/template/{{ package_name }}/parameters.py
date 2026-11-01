"""Research parameters: the defaults in `config`, then ``config.toml``, then command-line flags.

``config.toml`` is where a project's parameters live. Every key is optional, and unknown keys
or wrong types are refused with a message that names them, so a typo never goes unnoticed.
"""

from __future__ import annotations

import dataclasses
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config


class ParameterError(ValueError):
    """Raised when parameters are invalid; the message says which one and why."""


@dataclass(frozen=True)
class Parameters:
    """Everything a study or backtest needs besides the prices."""

    signal: str = "momentum"
    lookback: int | None = None  # None: the signal's standard lookback
    skip: int | None = None  # momentum only; None: the standard skip
    lag: int = config.TRADE_LAG
    quantiles: int = 5
    max_horizon: int = config.MAX_HORIZON
    cost_bps: float = config.COST_BPS
    rebalance_every: int = config.REBALANCE_EVERY
    delisting_return: float = config.DELISTING_RETURN

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


_FIELDS = {field.name: field for field in dataclasses.fields(Parameters)}


def load_parameters(path: Path) -> Parameters:
    """Read ``path`` (TOML, flat keys named like `Parameters` fields) over the defaults."""
    try:
        data = tomllib.loads(path.read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ParameterError(f"Cannot read {path}: {error}") from error
    unknown = sorted(set(data) - set(_FIELDS))
    if unknown:
        known = ", ".join(_FIELDS)
        raise ParameterError(f"{path}: unknown keys {', '.join(unknown)}; use {known}.")
    for key, value in data.items():
        if not _type_ok(_FIELDS[key].type, value):
            raise ParameterError(f"{path}: {key} must be {_FIELDS[key].type}, not {value!r}.")
    return Parameters(**data)


def dump_parameters(parameters: Parameters) -> str:
    """``parameters`` as TOML that `load_parameters` reads back unchanged."""
    lines = ["# The parameters this run used. Re-run it with --config pointing here."]
    for name, value in dataclasses.asdict(parameters).items():
        if value is not None:
            lines.append(f"{name} = {json.dumps(value)}")
    return "\n".join(lines) + "\n"


def _type_ok(annotation: object, value: object) -> bool:
    text = str(annotation)
    if isinstance(value, bool):
        return False
    if text.startswith("str"):
        return isinstance(value, str)
    if text.startswith("int"):
        return isinstance(value, int)
    return isinstance(value, int | float)  # float fields accept whole numbers
