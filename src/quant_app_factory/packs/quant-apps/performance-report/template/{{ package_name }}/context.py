"""What a report section receives: the books' returns, the benchmark, and the settings."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from quant_toolbox.data import FloatArray, Table

from .frequency import Periods
from .settings import Settings


@dataclass(frozen=True)
class Context:
    """The data one report is computed from, up to its as-of date."""

    books: Table
    settings: Settings
    benchmark: FloatArray | None = None
    benchmark_name: str = ""
    data_notes: list[str] = field(default_factory=list)
    periods: Periods | None = None  # how many periods a year, and how that was chosen

    @property
    def as_of(self) -> dt.date:
        """The last date in the report."""
        return self.books.dates[-1]

    @property
    def periods_per_year(self) -> int:
        """The periods a year the figures annualise with: the count resolved from the dates."""
        if self.periods is not None:
            return self.periods.count
        if isinstance(self.settings.periods_per_year, int):
            return self.settings.periods_per_year
        raise ValueError("periods_per_year is auto and unresolved; use build_context.")

    @property
    def dates(self) -> list[dt.date]:
        """The report's dates."""
        return self.books.dates

    def returns(self, book: str) -> FloatArray:
        """One book's per-bar returns, as fractions."""
        return self.books.column(book)
