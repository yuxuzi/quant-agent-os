"""Returns by horizon, month, and year, and drawdown episodes, from dated per-bar returns.

Conventions:
- A return dated d covers the time since the previous bar. A horizon is the bars dated after its
  start, up to and including the as-of date, compounded.
- MTD, QTD, and YTD start at the end of the previous month, quarter, and year. ``3M`` and ``1Y``
  start that long before the as-of date; a month-end as-of date starts at a month end.
- Horizons over one year are annualised, geometrically, by their length in years. ``ITD`` runs
  from the first return, annualised by bars once it covers a full year, and cumulative before.
- A horizon the series does not fully cover is None (n/a), never a shorter period shown under
  the longer one's name. ITD is always available.
- Missing returns are left out of compounding; the report's data notes count them.
"""

from __future__ import annotations

import calendar
import datetime as dt
import math
import re
from dataclasses import dataclass

import numpy as np

from .metrics import FloatArray, annualized_return

HORIZON = re.compile(r"^(MTD|QTD|YTD|ITD|[1-9][0-9]?[MY])$")


def is_horizon(label: str) -> bool:
    """Return whether ``label`` names a horizon: MTD, QTD, YTD, ITD, ``3M``, or ``5Y``."""
    return HORIZON.fullmatch(label) is not None


def months_before(day: dt.date, months: int) -> dt.date:
    """Return ``day`` less ``months``; a month-end day gives a month end."""
    index = day.year * 12 + (day.month - 1) - months
    year, month = index // 12, index % 12 + 1
    last = calendar.monthrange(year, month)[1]
    at_month_end = day.day == calendar.monthrange(day.year, day.month)[1]
    return dt.date(year, month, last if at_month_end else min(day.day, last))


def horizon_start(label: str, as_of: dt.date) -> dt.date | None:
    """Return the date a horizon starts after, or None for ITD."""
    if label == "ITD":
        return None
    if label == "MTD":
        return as_of.replace(day=1) - dt.timedelta(days=1)
    if label == "QTD":
        first_month = 3 * ((as_of.month - 1) // 3) + 1
        return dt.date(as_of.year, first_month, 1) - dt.timedelta(days=1)
    if label == "YTD":
        return dt.date(as_of.year - 1, 12, 31)
    count = int(label[:-1])
    return months_before(as_of, count * 12 if label.endswith("Y") else count)


def horizon_return(
    dates: list[dt.date], returns: FloatArray, label: str, periods_per_year: int
) -> float | None:
    """Return the compounded, and over a year annualised, return for ``label``, or None."""
    if not is_horizon(label):
        raise ValueError(f"{label!r} is not a horizon; use MTD, QTD, YTD, ITD, 3M, or 5Y.")
    values = np.asarray(returns, dtype=np.float64)
    known = [index for index, value in enumerate(values) if math.isfinite(value)]
    if not known:
        return None
    if label == "ITD":
        chosen = values[known]
        if chosen.size >= periods_per_year:
            return annualized_return(chosen, periods_per_year)
        return float(np.prod(1.0 + chosen)) - 1.0
    as_of = dates[-1]
    start = horizon_start(label, as_of)
    assert start is not None
    if not _covers(dates[known[0]], start, periods_per_year):
        return None
    chosen = np.array([values[i] for i in known if dates[i] > start], dtype=np.float64)
    if chosen.size == 0:
        return None
    growth = float(np.prod(1.0 + chosen))
    months = _length_in_months(label)
    return growth ** (12.0 / months) - 1.0 if months > 12 else growth - 1.0


def _length_in_months(label: str) -> int:
    """Return a trailing horizon's months; period-to-date horizons never annualise, so 0."""
    if label in {"MTD", "QTD", "YTD"}:
        return 0
    return int(label[:-1]) * (12 if label.endswith("Y") else 1)


def monthly_returns(dates: list[dt.date], returns: FloatArray) -> dict[tuple[int, int], float]:
    """Return compounded returns by ``(year, month)``, for months with at least one return."""
    growth: dict[tuple[int, int], float] = {}
    for day, value in zip(dates, returns, strict=True):
        if math.isfinite(value):
            key = (day.year, day.month)
            growth[key] = growth.get(key, 1.0) * (1.0 + float(value))
    return {key: value - 1.0 for key, value in growth.items()}


def calendar_years(dates: list[dt.date], returns: FloatArray) -> dict[int, float]:
    """Return compounded returns by calendar year."""
    growth: dict[int, float] = {}
    for day, value in zip(dates, returns, strict=True):
        if math.isfinite(value):
            growth[day.year] = growth.get(day.year, 1.0) * (1.0 + float(value))
    return {year: value - 1.0 for year, value in growth.items()}


def wealth(returns: FloatArray) -> FloatArray:
    """Return compounded wealth from 1; a missing return leaves it unchanged."""
    values = np.nan_to_num(np.asarray(returns, dtype=np.float64))
    return np.cumprod(1.0 + values)


def underwater(returns: FloatArray) -> FloatArray:
    """Return the drawdown from the running peak at each bar, starting from wealth of 1."""
    level = wealth(returns)
    peaks = np.maximum.accumulate(np.maximum(level, 1.0))
    result: FloatArray = level / peaks - 1.0
    return result


@dataclass(frozen=True)
class Drawdown:
    """One peak-to-trough episode; ``recovered`` is None while still under the peak."""

    peak: dt.date | None  # None: the peak is the start of the series
    trough: dt.date
    recovered: dt.date | None
    depth: float


def drawdowns(dates: list[dt.date], returns: FloatArray, top: int = 5) -> list[Drawdown]:
    """Return the ``top`` deepest episodes, deepest first."""
    gap = underwater(returns)
    episodes: list[Drawdown] = []
    start: int | None = None
    for index, value in enumerate(gap):
        if value < 0.0 and start is None:
            start = index
        if start is not None and (value == 0.0 or index == len(gap) - 1):
            end = index if value < 0.0 else index - 1
            deepest = start + int(np.argmin(gap[start : end + 1]))
            episodes.append(
                Drawdown(
                    peak=dates[start - 1] if start > 0 else None,
                    trough=dates[deepest],
                    recovered=dates[index] if value == 0.0 else None,
                    depth=float(gap[deepest]),
                )
            )
            start = None
    return sorted(episodes, key=lambda episode: episode.depth)[:top]


def _covers(first: dt.date, start: dt.date, periods_per_year: int) -> bool:
    """A first return dated within one bar after ``start`` covers the time from ``start``."""
    grace = max(math.ceil(366 / periods_per_year), 4)
    return (first - start).days <= grace
