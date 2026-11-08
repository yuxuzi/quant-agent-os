"""Price panels: prices by date and asset, read through the shared table format."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .firm.loaders import LOADERS
from .metrics import FloatArray
from .tables import Table, TableError, load_table

DataError = TableError


@dataclass(frozen=True)
class Panel:
    """Prices for ``assets`` (columns) at ``dates`` (rows). Missing prices are NaN."""

    dates: list[dt.date]
    assets: list[str]
    prices: FloatArray

    def __post_init__(self) -> None:
        table = Table(self.dates, self.assets, self.prices)  # shape, order, and unique names
        if len(table.dates) < 2:
            raise DataError("A panel needs at least two dates.")
        finite = self.prices[np.isfinite(self.prices)]
        if finite.size and float(finite.min()) <= 0.0:
            raise DataError("Prices must be positive.")

    def fingerprint(self) -> str:
        """SHA-256 of the dates, assets, and prices: changing any of them changes it."""
        return Table(self.dates, self.assets, self.prices).fingerprint()


def open_panel(source: str, base: Path = Path()) -> Panel:
    """Load what a ``data`` setting names: "demo" or a price file, relative to ``base``."""
    if source == "demo":
        from .synthetic import demo_panel

        return demo_panel()
    path = base / source
    if not path.is_file():
        raise DataError(f"No price file at {path}; set data in config.toml or pass --prices.")
    table = load_table(path, LOADERS)
    return Panel(table.dates, table.columns, table.values)


def aligned(table: Table, panel: Panel, what: str) -> FloatArray:
    """Return ``table``'s values on ``panel``'s dates and assets, NaN where it has none.

    A signal or weight is used on the date it carries, never earlier, so a file cannot move data
    backward. Its timing is its author's responsibility; a run records its fingerprint.
    """
    shared = [asset for asset in panel.assets if asset in table.columns]
    if not shared:
        raise DataError(f"The {what} file has none of the prices file's assets.")
    rows = {day: index for index, day in enumerate(table.dates)}
    if not any(day in rows for day in panel.dates):
        raise DataError(f"The {what} file has no date in common with the prices file.")
    result = np.full(panel.prices.shape, np.nan)
    for row, day in enumerate(panel.dates):
        if day in rows:
            for asset in shared:
                result[row, panel.assets.index(asset)] = table.column(asset)[rows[day]]
    return result
