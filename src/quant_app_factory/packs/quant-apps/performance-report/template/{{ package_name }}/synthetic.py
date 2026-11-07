"""Synthetic returns for demos and tests. They describe no real fund."""

from __future__ import annotations

import datetime as dt

import numpy as np

from .tables import Table


def business_days(count: int, start: dt.date = dt.date(2021, 1, 4)) -> list[dt.date]:
    """Return ``count`` weekdays from ``start``; holidays are ignored."""
    days: list[dt.date] = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += dt.timedelta(days=1)
    return days


def demo_returns(count: int = 1000, seed: int = 11) -> Table:
    """Three random-walk books and an index: any edge or pattern in them is chance."""
    generator = np.random.default_rng(seed)
    market = generator.normal(0.0003, 0.009, count)
    books = np.column_stack(
        [
            0.2 * market + generator.normal(0.0004, 0.004, count),
            0.6 * market + generator.normal(0.0002, 0.006, count),
            generator.normal(0.0003, 0.005, count),
            market,
        ]
    )
    return Table(business_days(count), ["Equity L/S", "Macro", "Stat Arb", "Index"], books)
