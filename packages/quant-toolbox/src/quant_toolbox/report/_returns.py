"""A returns section: a fund, its benchmarks, and the excess over each, by period.

Periods are ``1D`` (the return on the last date) and any horizon `performance.horizon_return`
reads: MTD, QTD, YTD, ITD, ``3M``, ``1Y``, ``5Y``. A horizon over a year is annualised; one a
series does not cover is n/a. An excess row is the fund's period return less the other's, so a
reader can check it against the two rows above it.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence

import numpy as np

from quant_toolbox.performance import Series, horizon_return, is_horizon
from quant_toolbox.report._document import Cell, Column, TableBlock

RETURN_PERIODS = ("1D", "MTD", "QTD", "YTD", "1Y", "3Y", "5Y", "ITD")


def returns_table(
    dates: Sequence[dt.date],
    series: Mapping[str, Series],
    periods_per_year: int,
    *,
    periods: Sequence[str] = RETURN_PERIODS,
    excess_over: str | None = None,
    calendar: object = None,
    title: str = "Returns by horizon",
) -> TableBlock:
    """One row per series (one return per date each), one column per period.

    With ``excess_over`` (the name of one series, usually the fund), a row per other series gives
    that series' excess over it. ``calendar`` is `horizon_return`'s: None uses the configured one.
    """
    for period in periods:
        if period != "1D" and not is_horizon(period):
            raise ValueError(f"{period!r} is not a period; use 1D, MTD, QTD, YTD, ITD, 3M, or 5Y.")
    if excess_over is not None and excess_over not in series:
        raise ValueError(f"No series {excess_over!r}; the table has {', '.join(series)}.")
    days = list(dates)
    if not days:
        raise ValueError("No dates to report returns over.")
    by_name: dict[str, list[float | None]] = {}
    for name, returns in series.items():
        values = np.asarray(returns, dtype=np.float64).ravel()
        if values.size != len(days):
            raise ValueError(f"{name} has {values.size} returns for {len(days)} dates.")
        by_name[name] = [
            _period_return(days, values, period, periods_per_year, calendar) for period in periods
        ]
    rows: list[list[Cell]] = [[name, *values] for name, values in by_name.items()]
    if excess_over is not None:
        base = by_name[excess_over]
        for name, other in by_name.items():
            if name != excess_over:
                rows.append([f"{excess_over} over {name}", *_less(base, other)])
    return TableBlock(
        title,
        [Column("Series", "text"), *(Column(period) for period in periods)],
        rows,
        note=(
            f"1D is the return dated {days[-1].isoformat()}. Over one year annualised; n/a where "
            "a series does not cover the period. An excess row is the difference of two returns."
        ),
    )


def _period_return(
    days: list[dt.date], values: np.ndarray, period: str, periods_per_year: int, calendar: object
) -> float | None:
    if period == "1D":
        last = float(values[-1])
        return last if np.isfinite(last) else None
    return horizon_return(days, values, period, periods_per_year, calendar=calendar)


def _less(left: list[float | None], right: list[float | None]) -> list[Cell]:
    return [None if a is None or b is None else a - b for a, b in zip(left, right, strict=True)]
