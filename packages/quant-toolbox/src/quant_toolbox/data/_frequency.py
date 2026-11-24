"""How many periods a year a return series holds: inferred from its dates, or checked.

Every annualised figure multiplies by the periods a year, so a wrong count is a wrong number. The
median spacing of the dates names the nearest frequency; a stated count must agree with that
spacing to within a factor of `TOLERANCE`, or the caller is refused.
"""

from __future__ import annotations

import datetime as dt
import itertools
import math
import statistics
from dataclasses import dataclass

from quant_toolbox.data._tables import TableError

TOLERANCE = 1.5  # a stated count may imply a spacing this many times the dates', and no more
DAYS_PER_YEAR = 365.25
NAMES = {252: "daily", 52: "weekly", 12: "monthly", 4: "quarterly", 1: "annual"}
STALE_PERIODS = 2  # a last date more than this many periods before the as-of date is stale


@dataclass(frozen=True)
class Frequency:
    """The periods a year of a series: ``name`` is empty for a count that has no name."""

    name: str
    periods_per_year: int


def median_spacing(dates: list[dt.date]) -> float:
    """The median number of days between consecutive dates, which must be sorted."""
    gaps = [(later - earlier).days for earlier, later in itertools.pairwise(dates)]
    return statistics.median(gaps)


def _agrees(spacing: float, period_days: float) -> bool:
    """Whether two spacings differ by no more than the tolerance, either way."""
    low, high = sorted((spacing, period_days))
    return high <= low * TOLERANCE


def _nearest(spacing: float) -> int | None:
    """The named periods a year whose spacing agrees with ``spacing``, nearest first, or None."""
    best = min(NAMES, key=lambda count: abs(math.log(DAYS_PER_YEAR / count / spacing)))
    return best if _agrees(spacing, DAYS_PER_YEAR / best) else None


def infer_frequency(dates: list[dt.date]) -> Frequency:
    """The frequency the dates show: daily, weekly, monthly, quarterly, or annual.

    Raises TableError when there are fewer than two dates, when they do not increase, or when
    their median spacing matches none of those frequencies.
    """
    if len(dates) < 2:
        raise TableError(
            "The returns have fewer than two dates, so their frequency cannot be inferred. "
            "Set periods_per_year in config.toml."
        )
    spacing = median_spacing(dates)
    if spacing <= 0:
        raise TableError("The return dates do not increase; sort them before reporting.")
    count = _nearest(spacing)
    if count is None:
        raise TableError(
            f"The dates are a median {spacing:g} days apart, which matches no frequency "
            "(daily, weekly, monthly, quarterly, or annual). Set periods_per_year in "
            "config.toml to the periods a year."
        )
    return Frequency(NAMES[count], count)


def check_frequency(dates: list[dt.date], periods_per_year: int) -> Frequency:
    """The frequency for a stated ``periods_per_year``, once it is checked against the dates.

    Raises TableError when the dates are fewer than two, do not increase, or are spaced by more
    than `TOLERANCE` from what the stated count implies; the message names the likely count.
    """
    if len(dates) < 2:
        return Frequency(NAMES.get(periods_per_year, ""), periods_per_year)
    spacing = median_spacing(dates)
    if spacing <= 0:
        raise TableError("The return dates do not increase; sort them before reporting.")
    if _agrees(spacing, DAYS_PER_YEAR / periods_per_year):
        return Frequency(NAMES.get(periods_per_year, ""), periods_per_year)
    fit = _nearest(spacing)
    likely = (
        f"Set periods_per_year = {fit} ({NAMES[fit]}), or auto to infer it."
        if fit
        else "Set periods_per_year to the periods a year, or auto to infer it."
    )
    raise TableError(
        f"periods_per_year is {periods_per_year}, about "
        f"{DAYS_PER_YEAR / periods_per_year:.3g} days a period, but the dates are a median "
        f"{spacing:g} days apart. {likely}"
    )


def periods_behind(last: dt.date, as_of: dt.date, frequency: Frequency) -> float:
    """How many of the series' periods lie after ``last``, up to ``as_of``.

    Daily data counts weekdays, so that a Friday's data is current on the Monday; other data counts
    calendar days in periods of the average length.
    """
    if as_of <= last:
        return 0.0
    if frequency.name == "daily":
        return float(
            sum(
                (last + dt.timedelta(days=day)).weekday() < 5
                for day in range(1, (as_of - last).days + 1)
            )
        )
    return (as_of - last).days / (DAYS_PER_YEAR / frequency.periods_per_year)


def period_noun(periods_per_year: int) -> str:
    """The name of one period, as a report says it: "quarterly", or "3-month" for another count."""
    return NAMES.get(periods_per_year) or f"{12 / periods_per_year:g}-month"
