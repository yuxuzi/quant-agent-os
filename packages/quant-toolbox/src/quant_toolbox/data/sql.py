"""A standard SQL connection: an engine, a fetch with bind parameters, and a long-to-wide pivot.

A fund's data sits in a database, and the code that reads it repeats: an engine whose password
may be a token that expires, SQL written in one parameter style per driver, `Decimal` and `date`
cells, identifier case, NULLs, and the pivot of ``(date, name, value)`` rows to the wide table the
rest of this package reads. This module does that once, over SQLAlchemy (the ``sql`` extra,
loaded in the function that needs it) and numpy. The database's own driver (psycopg, pyodbc,
snowflake-sqlalchemy, databricks-sql-connector) is the caller's install.

Conventions:

- SQL is always `sqlalchemy.text` with ``:name`` binds, whatever the driver's own style; a list
  value binds as an ``IN`` list. A literal colon in a string that looks like a bind needs ``\\:``.
- A `Decimal` becomes a float, a `datetime.date` becomes ISO text (``2024-03-29``), and a
  `datetime.datetime` does too when it is midnight with no zone, else its ISO text. A NULL in a
  column of numbers is NaN; a NULL elsewhere stays None.
- Column names are lower case, so Snowflake's upper-case names read as everyone else's. Two
  columns that differ only by case are an error. Values, such as a name in a key column, are kept.
- `pivot_long` refuses two rows for one ``(index, column)`` pair unless told what they mean: a call
  and a distribution on one day, netted by a pivot, is a silent error worth a loud one.
"""

from __future__ import annotations

import csv
import io
import math
import re
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from quant_toolbox._optional import require

if TYPE_CHECKING:
    from sqlalchemy.engine import URL, Engine

Duplicates = Literal["error", "sum", "last"]
Shape = Literal["long", "wide", "table"]
Credential = Callable[[], str]
_BIND = re.compile(r"(?<![:\w\\]):([A-Za-z_]\w*)")
_PURPOSE = "reading a SQL database"


class SqlError(ValueError):
    """Raised when a query's result cannot be shaped as asked, or a connection cannot be made."""


@dataclass(frozen=True)
class Rows:
    """A query's result: lower-case column names and normalised rows (see the module notes)."""

    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]

    def column(self, name: str) -> list[object]:
        """Return column ``name`` (any case), or raise `SqlError` naming the columns there are."""
        return [row[self.position(name)] for row in self.rows]

    def position(self, name: str) -> int:
        """Return the position of column ``name`` (any case), or raise `SqlError`."""
        try:
            return self.columns.index(name.lower())
        except ValueError:
            raise SqlError(
                f"The result has no column '{name}'; its columns are {', '.join(self.columns)}."
            ) from None


@dataclass(frozen=True)
class SqlDataset:
    """One dataset declared as data: the SQL that reads it and how its rows become a table.

    ``shape`` ``long`` means the query returns ``(index, column, value)`` rows, pivoted wide (a
    date by names table), with ``index``, ``column``, and ``value`` naming the result columns
    (default: the first three). ``wide`` means the query already returns an index column and
    numeric columns, and ``index`` names the index (default: the first column). ``table`` means
    rows as they are, one per entity, the first column its key.

    ``params`` are ``:name`` binds. ``duplicates`` and ``fill`` are `pivot_long`'s (``fill``
    replaces a cell with no row, never a NULL). ``calendar``, for a long dataset, is SQL returning
    the index values to pivot onto, on the source ``calendar_source`` (default: ``source``), so a
    series with gaps still has a row for every business day. ``source`` names a database when
    a store has several; ``description`` is for people. ``index_name`` renames the index column of
    the result (``date``), so the SQL need not alias an expression.
    """

    id: str
    sql: str
    params: Mapping[str, object] = field(default_factory=dict)
    shape: Shape = "long"
    index: str | None = None
    column: str | None = None
    value: str | None = None
    duplicates: Duplicates = "error"
    fill: float | None = None
    calendar: str | None = None
    calendar_source: str | None = None
    source: str | None = None
    description: str = ""
    index_name: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.sql.strip():
            raise SqlError("A SqlDataset needs an id and its SQL.")
        if self.shape not in ("long", "wide", "table"):
            raise SqlError(f"Dataset '{self.id}': shape must be long, wide, or table.")
        if self.duplicates not in ("error", "sum", "last"):
            raise SqlError(f"Dataset '{self.id}': duplicates must be error, sum, or last.")
        named = [name for name in (self.index, self.column, self.value) if name is not None]
        if self.shape == "long" and len(named) not in (0, 3):
            raise SqlError(f"Dataset '{self.id}': name all of index, column, and value, or none.")
        if self.shape != "long" and (self.column or self.value or self.calendar):
            raise SqlError(f"Dataset '{self.id}': column, value, and calendar are for long.")

    def params_for(self, sql: str) -> dict[str, object]:
        """Return the params ``sql`` binds, raising `SqlError` for a bind with no value."""
        wanted = set(_BIND.findall(sql))
        missing = sorted(wanted - set(self.params))
        if missing:
            raise SqlError(f"Dataset '{self.id}' binds :{', :'.join(missing)} but has no value.")
        return {name: value for name, value in self.params.items() if name in wanted}


