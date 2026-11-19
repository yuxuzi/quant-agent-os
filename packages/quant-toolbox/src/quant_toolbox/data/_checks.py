"""Checks on returns before any metric runs. Each returns warnings as plain sentences."""

from __future__ import annotations

import statistics
from collections.abc import Sequence

import numpy as np

from quant_toolbox.data._calendar import (
    CalendarError,
    as_calendar,
    configured_calendar,
    session_gaps,
)
from quant_toolbox.data._tables import Table

MIN_OBSERVATIONS = 12  # below this, a metric of a return series is unreliable
PERCENT_LIKE = 0.2  # a typical return above this is more likely a percent than a fraction
OUTLIER = 0.5  # a per-bar move beyond this is unusual


def short_sample(name: str, count: int, minimum: int = MIN_OBSERVATIONS) -> list[str]:
    """A warning when ``count`` observations are too few to rely on, else nothing."""
    if count >= minimum:
        return []
    return [f"Only {count} observations for {name}; results are unreliable below {minimum}."]


def percent_like(name: str, values: Sequence[float]) -> list[str]:
    """A warning when returns look like percentages (typical size above 0.2), else nothing."""
    finite = [abs(float(value)) for value in values if np.isfinite(value)]
    if not finite:
        return []
    typical = statistics.median(finite)
    if typical <= PERCENT_LIKE:
        return []
    return [
        f"Returns of {name} look like percentages (typical size {typical:g}); the tools expect "
        "fractions, 0.01 for 1%, and these results would be wrong if so."
    ]


def return_notes(table: Table, outlier: float = OUTLIER, *, calendar: object = None) -> list[str]:
    """Gaps inside each column's history, unusually large moves, and late starts or early ends.

    With a ``calendar`` (anything `as_calendar` reads, with a range), or else the one the fund
    configured, it also notes rows dated on days that are not sessions, and sessions with no row.
    Without either, no calendar is checked, since plain weekdays would flag every holiday.
    """
    notes = []
    for name in table.columns:
        values = table.column(name)
        known = np.flatnonzero(np.isfinite(values))
        if known.size == 0:
            notes.append(f"{name} has no returns.")
            continue
        gaps = int((~np.isfinite(values[known[0] : known[-1] + 1])).sum())
        if gaps:
            notes.append(
                f"{name} has {gaps} missing returns inside its history, left out of compounding."
            )
        large = int((np.abs(values[known]) > outlier).sum())
        if large:
            notes.append(
                f"{name} has {large} moves beyond {outlier:.0%} in one bar. Check they are real, "
                "and that returns_unit is right."
            )
        if known[0] > 0:
            notes.append(
                f"{name} starts {table.dates[int(known[0])]}, after the file's first date."
            )
        if known[-1] < len(values) - 1:
            notes.append(f"{name} ends {table.dates[int(known[-1])]}, before the as-of date.")
    chosen = as_calendar(calendar) if calendar is not None else configured_calendar()
    if chosen is None:
        return notes
    try:
        missing, off = session_gaps(table, chosen)
    except CalendarError:
        notes.append(
            f"The dates run outside the days {chosen.name} knows ({chosen.first} to "
            f"{chosen.last}); sessions were not checked."
        )
        return notes
    if off:
        notes.append(
            f"{len(off)} row(s) dated on a day {chosen.name} has no session, the first {off[0]}."
        )
    if missing:
        notes.append(
            f"{len(missing)} session(s) of {chosen.name} have no row, the first {missing[0]}."
        )
    return notes
