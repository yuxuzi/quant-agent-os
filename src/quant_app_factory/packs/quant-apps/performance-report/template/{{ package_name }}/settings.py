"""The application's settings, from ``config.toml``. Unknown keys are refused, so a typo shows."""

from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .horizons import is_horizon


class SettingsError(ValueError):
    """Raised when settings are invalid; the message says which and why."""


@dataclass(frozen=True)
class Tearsheet:
    """One audience's report: its title and the sections it shows, in order."""

    sections: list[str]
    title: str = ""


@dataclass(frozen=True)
class ExcelSource:
    """Where the table starts in a workbook; the defaults read the first sheet from row 1."""

    sheet: str = ""
    header_row: int = 1


DEFAULT_TEARSHEETS = {
    "pm": Tearsheet(
        title="Portfolio manager review",
        sections=["horizons", "risk", "equity", "underwater", "monthly", "drawdowns"],
    ),
}


@dataclass(frozen=True)
class Settings:
    """Everything a report needs besides the returns."""

    returns: str = "demo"  # a file relative to config.toml, or "demo"
    benchmark: str = ""  # a column of the returns file to compare against, not shown as a book
    basis: str = "not stated"  # what the returns are, on every report: net, gross, or your words
    returns_unit: str = "fraction"  # fraction (0.01 is 1%) or percent (1.0 is 1%)
    periods_per_year: int = 252
    risk_free: float = 0.0  # annual, in the same unit as a fraction
    var_cutoff: float = 0.05
    top_drawdowns: int = 5
    firm_name: str = ""
    horizons: list[str] = field(
        default_factory=lambda: ["MTD", "QTD", "YTD", "1Y", "3Y", "5Y", "ITD"]
    )
    default_tearsheet: str = "pm"
    tearsheets: dict[str, Tearsheet] = field(default_factory=lambda: dict(DEFAULT_TEARSHEETS))
    excel: ExcelSource = field(default_factory=ExcelSource)

    def __post_init__(self) -> None:
        problems = [
            message
            for failed, message in (
                (
                    self.returns_unit not in {"fraction", "percent"},
                    "returns_unit must be fraction or percent",
                ),
                (self.periods_per_year < 1, "periods_per_year must be at least 1"),
                (not 0.0 < self.var_cutoff < 0.5, "var_cutoff must be between 0 and 0.5"),
                (self.top_drawdowns < 1, "top_drawdowns must be at least 1"),
                (not self.horizons, "horizons must name at least one horizon"),
                (
                    self.default_tearsheet not in self.tearsheets,
                    f"default_tearsheet {self.default_tearsheet!r} is not in tearsheets",
                ),
            )
            if failed
        ]
        bad = [label for label in self.horizons if not is_horizon(label)]
        if bad:
            problems.append(
                f"horizons has {', '.join(bad)}; use MTD, QTD, YTD, ITD, or lengths such as 3M, 5Y"
            )
        if problems:
            raise SettingsError("; ".join(problems) + ".")


_FLAT = {
    "returns": str,
    "benchmark": str,
    "basis": str,
    "returns_unit": str,
    "periods_per_year": int,
    "risk_free": float,
    "var_cutoff": float,
    "top_drawdowns": int,
    "firm_name": str,
    "default_tearsheet": str,
}


def load_settings(path: Path) -> Settings:
    """Read ``path``, or return the defaults when it does not exist."""
    if not path.is_file():
        return Settings()
    try:
        data = tomllib.loads(path.read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise SettingsError(f"Cannot read {path}: {error}") from error
    known = {*_FLAT, "horizons", "tearsheets", "excel"}
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
    if "horizons" in data:
        chosen["horizons"] = _strings(data["horizons"], f"{path}: horizons")
    if "tearsheets" in data:
        chosen["tearsheets"] = {
            name: _tearsheet(path, name, entry) for name, entry in data["tearsheets"].items()
        }
    if "excel" in data:
        chosen["excel"] = _excel(path, data["excel"])
    return dataclasses.replace(Settings(), **chosen)


def _tearsheet(path: Path, name: str, entry: Any) -> Tearsheet:
    where = f"{path}: tearsheets.{name}"
    if not isinstance(entry, dict) or set(entry) - {"title", "sections"}:
        raise SettingsError(f"{where} takes only title and sections.")
    if "sections" not in entry:
        raise SettingsError(f"{where} needs sections.")
    title = entry.get("title", "")
    if not isinstance(title, str):
        raise SettingsError(f"{where}: title must be text.")
    return Tearsheet(sections=_strings(entry["sections"], f"{where}: sections"), title=title)


def _excel(path: Path, entry: Any) -> ExcelSource:
    if not isinstance(entry, dict) or set(entry) - {"sheet", "header_row"}:
        raise SettingsError(f"{path}: excel takes only sheet and header_row.")
    sheet, header = entry.get("sheet", ""), entry.get("header_row", 1)
    if not isinstance(sheet, str) or not isinstance(header, int) or header < 1:
        raise SettingsError(f"{path}: excel needs a text sheet and a header_row of 1 or more.")
    return ExcelSource(sheet=sheet, header_row=header)


def _strings(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SettingsError(f"{where} must be a list of text.")
    return value