# ---- connection ------------------------------------------------------------------------------


def connect(
    url: str | URL,
    *,
    credential: Credential | None = None,
    read_only: bool = True,
    **engine_kwargs: Any,
) -> Engine:
    """Return a SQLAlchemy engine for ``url``, with a credential called for every connection.

    ``credential`` returns a password or short-lived token (an RDS IAM token lasts 15 minutes, an
    Entra token about an hour); it is called each time the pool opens a connection, so a token
    never goes stale in the engine, and it overrides any password in the URL. It sets the driver's
    ``password`` parameter; a driver that takes a connection string (pyodbc) takes a token through
    ``connect_args`` instead.

    ``read_only`` is best effort, not a sandbox: SQLite opens ``mode=ro`` and sets
    ``query_only``; PostgreSQL (psycopg, psycopg2) sets ``default_transaction_read_only``; a
    DuckDB file opens read-only. Any other dialect gets a `UserWarning`, because nothing here
    limits it: give the login only the grants it needs. ``engine_kwargs`` go to
    ``sqlalchemy.create_engine``; its ``connect_args`` are merged with what read-only adds.
    """
    sa = require("sqlalchemy", extra="sql", purpose=_PURPOSE)
    require("sqlalchemy.event", extra="sql", purpose=_PURPOSE)
    parsed = sa.engine.make_url(url)
    connect_args: dict[str, Any] = dict(engine_kwargs.pop("connect_args", None) or {})
    on_connect: list[Callable[[Any], None]] = []
    if read_only:
        parsed = _read_only(parsed, connect_args, on_connect)
    engine: Engine = sa.create_engine(parsed, connect_args=connect_args, **engine_kwargs)
    if credential is not None:
        sa.event.listen(engine, "do_connect", _with_credential(credential))
    for hook in on_connect:
        sa.event.listen(engine, "connect", _listener(hook))
    return engine


def _with_credential(credential: Credential) -> Callable[..., None]:
    def do_connect(
        dialect: object, record: object, args: object, parameters: dict[str, Any]
    ) -> None:
        parameters["password"] = credential()

    return do_connect


def _listener(hook: Callable[[Any], None]) -> Callable[..., None]:
    def on_connect(dbapi_connection: Any, record: object) -> None:
        hook(dbapi_connection)

    return on_connect


def _read_only(
    url: URL, connect_args: dict[str, Any], on_connect: list[Callable[[Any], None]]
) -> URL:
    backend, driver, database = url.get_backend_name(), url.get_driver_name(), url.database
    if backend == "sqlite":
        on_connect.append(_sqlite_query_only)
        if database and database != ":memory:" and not database.startswith("file:"):
            quoted = "file:" + database.replace("%", "%25").replace("?", "%3f").replace("#", "%23")
            return url.set(database=quoted, query={**url.query, "mode": "ro", "uri": "true"})
        return url
    if backend == "postgresql" and driver in ("psycopg", "psycopg2"):
        options = str(connect_args.get("options", "")).strip()
        connect_args["options"] = f"{options} -c default_transaction_read_only=on".strip()
        return url
    if backend == "duckdb":
        if database and database != ":memory:":
            connect_args["read_only"] = True
        return url
    warnings.warn(
        f"read_only has no effect on {backend}+{driver}: connect with a login that has only "
        "read grants.",
        UserWarning,
        stacklevel=3,
    )
    return url


