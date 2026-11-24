"""A `DatasetStore` over a firm's SQL databases, declared as data (decision 0032).

A firm's data sits in a warehouse, an order-management database, and a lake. Each dataset is a
`SqlDataset` (an id, the SQL, and a shape); this store runs it against the named source,
shapes the rows (pivoting long rows wide), writes them as CSV, and parses that with the
platform's own parser, so a dataset here gets the checks of `qaos data add` and its fingerprint
is the SHA-256 of the same CSV that a file with the same cells would have.

It needs the toolbox's ``sql`` extra and the database's own driver, which is the firm's install.
Importing this module needs Quant Toolbox (numpy); `quant_platform` loads it only when asked.

A source is a SQLAlchemy engine (`quant_toolbox.data.sql.connect`). What changed in a source is
answered by its *stamp*, so a cached dataset is rebuilt only when it must be:

- a ``str`` is SQL returning one value that changes with the data (a version table, a
  ``MAX(loaded_at)``), run before each read;
- a number is a time to live in seconds, after which the source is read again (the default is
  `DEFAULT_TTL_SECONDS`; 0 reads every time);
- a callable returns any hashable value (a file's mtime, a Delta table version).

A dataset that no SQL can build, such as one that joins two sources and carries a rule, is a
*derived* dataset: a Python function given a ``fetch(source, sql, params)`` and returning
`Rows`. It is the exception, named in ``derived`` so a reader sees where fund logic lives.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Hashable, Mapping, Sequence
from typing import TYPE_CHECKING, Protocol

from quant_agent_os.data import Dataset, UnknownDatasetError, parse_dataset
from quant_toolbox.data import Rows, SqlDataset, SqlError, fetch_rows, rows_to_csv, shape_rows

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

DEFAULT_TTL_SECONDS = 60.0
Stamp = str | int | float | Callable[[], Hashable]


class Fetch(Protocol):
    """What a derived dataset reads with: SQL on a named source, as normalised `Rows`."""

    def __call__(self, source: str, sql: str, params: Mapping[str, object] | None = None) -> Rows:
        """Run ``sql`` with ``:name`` binds on source ``source``."""
        ...


Derived = Callable[[Fetch], Rows]


class DatasetReadError(LookupError):
    """A dataset the caller may read could not be read: its columns differ, or its query failed.

    The message names the dataset and never its SQL or the driver's text, which can name other
    tables. A missing column is named with the columns the result has. A caller sees it only for a
    dataset the caller may already read, since a refused one answers as missing (decision 0021).
    """


def _database_errors() -> tuple[type[BaseException], ...]:
    """The errors a driver raises through SQLAlchemy; the `sql` extra is installed to get here."""
    from sqlalchemy.exc import DBAPIError

    return (DBAPIError,)


def _check_declared(item: SqlDataset, rows: Rows) -> None:
    """Raise `DatasetReadError` if the result lacks a column the declaration names."""
    expected: tuple[str, ...]
    if item.shape == "long" and item.index and item.column and item.value:
        expected = (item.index, item.column, item.value)
    elif item.shape == "wide" and item.index:
        expected = (item.index,)
    else:
        return
    missing = [name for name in expected if name.lower() not in rows.columns]
    if missing:
        listed = ", ".join(f"'{name}'" for name in missing)
        noun = "column" if len(missing) == 1 else "columns"
        raise DatasetReadError(
            f"Dataset '{item.id}' has no {noun} {listed}, which its declaration names; its columns "
            f"are {', '.join(rows.columns)}."
        )


class SqlDatasetStore:
    """Datasets read from SQL databases, cached until a source's stamp changes."""

    def __init__(
        self,
        sources: Mapping[str, Engine],
        datasets: Sequence[SqlDataset],
        *,
        derived: Mapping[str, Derived] | None = None,
        stamps: Mapping[str, Stamp] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Declare the store; nothing is read until a dataset is.

        ``sources`` names the engines. A dataset's ``source`` may be omitted when there is one.
        ``derived`` maps a dataset id to its Python builder. ``stamps`` maps a source name to its
        stamp. Raises `ValueError` for a repeated id, an unknown source, or a derived id that is
        also declared.
        """
        self.sources = dict(sources)
        self.datasets = {item.id: item for item in datasets}
        self.derived = dict(derived or {})
        self.stamps = dict(stamps or {})
        if len(self.datasets) != len(datasets):
            raise ValueError("Two datasets share an id.")
        if clash := sorted(set(self.derived) & set(self.datasets)):
            raise ValueError(f"{', '.join(clash)} is both declared and derived.")
        if unknown := sorted(set(self.stamps) - set(self.sources)):
            raise ValueError(f"A stamp names no source: {', '.join(unknown)}.")
        for item in self.datasets.values():
            for name in (self._source_of(item), item.calendar_source or self._source_of(item)):
                if name not in self.sources:
                    raise ValueError(
                        f"Dataset '{item.id}' reads source '{name}'; the sources are "
                        f"{', '.join(self.sources)}."
                    )
        self._clock = clock
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[tuple[Hashable, ...], Dataset]] = {}
        self._ttl: dict[str, tuple[float, int]] = {}

    def ids(self) -> list[str]:
        """Return every dataset id, sorted."""
        return sorted([*self.datasets, *self.derived])

    def get(self, dataset_id: str) -> Dataset:
        """Return dataset ``dataset_id``, or raise `UnknownDatasetError` naming the others."""
        if dataset_id not in self.datasets and dataset_id not in self.derived:
            raise UnknownDatasetError(
                f"No dataset '{dataset_id}'; the datasets are {', '.join(self.ids())}."
            )
        used = self._sources_used(dataset_id)
        try:
            stamp = tuple(self._stamp(name) for name in used)
            with self._lock:
                cached = self._cache.get(dataset_id)
            if cached is not None and cached[0] == stamp:
                return cached[1]
            text = rows_to_csv(self._rows(dataset_id))
        except _database_errors() as error:  # a failed read is never cached
            raise DatasetReadError(
                f"Dataset '{dataset_id}' could not be read: the database could not run its query. "
                "A column the query names may have been renamed or removed, or the database may "
                "be unavailable."
            ) from error
        dataset = parse_dataset(dataset_id, text.encode("utf-8"))
        with self._lock:
            self._cache[dataset_id] = (stamp, dataset)
        return dataset

    # ---- reading --------------------------------------------------------------------------

    def _rows(self, dataset_id: str) -> Rows:
        if dataset_id in self.derived:
            return self.derived[dataset_id](self._fetch)
        item = self.datasets[dataset_id]
        source = self._source_of(item)
        rows = self._fetch(source, item.sql, item.params_for(item.sql))
        calendar = None
        if item.calendar is not None:
            calendar = self._fetch(
                item.calendar_source or source, item.calendar, item.params_for(item.calendar)
            )
        _check_declared(item, rows)
        try:
            return shape_rows(item, rows, calendar)
        except SqlError as error:
            raise SqlError(f"Dataset '{dataset_id}': {error}") from error

    def _fetch(self, source: str, sql: str, params: Mapping[str, object] | None = None) -> Rows:
        if source not in self.sources:
            raise UnknownDatasetError(
                f"No source '{source}'; the sources are {', '.join(self.sources)}."
            )
        return fetch_rows(self.sources[source], sql, params)

    def _source_of(self, item: SqlDataset) -> str:
        if item.source is not None:
            return item.source
        if len(self.sources) == 1:
            return next(iter(self.sources))
        raise ValueError(f"Dataset '{item.id}' names no source, and there are several.")

    def _sources_used(self, dataset_id: str) -> list[str]:
        if dataset_id in self.derived:  # a derived dataset may read any source
            return sorted(self.sources)
        item = self.datasets[dataset_id]
        return sorted({self._source_of(item), item.calendar_source or self._source_of(item)})

    # ---- change stamps --------------------------------------------------------------------

    def _stamp(self, source: str) -> Hashable:
        stamp = self.stamps.get(source, DEFAULT_TTL_SECONDS)
        if isinstance(stamp, str):
            rows = self._fetch(source, stamp)
            return rows.rows[0][0] if rows.rows else None
        if isinstance(stamp, int | float):
            return self._generation(source, float(stamp))
        return stamp()

    def _generation(self, source: str, ttl: float) -> int:
        """Count the time-to-live periods of ``source`` since the first read, from the clock."""
        now = self._clock()
        with self._lock:
            started, generation = self._ttl.get(source, (now, 0))
            if now - started >= ttl:
                started, generation = now, generation + 1
            self._ttl[source] = (started, generation)
        return generation
