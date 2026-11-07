"""Reconcile: compare monthly returns against reference figures, such as the administrator's.

The reference is a table of returns by date. Its dates are matched by year and month. Our figures
are the book's returns compounded within each month. A month differs when the absolute difference
is above the tolerance. A month on only one side is reported, never skipped silently, and a month
missing from the reference does not count as a break, because the reference is often shorter.
"""

from __future__ import annotations

from dataclasses import dataclass

from .document import Column, TableBlock
from .horizons import monthly_returns
from .tables import Table, TableError


@dataclass(frozen=True)
class Difference:
    """One book and month on both sides."""

    book: str
    month: str
    ours: float
    reference: float

    @property
    def bps(self) -> float:
        """Ours minus the reference, in basis points."""
        return (self.ours - self.reference) * 10_000.0


@dataclass(frozen=True)
class Reconciliation:
    """The compared months, the breaks among them, and what could not be compared."""

    compared: list[Difference]
    breaks: list[Difference]
    only_ours: list[str]
    only_reference: list[str]
    tolerance_bps: float

    @property
    def clean(self) -> bool:
        """Whether every compared month agrees, and nothing in the reference went uncompared."""
        return not self.breaks and not self.only_reference


def reconcile(ours: Table, reference: Table, tolerance_bps: float) -> Reconciliation:
    """Compare each book that is in both tables, month by month."""
    if tolerance_bps < 0:
        raise TableError("The tolerance cannot be negative.")
    shared = [name for name in ours.columns if name in reference.columns]
    if not shared:
        raise TableError(
            f"No book in both files: ours are {', '.join(ours.columns)}, the reference has "
            f"{', '.join(reference.columns)}. Column names must match."
        )
    compared: list[Difference] = []
    only_ours = [f"{name}: not in the reference" for name in ours.columns if name not in shared]
    only_reference = [
        f"{name}: not in our file" for name in reference.columns if name not in shared
    ]
    for book in shared:
        mine = monthly_returns(ours.dates, ours.column(book))
        theirs = monthly_returns(reference.dates, reference.column(book))
        for key in sorted(mine.keys() | theirs.keys()):
            month = f"{key[0]}-{key[1]:02d}"
            if key not in theirs:
                only_ours.append(f"{book} {month}: not in the reference")
            elif key not in mine:
                only_reference.append(f"{book} {month}: not in our file")
            else:
                compared.append(Difference(book, month, mine[key], theirs[key]))
    breaks = [item for item in compared if abs(item.bps) > tolerance_bps]
    return Reconciliation(compared, breaks, only_ours, only_reference, tolerance_bps)


def as_block(result: Reconciliation) -> TableBlock:
    """The breaks as a table for a report, or every compared month count when clean."""
    return TableBlock(
        title="Reconciliation breaks",
        columns=[
            Column("Book", "text"),
            Column("Month", "text"),
            Column("Ours"),
            Column("Reference"),
            Column("Difference (bps)", "ratio"),
        ],
        rows=[[d.book, d.month, d.ours, d.reference, d.bps] for d in result.breaks],
        note=f"{len(result.compared)} months compared at {result.tolerance_bps:g} bps tolerance.",
    )
