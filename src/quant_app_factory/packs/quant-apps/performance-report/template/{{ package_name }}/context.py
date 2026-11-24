"""What a report section receives: the books' returns, the benchmark, and the settings."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from quant_toolbox.data import FloatArray, Frequency, Table

from .settings import Settings


@dataclass(frozen=True)
class Context:
    """The data one report is computed from, up to its as-of date."""

    books: Table
    settings: Settings
    benchmarks: dict[str, str] = field(default_factory=dict)  # a book -> its benchmark's column
    benchmark_returns: dict[str, FloatArray] = field(default_factory=dict)  # a column -> returns
    data_notes: list[str] = field(default_factory=list)
    periods: Frequency | None = None  # how many periods a year the dates show, or the settings set
    periods_how: str = "inferred from the dates"

    def benchmark_of(self, book: str) -> tuple[str, FloatArray] | None:
        """The name and returns of ``book``'s benchmark, or None when it has none."""
        name = self.benchmarks.get(book, "")
        return (name, self.benchmark_returns[name]) if name else None

    @property
    def benchmark_names(self) -> list[str]:
        """Every benchmark the books use, each once, in the books' order."""
        return list(dict.fromkeys(name for name in self.benchmarks.values() if name))

    @property
    def benchmark(self) -> FloatArray | None:
        """The benchmark, when the books use just one; a table of several gives None."""
        names = self.benchmark_names
        return self.benchmark_returns[names[0]] if len(names) == 1 else None

    @property
    def benchmark_name(self) -> str:
        """The benchmark's column, when the books use just one; otherwise empty."""
        names = self.benchmark_names
        return names[0] if len(names) == 1 else ""

    @property
    def as_of(self) -> dt.date:
        """The last date in the report."""
        return self.books.dates[-1]

    @property
    def periods_per_year(self) -> int:
        """The periods a year the figures annualise with: the count resolved from the dates."""
        if self.periods is not None:
            return self.periods.periods_per_year
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
