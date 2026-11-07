"""The application's settings, from ``config.toml``. Unknown keys are refused, so a typo shows."""

from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class SettingsError(ValueError):
    """Raised when settings are invalid; the message says which and why."""


LIMITS = {
    "gross_max": "Gross exposure",
    "net_max": "Net exposure, upper",
    "net_min": "Net exposure, lower",
    "single_name_max": "Largest single position",
    "top_five_max": "Top five positions, gross",
    "group_gross_max": "Largest group, gross",
    "volatility_max": "Annualised volatility",
    "var_max": "Value at risk, as a loss",
    "beta_max": "Size of beta",
    "stress_loss_max": "Worst stress scenario, as a loss",
}
DEFAULT_SECTIONS = [
    "limits",
    "exposures",
    "groups",
    "top_positions",
    "risk",
    "contributors",
    "stress",
    "exposure_history",
]


@dataclass(frozen=True)
class Scenario:
    """A stress: a return for every asset, with returns by group replacing it for their assets."""

    name: str
    market: float = 0.0
    groups: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Settings:
    """Everything a risk report needs besides the positions and prices."""

    positions: str = "demo"  # a table of weights of NAV by date and asset, or "demo"
    prices: str = ""  # a table of prices by date and asset; "" means no return-based risk
    groups: str = ""  # a CSV with the columns asset,group, such as sectors
    market: str = ""  # a column of the prices file to measure beta against, not a position
    periods_per_year: int = 252
    var_cutoff: float = 0.05
    lookback: int = 252  # bars of returns for value at risk, volatility, and contributions
    firm_name: str = ""
    top_positions: int = 10
    sections: list[str] = field(default_factory=lambda: list(DEFAULT_SECTIONS))
    limits: dict[str, float] = field(default_factory=dict)
    scenarios: list[Scenario] = field(default_factory=list)

    def __post_init__(self) -> None:
        problems = [
            message
            for failed, message in (
                (self.periods_per_year < 1, "periods_per_year must be at least 1"),
                (not 0.0 < self.var_cutoff < 0.5, "var_cutoff must be between 0 and 0.5"),
                (self.lookback < 2, "lookback must be at least 2 bars"),
                (self.top_positions < 1, "top_positions must be at least 1"),
            )
            if failed
        ]
        unknown = sorted(set(self.limits) - set(LIMITS))
        if unknown:
            problems.append(f"unknown limits {', '.join(unknown)}; use {', '.join(LIMITS)}")
        if problems:
            raise SettingsError("; ".join(problems) + ".")


_FLAT = {
    "positions": str,
    "prices": str,
    "groups": str,
    "market": str,
    "periods_per_year": int,
    "var_cutoff": float,
    "lookback": int,
    "firm_name": str,
    "top_positions": int,
}


def load_settings(path: Path) -> Settings:
    """Read ``path``, or return the defaults when it does not exist."""
    if not path.is_file():
        return Settings()
    try:
        data = tomllib.loads(path.read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise SettingsError(f"Cannot read {path}: {error}") from error
    known = {*_FLAT, "sections", "limits", "scenarios"}
    unknown = sorted(set(data) - known)
    if unknown:
        raise SettingsError(f"{path}: unknown keys {', '.join(unknown)}; use {', '.join(known)}.")
    chosen: dict[str, Any] = {}
    for key, kind in _FLAT.items():
        if key in data:
            value = data[key]
            valid = isinstance(value, int | float) if kind is float else isinstance(value, kind)
            if not valid or isinstance(value, bool):
                raise SettingsError(f"{path}: {key} must be {kind.__name__}, not {value!r}.")
            chosen[key] = float(value) if kind is float else value
    if "sections" in data:
        sections = data["sections"]
        if not isinstance(sections, list) or not all(isinstance(s, str) for s in sections):
            raise SettingsError(f"{path}: sections must be a list of text.")
        chosen["sections"] = sections
    if "limits" in data:
        chosen["limits"] = {key: _number(path, key, value) for key, value in data["limits"].items()}
    if "scenarios" in data:
        chosen["scenarios"] = [_scenario(path, entry) for entry in data["scenarios"]]
    return dataclasses.replace(Settings(), **chosen)


def _scenario(path: Path, entry: Any) -> Scenario:
    if (
        not isinstance(entry, dict)
        or "name" not in entry
        or set(entry)
        - {
            "name",
            "market",
            "groups",
        }
    ):
        raise SettingsError(f"{path}: a scenario takes a name, a market return, and groups.")
    groups = entry.get("groups", {})
    return Scenario(
        name=str(entry["name"]),
        market=_number(path, "market", entry.get("market", 0.0)),
        groups={str(key): _number(path, str(key), value) for key, value in groups.items()},
    )


def _number(path: Path, key: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SettingsError(f"{path}: {key} must be a number, not {value!r}.")
    return float(value)