def _sqlite_query_only(connection: Any) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA query_only = ON")
    finally:
        cursor.close()


# ---- fetch -----------------------------------------------------------------------------------


def fetch_rows(engine: Engine, sql: str, params: Mapping[str, object] | None = None) -> Rows:
    """Run ``sql`` with ``:name`` binds from ``params`` and return its rows, normalised.

    See the module notes for what is normalised. Raises `SqlError` for a statement that returns
    no rows, or two columns whose names differ only by case; the driver's own error otherwise.
    """
    sa = require("sqlalchemy", extra="sql", purpose=_PURPOSE)
    values = dict(params or {})
    statement = sa.text(sql)
    lists = [
        sa.bindparam(name, expanding=True)
        for name, value in values.items()
        if isinstance(value, list | tuple | set | frozenset)
    ]
    if lists:
        statement = statement.bindparams(*lists)
        values = {k: sorted(v) if isinstance(v, set | frozenset) else v for k, v in values.items()}
    with engine.connect() as connection:
        result = connection.execute(statement, values)
        if not result.returns_rows:
            raise SqlError("The statement returns no rows; fetch_rows reads, it does not write.")
        raw_names, raw_rows = list(result.keys()), result.fetchall()
    names = tuple(str(name).lower() for name in raw_names)
    if len(set(names)) != len(names):
        raise SqlError(
            f"Columns {', '.join(map(str, raw_names))} collide once lower-cased; alias them."
        )
    return Rows(names, _normalised(tuple(tuple(row) for row in raw_rows), len(names)))


def _normalised(rows: tuple[tuple[Any, ...], ...], width: int) -> tuple[tuple[object, ...], ...]:
    numeric = [
        any(_is_number(row[at]) for row in rows) for at in range(width)
    ]  # a column of numbers: its NULLs are NaN
    return tuple(
        tuple(
            math.nan if cell is None and numeric[at] else _cell(cell) for at, cell in enumerate(row)
        )
        for row in rows
    )


def _is_number(cell: object) -> bool:
    return isinstance(cell, int | float | Decimal) and not isinstance(cell, bool)


def _cell(cell: object) -> object:
    if isinstance(cell, bool):
        return int(cell)
    if isinstance(cell, Decimal):
        return float(cell)
    if isinstance(cell, datetime):
        if cell.tzinfo is None and cell.time() == time(0):
            return cell.date().isoformat()
        return cell.isoformat()
    if isinstance(cell, date):
        return cell.isoformat()
    if isinstance(cell, np.generic):
        return cell.item()
    return cell


# ---- shape -----------------------------------------------------------------------------------


