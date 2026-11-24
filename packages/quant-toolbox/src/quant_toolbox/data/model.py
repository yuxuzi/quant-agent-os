"""The six standard tables (decision 0037): columns, a validator, the as-of rule, wide tables.

Every table is long: one row per key. `TABLES` describes each one, `validate` names every problem
in a column-oriented ``dict[str, list]``, `as_of` picks the vintage a row list stands for, and
`to_wide` pivots a long table to the wide `Table` the tools read. Pure Python and numpy.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from quant_toolbox._types import FloatArray
from quant_toolbox.data._tables import Table, TableError, parse_date
from quant_toolbox.data.sql import Rows, pivot_long

ColumnType = Literal["text", "date", "number"]


@dataclass(frozen=True)
class Column:
    """One column of a standard table: its name, its type, and whether every row must fill it."""

    name: str
    type: ColumnType
    required: bool = False


@dataclass(frozen=True)
class TableSpec:
    """A standard table: its columns, the key that makes a row unique, and what it holds."""

    name: str
    key: tuple[str, ...]
    columns: tuple[Column, ...]
    doc: str

    def column(self, name: str) -> Column | None:
        """Return the column called ``name``, or None when the table has no such column."""
        return next((column for column in self.columns if column.name == name), None)


def _req(name: str, kind: ColumnType) -> Column:
    return Column(name, kind, required=True)


def _opt(name: str, kind: ColumnType) -> Column:
    return Column(name, kind, required=False)


TABLES: Mapping[str, TableSpec] = {
    "securities": TableSpec(
        name="securities",
        key=("instrument", "valid_from"),
        columns=(
            _req("instrument", "text"),
            _req("valid_from", "date"),
            _opt("valid_to", "date"),
            _opt("ticker", "text"),
            _opt("cusip", "text"),
            _opt("isin", "text"),
            _opt("figi", "text"),
            _opt("asset_class", "text"),
            _opt("currency", "text"),
            _opt("sector", "text"),
            _opt("rating", "text"),
            _opt("issuer", "text"),
        ),
        doc="Identifiers with their history, asset class, currency, sector, rating, and issuer.",
    ),
    "prices": TableSpec(
        name="prices",
        key=("instrument", "date", "field", "source"),
        columns=(
            _req("instrument", "text"),
            _req("date", "date"),
            _req("field", "text"),
            _req("source", "text"),
            _req("value", "number"),
        ),
        doc="One price field (close, adjusted close, yield, spread) of one instrument on one day.",
    ),
    "returns": TableSpec(
        name="returns",
        key=("entity", "date", "as_of"),
        columns=(
            _req("entity", "text"),
            _req("date", "date"),
            _req("as_of", "date"),
            _req("value", "number"),
            _opt("frequency", "text"),
            _opt("basis", "text"),
            _opt("currency", "text"),
        ),
        doc="A return of a fund, book, strategy, share class, or benchmark, by vintage.",
    ),
    "holdings": TableSpec(
        name="holdings",
        key=("book", "instrument", "date"),
        columns=(
            _req("book", "text"),
            _req("instrument", "text"),
            _req("date", "date"),
            _req("quantity", "number"),
            _opt("market_value", "number"),
            _opt("weight", "number"),
        ),
        doc="What a book held in one instrument on one day: quantity, market value, and weight.",
    ),
    "measures": TableSpec(
        name="measures",
        key=("entity", "date", "measure", "model", "as_of"),
        columns=(
            _req("entity", "text"),
            _req("date", "date"),
            _req("measure", "text"),
            _req("model", "text"),
            _req("as_of", "date"),
            _req("value", "number"),
        ),
        doc="A risk or analytics output (VaR, exposure, OAS, factor loading) by model and vintage.",
    ),
    "reference": TableSpec(
        name="reference",
        key=("kind", "name", "date"),
        columns=(
            _req("kind", "text"),
            _req("name", "text"),
            _req("date", "date"),
            _opt("value", "number"),
        ),
        doc=(
            "Named reference data: trading calendars, FX rates, benchmark constituents, and "
            "entity relations, told apart by kind."
        ),
    ),
}


def validate(table_name: str, columns: Mapping[str, Sequence[Any]]) -> list[str]:
    """Return every problem with ``columns`` as ``table_name``, or an empty list when it is valid.

    ``columns`` maps each column name to its values, one per row. Problems named: an unknown
    table, a missing required column, columns of unequal length, a value of the wrong type (with
    its row), a required cell left empty, and two rows with the same key.
    """
    spec = _spec(table_name)
    problems: list[str] = []
    lengths = {len(values) for values in columns.values()}
    if len(lengths) > 1:
        problems.append("The columns have different lengths; every column needs one value per row.")
    rows = max(lengths, default=0)
    for column in spec.columns:
        if column.name not in columns:
            if column.required:
                problems.append(f"Missing required column '{column.name}' of {spec.name}.")
            continue
        for number, value in enumerate(columns[column.name], 1):
            problem = _cell_problem(column, value)
            if problem is not None:
                problems.append(f"Row {number}, column '{column.name}': {problem}")
    if all(key in columns for key in spec.key):
        first_seen: dict[tuple[Any, ...], int] = {}
        for number in range(1, rows + 1):
            key = tuple(columns[name][number - 1] for name in spec.key)
            if key in first_seen:
                shown = ", ".join(str(part) for part in key)
                problems.append(
                    f"Row {number} repeats the key ({', '.join(spec.key)}) = ({shown}) of row "
                    f"{first_seen[key]}."
                )
            else:
                first_seen[key] = number
    return problems


def as_of(
    rows: Sequence[Mapping[str, Any]],
    date: dt.date,
    vintage: dt.date | None = None,
    *,
    key: Sequence[str] = ("entity", "date"),
) -> list[dict[str, Any]]:
    """Return one row per ``key`` group: its latest vintage at or before ``date``.

    Rows carry their vintage in ``as_of``. Without ``vintage``, each group keeps the row with the
    latest ``as_of`` that is on or before ``date``, so a restated figure replaces the original
    only from the day it was restated. With ``vintage``, each group keeps the row whose ``as_of``
    is exactly that day, and groups without one are left out. Rows are returned sorted by key.
    """
    chosen: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        vintage_day = parse_date(row["as_of"])
        if vintage is None:
            if vintage_day > date:
                continue
        elif vintage_day != parse_date(vintage):
            continue
        group = tuple(row[name] for name in key)
        held = chosen.get(group)
        if held is None or vintage_day > parse_date(held["as_of"]):
            chosen[group] = dict(row)
    return [chosen[group] for group in sorted(chosen, key=str)]


def to_wide(
    columns: Mapping[str, Sequence[Any]],
    *,
    names: str = "entity",
    value: str = "value",
    date: str = "date",
    where: Mapping[str, object] | None = None,
) -> Table:
    """Pivot long rows to a wide `Table`: one date per row, one column per name.

    ``names`` is the column whose values become the table's columns, ``value`` the numbers, and
    ``date`` the index. ``where`` keeps only the rows whose columns equal the given values, so a
    prices table with several fields becomes one wide table per field. Two rows for the same
    date and name raise `TableError`, because a pivot would overwrite one of them.
    """
    wanted = dict(where or {})
    names_list = list(columns)
    for needed in (names, value, date, *wanted):
        if needed not in columns:
            raise TableError(f"No column '{needed}'; the table has {', '.join(names_list)}.")
    count = len(columns[date])
    kept = [
        row for row in range(count) if all(columns[name][row] == wanted[name] for name in wanted)
    ]
    if not kept:
        raise TableError("No rows match the filter, so there is no wide table to build.")
    long_rows = tuple(
        (
            parse_date(columns[date][row]),
            str(columns[names][row]),
            _float_or_none(columns[value][row]),
        )
        for row in kept
    )
    result = pivot_long(
        Rows(columns=("date", "name", "value"), rows=long_rows),
        "date",
        "name",
        "value",
        duplicates="error",
    )
    days = [parse_date(day) for day in result.column("date")]
    table_names = list(result.columns[1:])
    values = [[row[i] for i in range(1, len(row))] for row in result.rows]
    return Table(days, table_names, _matrix(values, len(days), len(table_names)))


def _spec(table_name: str) -> TableSpec:
    spec = TABLES.get(table_name)
    if spec is None:
        raise TableError(f"No standard table {table_name!r}; the tables are {', '.join(TABLES)}.")
    return spec


def _cell_problem(column: Column, value: Any) -> str | None:
    if value is None:
        return "is empty." if column.required else None
    if column.type == "number":
        if isinstance(value, bool) or not isinstance(value, int | float):
            return f"expected a number, got {value!r}."
        return None
    if column.type == "date":
        if isinstance(value, dt.date | str):
            try:
                parse_date(value)
            except TableError:
                return f"expected a date as YYYY-MM-DD, got {value!r}."
            return None
        return f"expected a date, got {value!r}."
    if not isinstance(value, str):
        return f"expected text, got {value!r}."
    return None


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return None if math.isnan(number) else number


def _matrix(values: list[list[Any]], rows: int, cols: int) -> FloatArray:
    grid = np.full((rows, cols), np.nan)
    for i, row in enumerate(values):
        for j, cell in enumerate(row):
            if cell is not None:
                grid[i, j] = cell
    return grid
