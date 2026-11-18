"""Calendars for synthetic data: weekdays, with holidays ignored."""

from __future__ import annotations

import datetime as dt


def business_days(count: int, start: dt.date = dt.date(2021, 1, 4)) -> list[dt.date]:
    """Return ``count`` weekdays from ``start``; holidays are ignored."""
    days: list[dt.date] = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += dt.timedelta(days=1)
    return days