def pivot_long(
    rows: Rows,
    index: str,
    column: str,
    value: str,
    *,
    duplicates: Duplicates = "error",
    fill: float | None = None,
    index_values: Sequence[object] | None = None,
) -> Rows:
    """Pivot ``(index, column, value)`` rows to a wide table: an index row, a column per name.

    The result's columns are ``index`` and then the sorted ``column`` values as text; a cell with
    no row is ``fill`` (NaN when None), and a NULL value stays NaN. ``index_values`` fixes the
    index (rows for other values are dropped), so a series with gaps still has every day.

    Two rows for one ``(index, column)`` pair raise `SqlError` naming the pairs, because a pivot
    would net or overwrite them: ``duplicates="sum"`` adds them (a NULL adds nothing) and
    ``"last"`` keeps the last row in the result's order, for when the data means that.
    """
    at = (rows.position(index), rows.position(column), rows.position(value))
    cells = [(row[at[0]], str(row[at[1]]), row[at[2]]) for row in rows.rows]
    for _, _, cell in cells:
        if cell is not None and not _is_number(cell):
            raise SqlError(f"Column '{value}' holds {cell!r}, which is not a number.")
    days = list(index_values) if index_values is not None else _ordered({c[0] for c in cells})
    names = sorted({c[1] for c in cells})
    day_at, name_at = {day: i for i, day in enumerate(days)}, {n: i for i, n in enumerate(names)}
    grid = np.full((len(days), len(names)), np.nan)
    seen = np.zeros(grid.shape, dtype=bool)
    repeated: dict[tuple[object, str], int] = {}
    for day, name, cell in cells:
        if day not in day_at:
            continue
        i, j = day_at[day], name_at[name]
        number = math.nan if cell is None else float(cell)  # type: ignore[arg-type]
        if seen[i, j]:
            repeated[(day, name)] = repeated.get((day, name), 1) + 1
            if duplicates == "sum":
                number = _plus(grid[i, j], number)
            elif duplicates == "error":
                continue
        seen[i, j], grid[i, j] = True, number
    if repeated and duplicates == "error":
        shown = ", ".join(f"({d}, {n}) x{c}" for (d, n), c in list(repeated.items())[:5])
        raise SqlError(
            f"{len(repeated)} (index, column) pairs have more than one row, such as {shown}; a "
            "pivot would net or overwrite them. Aggregate in the SQL, or pass duplicates='sum' "
            "or 'last' if that is what the data means."
        )
    if fill is not None:
        grid[~seen] = fill
    body = tuple((day, *grid[i].tolist()) for i, day in enumerate(days))
    return Rows((rows.columns[at[0]], *names), body)


def _plus(first: float, second: float) -> float:
    if math.isnan(first):
        return second
    return first if math.isnan(second) else first + second


def _ordered(values: set[object]) -> list[object]:
    try:
        return sorted(values)  # type: ignore[type-var]
    except TypeError:
        return sorted(values, key=str)


def shape_rows(dataset: SqlDataset, rows: Rows, calendar: Rows | None = None) -> Rows:
    """Return ``rows`` as ``dataset`` says its table is: pivoted, reordered, or as they are.

    ``calendar`` is the result of the dataset's calendar SQL; its first column is the index.
    """
    shaped = _shaped(dataset, rows, calendar)
    if dataset.index_name is None:
        return shaped
    return Rows((dataset.index_name.lower(), *shaped.columns[1:]), shaped.rows)


def _shaped(dataset: SqlDataset, rows: Rows, calendar: Rows | None) -> Rows:
    if dataset.shape == "long":
        if dataset.index is None or dataset.column is None or dataset.value is None:
            if len(rows.columns) < 3:
                raise SqlError(f"Dataset '{dataset.id}': a long query returns three columns.")
            names = rows.columns[:3]
        else:
            names = (dataset.index, dataset.column, dataset.value)
        days = None if calendar is None else [row[0] for row in calendar.rows]
        return pivot_long(
            rows,
            names[0],
            names[1],
            names[2],
            duplicates=dataset.duplicates,
            fill=dataset.fill,
            index_values=days,
        )
    if dataset.shape == "wide" and dataset.index is not None:
        first = rows.position(dataset.index)
        order = [first, *(at for at in range(len(rows.columns)) if at != first)]
        return Rows(
            tuple(rows.columns[at] for at in order),
            tuple(tuple(row[at] for at in order) for row in rows.rows),
        )
    return rows


def rows_to_csv(rows: Rows) -> str:
    """Return ``rows`` as CSV text: floats shortest-exact, NaN as ``NaN``, None as a blank cell."""
    text = io.StringIO()
    writer = csv.writer(text, lineterminator="\n")
    writer.writerow(rows.columns)
    for row in rows.rows:
        writer.writerow(["" if cell is None else _csv_cell(cell) for cell in row])
    return text.getvalue()


def _csv_cell(cell: object) -> object:
    if isinstance(cell, float):
        return "NaN" if math.isnan(cell) else repr(cell)
    return cell
