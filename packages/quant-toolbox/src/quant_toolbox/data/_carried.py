"""Facts about returns that suggest carried-forward prices: exact zero runs, and closed weekdays.

These are facts, not warnings: the caller words them. The figures are never changed by a check.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

RUN = 3  # this many exact zero returns in a row suggest a carried-forward price
SHARE = 0.05  # or more than this share of a window, if there are at least two of them


@dataclass(frozen=True)
class ZeroRuns:
    """An asset whose exact zero returns suggest carried-forward prices."""

    asset: str
    zero_returns: int  # exact zero returns in the window
    window: int  # returns in the window
    longest_run: int  # the most exact zero returns in a row


@dataclass(frozen=True)
class CarriedForward:
    """What the returns show: the assets flagged, and the weekdays on which every asset is zero."""

    assets: list[ZeroRuns] = field(default_factory=list)
    closed_days: list[dt.date] = field(default_factory=list)


def _zero_runs(values: Sequence[float]) -> tuple[int, int]:
    """The count of exact zero returns, and the longest run of them in a row."""
    count, longest, run = 0, 0, 0
    for value in values:
        if value == 0.0:
            count += 1
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return count, longest


def carried_forward(
    assets: Mapping[str, Sequence[float]], dates: Sequence[dt.date] | None = None
) -> CarriedForward:
    """The carried-forward facts in ``assets``' returns, one list per asset.

    An asset is flagged when it has a run of `RUN` exact zero returns, or at least two exact zero
    returns that are more than `SHARE` of its window. A weekday on which every asset's return is
    exactly zero is a closed day; it is checked only when ``dates`` (one per return) are given.
    """
    flagged: list[ZeroRuns] = []
    for asset, values in assets.items():
        count, longest = _zero_runs(values)
        if values and (longest >= RUN or (count >= 2 and count > SHARE * len(values))):
            flagged.append(ZeroRuns(asset, count, len(values), longest))
    columns = list(assets.values())
    closed: list[dt.date] = []
    if dates is not None and columns and all(len(values) == len(dates) for values in columns):
        closed = [
            day
            for index, day in enumerate(dates)
            if day.weekday() < 5 and all(values[index] == 0.0 for values in columns)
        ]
    return CarriedForward(assets=flagged, closed_days=closed)
