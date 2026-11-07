"""What a report section receives: the books' returns, the benchmark, and the settings."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .metrics import FloatArray
from .settings import Settings
from .tables import Table


@dataclass(frozen=True)
class Context:
    """The data one report is computed from, up to its as-of date."""

    books: Table
    settings: Settings
    benchmark: FloatArray | None = None
    benchmark_name: str = ""
    data_notes: list[str] = field(default_factory=list)

    @property
    def as_of(self) -> dt.date:
        """The last date in the report."""
        return self.books.dates[-1]

    @property
    def dates(self) -> list[dt.date]:
        """The report's dates."""
        return self.books.dates

    def returns(self, book: str) -> FloatArray:
        """One book's per-bar returns, as fractions."""
        return self.books.column(book)
