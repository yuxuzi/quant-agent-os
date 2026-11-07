"""A synthetic book for demos and tests. It describes no real fund."""

from __future__ import annotations

import datetime as dt

import numpy as np

from .tables import Table


def business_days(count: int, start: dt.date = dt.date(2022, 1, 3)) -> list[dt.date]:
    """Return ``count`` weekdays from ``start``; holidays are ignored."""
    days: list[dt.date] = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += dt.timedelta(days=1)
    return days


def demo_book(
    count: int = 400, assets: int = 12, seed: int = 21
) -> tuple[Table, Table, dict[str, str]]:
    """Return ``(positions, prices, groups)``: random weights and random-walk prices."""
    generator = np.random.default_rng(seed)
    names = [f"ASSET{index:02d}" for index in range(assets)]
    days = business_days(count)
    market = generator.normal(0.0003, 0.009, count)
    loadings = generator.uniform(0.5, 1.4, assets)
    returns = market[:, None] * loadings + generator.normal(0.0, 0.012, (count, assets))
    prices = 100.0 * np.cumprod(1.0 + returns, axis=0)
    base = generator.normal(0.0, 0.08, assets)
    moves = generator.normal(0.0, 0.002, (count, assets))
    weights = base + np.cumsum(moves, axis=0)
    all_prices = np.column_stack([prices, 100.0 * np.cumprod(1.0 + market)])
    sectors = ["Technology", "Financials", "Energy", "Health"]
    groups = {name: sectors[index % len(sectors)] for index, name in enumerate(names)}
    return (
        Table(days, names, weights),
        Table(days, [*names, "Index"], all_prices),
        groups,
    )
