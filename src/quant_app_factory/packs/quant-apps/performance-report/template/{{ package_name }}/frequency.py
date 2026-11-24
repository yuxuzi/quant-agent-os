"""How many periods a year the returns hold: inferred from the dates, or as the settings say.

Every annualised figure multiplies by this count, so a wrong one is a wrong number. ``auto``, the
default, reads the median spacing of the dates and names the nearest frequency. A number that is
set must agree with that spacing to within a factor of 1.5, or the report refuses to run.
"""

from __future__ import annotations

import datetime as dt
import itertools
import math
import statistics
from dataclasses import dataclass

from quant_toolbox.data import TableError

AUTO = "auto"
TOLERANCE = 1.5  # a set number may imply a spacing this many times the dates', and no more
DAYS_PER_YEAR = 365.25
NAMES = {252: "daily", 52: "weekly", 12: "monthly", 4: "quarterly", 1: "annual"}


@dataclass(frozen=True)
class Periods:
    """The periods a year that annualised figures use, and how they were chosen."""

    count: int
    name: str  # daily, weekly, monthly, quarterly, or annual; empty for any other number
    how: str  # "inferred from the dates" or "set in config.toml"

    def statement(self) -> str:
        """The periods, as a report states them, such as "12 periods a year (monthly, ...)"."""
        named = f"{self.name}, " if self.name else ""
        return f"{self.count} periods a year ({named}{self.how})"


def median_spacing(dates: list[dt.date]) -> float:
    """The median number of days between consecutive dates, which must be sorted."""
    gaps = [(later - earlier).days for earlier, later in itertools.pairwise(dates)]
    return statistics.median(gaps)


def _agrees(spacing: float, period_days: float) -> bool:
    """Whether two spacings differ by no more than the tolerance, either way."""
    low, high = sorted((spacing, period_days))
    return high <= low * TOLERANCE


def nearest(spacing: float) -> int | None:
    """The named periods a year whose spacing agrees with ``spacing``, nearest first, or None."""
    best = min(NAMES, key=lambda count: abs(math.log(DAYS_PER_YEAR / count / spacing)))
    return best if _agrees(spacing, DAYS_PER_YEAR / best) else None


def inferred_name(dates: list[dt.date]) -> str:
    """The frequency's name as the dates show it, or "" when they show none; never refuses."""
    if len(dates) < 2:
        return ""
    spacing = median_spacing(dates)
    count = nearest(spacing) if spacing > 0 else None
    return NAMES[count] if count else ""


def resolve(dates: list[dt.date], setting: int | str) -> Periods:
    """The periods a year for ``dates``: inferred when ``setting`` is auto, else checked.

    Raises TableError when the dates show no frequency, or when a set number disagrees with them
    by more than the tolerance; the message says what the dates look like.
    """
    if len(dates) < 2:
        if setting == AUTO:
            raise TableError(
                "The returns have fewer than two dates, so their frequency cannot be inferred. "
                "Set periods_per_year in config.toml."
            )
        return Periods(int(setting), NAMES.get(int(setting), ""), "set in config.toml")
    spacing = median_spacing(dates)
    if spacing <= 0:
        raise TableError("The return dates do not increase; sort them before reporting.")
    if setting == AUTO:
        count = nearest(spacing)
        if count is None:
            raise TableError(
                f"The dates are a median {spacing:g} days apart, which matches no frequency "
                "(daily, weekly, monthly, quarterly, or annual). Set periods_per_year in "
                "config.toml to the periods a year."
            )
        return Periods(count, NAMES.get(count, ""), "inferred from the dates")
    number = int(setting)
    if _agrees(spacing, DAYS_PER_YEAR / number):
        return Periods(number, NAMES.get(number, ""), "set in config.toml")
    fit = nearest(spacing)
    likely = (
        f"Set periods_per_year = {fit} ({NAMES[fit]}), or auto to infer it."
        if fit
        else "Set periods_per_year to the periods a year, or auto to infer it."
    )
    raise TableError(
        f"periods_per_year is {number}, about {DAYS_PER_YEAR / number:.3g} days a period, but the "
        f"dates are a median {spacing:g} days apart. {likely}"
    )
