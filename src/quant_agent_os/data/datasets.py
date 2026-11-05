"""Datasets: numeric tables that tools read by id, so numbers never pass through a model.

A dataset is a CSV file with a header row. The first column is its index when it is named like
a date (``date``, ``datetime``, ``time``, ``period``), when it holds compact dates (``20240102``,
``202401``), or when its values are not all numbers; ``--index`` names any other column. Every
other column must be numeric, and a blank or ``NaN`` cell is kept as a missing value (NaN), never
filled in. An operator adds datasets with ``qaos data add`` (a write, so it is a
command, not a tool), and tools name them by id. Each dataset's fingerprint is the SHA-256 of its
file, recorded as a data version so a result says exactly which data it came from.

A Parquet file (the ``parquet`` extra) is converted to CSV when it is added, so the stored file,
and so the fingerprint, is that CSV. From Python, `MemoryDatasetStore.put` takes a pandas object.
Both go through the same parser as a CSV file, so their dates, missing values, and limits are
treated the same, and nothing else here needs pandas.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
import statistics
from datetime import date, datetime
from functools import cached_property
from itertools import pairwise
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

DATASET_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_ROWS = 200_000
MAX_COLUMNS = 500
MAX_CACHED = 16
PARQUET_SUFFIXES = frozenset({".parquet", ".pq"})
INDEX_NAMES = frozenset({"date", "datetime", "time", "period"})
MISSING_CELLS = frozenset({"", "na", "n/a", "nan", "null", "none"})
# Median days between dates, and the periods per year they mean. Daily is decided separately.
FREQUENCIES = (((6, 8), 52), ((25, 35), 12), ((85, 95), 4), ((175, 190), 2), ((350, 380), 1))


class DatasetError(ValueError):
    """Raised when a dataset file is malformed or exceeds the limits."""


class UnknownDatasetError(LookupError):
    """Raised when no dataset has the requested id."""


class UnknownColumnError(LookupError):
    """Raised when a dataset has no column of the requested name."""


class DatasetInfo(BaseModel):
    """What a client needs to choose a dataset and its columns, without the values."""

    id: str
    rows: int
    columns: list[str]
    index_name: str | None = None
    first_index: str | None = None
    last_index: str | None = None
    fingerprint: str = Field(description="sha256 of the file; the dataset's data version")
    periods_per_year: int | None = Field(
        default=None, description="Inferred from the dates; None when they do not show it"
    )
    missing_cells: int = 0


class Dataset(BaseModel):
    """A dataset's values: numeric columns aligned by row (NaN when missing), with an index."""

    id: str
    columns: dict[str, list[float]]
    index_name: str | None = None
    index: list[str] | None = None
    fingerprint: str

    @property
    def rows(self) -> int:
        """The number of rows."""
        return len(next(iter(self.columns.values()), []))

    @cached_property
    def dates(self) -> list[date] | None:
        """The index as dates, or None when there is no index or a value is not a date."""
        if not self.index:
            return None
        dates = [parse_date(value) for value in self.index]
        return None if None in dates else dates  # type: ignore[return-value]

    @property
    def periods_per_year(self) -> int | None:
        """The frequency its dates show, or None when they show none."""
        return infer_periods_per_year(self.dates) if self.dates else None

    def column(self, name: str) -> list[float]:
        """Return the values of column ``name``, or raise `UnknownColumnError` naming the others."""
        if name not in self.columns:
            raise UnknownColumnError(
                f"Dataset '{self.id}' has no column '{name}'; its columns are "
                f"{', '.join(self.columns)}."
            )
        return self.columns[name]

    def info(self) -> DatasetInfo:
        """Return the dataset's description without its values."""
        return DatasetInfo(
            id=self.id,
            rows=self.rows,
            columns=list(self.columns),
            index_name=self.index_name,
            first_index=self.index[0] if self.index else None,
            last_index=self.index[-1] if self.index else None,
            fingerprint=self.fingerprint,
            periods_per_year=self.periods_per_year,
            missing_cells=sum(
                math.isnan(value) for values in self.columns.values() for value in values
            ),
        )


def check_dataset_id(dataset_id: str) -> str:
    """Return ``dataset_id`` or raise `DatasetError` describing a valid id."""
    if not DATASET_ID.fullmatch(dataset_id):
        raise DatasetError(
            f"Dataset id '{dataset_id}' must be lowercase letters, digits, '.', '_', or '-', "
            "start with a letter or digit, and be at most 64 characters."
        )
    return dataset_id


def parse_date(text: str) -> date | None:
    """Return the date ``text`` names, or None; day-first and month-first forms are ambiguous."""
    text = text.strip()
    if text.isdigit():
        if len(text) not in (6, 8):
            return None
        formats = ["%Y%m%d" if len(text) == 8 else "%Y%m"]
    else:
        formats = ["%Y/%m/%d", "%Y-%m"]
        try:
            return _plausible(datetime.fromisoformat(text).date())
        except ValueError:
            pass
    for pattern in formats:
        try:
            return _plausible(datetime.strptime(text, pattern).date())
        except ValueError:
            continue
    return None


def infer_periods_per_year(dates: list[date]) -> int | None:
    """Return the periods per year sorted ``dates`` show, or None when they show no clear one.

    Daily dates mean 252 periods, or 365 when weekends make up over a tenth of them.
    """
    if len(dates) < 3:
        return None
    median = statistics.median((later - earlier).days for earlier, later in pairwise(dates))
    if 1 <= median <= 4:
        weekends = sum(day.weekday() >= 5 for day in dates)
        return 365 if weekends > len(dates) / 10 else 252
    for (low, high), periods in FREQUENCIES:
        if low <= median <= high:
            return periods
    return None


def parse_dataset(dataset_id: str, data: bytes, *, index_column: str | None = None) -> Dataset:
    """Parse CSV ``data`` into a `Dataset`, or raise `DatasetError` naming the bad row.

    ``index_column`` names the index; without it, the first column is the index when it looks
    like one (see the module docstring).
    """
    if len(data) > MAX_FILE_BYTES:
        raise DatasetError(f"Dataset '{dataset_id}' exceeds {MAX_FILE_BYTES} bytes.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise DatasetError(f"Dataset '{dataset_id}' is not UTF-8 text.") from error
    rows = [row for row in csv.reader(io.StringIO(text)) if any(cell.strip() for cell in row)]
    if len(rows) < 3:
        raise DatasetError(f"Dataset '{dataset_id}' needs a header row and at least two rows.")
    header, body = [cell.strip() for cell in rows[0]], rows[1:]
    if len(body) > MAX_ROWS or len(header) > MAX_COLUMNS:
        raise DatasetError(
            f"Dataset '{dataset_id}' exceeds {MAX_ROWS} rows or {MAX_COLUMNS} columns."
        )
    if not all(header) or len(set(header)) != len(header):
        raise DatasetError(f"Dataset '{dataset_id}' needs a unique, non-empty name per column.")
    for number, row in enumerate(body, start=2):
        if len(row) != len(header):
            raise DatasetError(
                f"Dataset '{dataset_id}' line {number} has {len(row)} cells, not {len(header)}."
            )
    position = _index_position(dataset_id, header, body, index_column)
    names = [name for at, name in enumerate(header) if at != position]
    columns: dict[str, list[float]] = {name: [] for name in names}
    for number, row in enumerate(body, start=2):
        cells = [cell for at, cell in enumerate(row) if at != position]
        for name, cell in zip(names, cells, strict=True):
            value = _value(cell)
            if value is None:
                raise DatasetError(
                    f"Dataset '{dataset_id}' line {number}, column '{name}': '{cell}' is not a "
                    "number or a missing value."
                )
            columns[name].append(value)
    if not columns:
        raise DatasetError(f"Dataset '{dataset_id}' has no numeric column.")
    index = None if position is None else [row[position].strip() for row in body]
    dataset = Dataset(
        id=dataset_id,
        columns=columns,
        index_name=None if position is None else header[position],
        index=index,
        fingerprint=hashlib.sha256(data).hexdigest(),
    )
    if index is not None:
        _check_index(dataset_id, index, dataset.dates)
    return dataset


class DatasetStore(Protocol):
    """Where tools read datasets from."""

    def get(self, dataset_id: str) -> Dataset:
        """Return a dataset or raise `UnknownDatasetError`."""
        ...

    def ids(self) -> list[str]:
        """Return every dataset id, sorted."""
        ...


class DirectoryDatasetStore:
    """Datasets as ``<id>.csv`` files in one directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._parsed: dict[str, tuple[str | None, Dataset]] = {}

    def put(self, dataset_id: str, source: Path, *, index_column: str | None = None) -> DatasetInfo:
        """Validate ``source`` and store it as ``dataset_id``, replacing any earlier version.

        ``index_column`` names the index column and is kept beside the file. A CSV file stays
        exactly as given, so its fingerprint matches the source; a Parquet file is stored as CSV.
        """
        check_dataset_id(dataset_id)
        if source.suffix.lower() in PARQUET_SUFFIXES:
            data, own_index = _frame_csv(dataset_id, _read_parquet(dataset_id, source))
            index_column = index_column or own_index
        else:
            data = source.read_bytes()
        dataset = parse_dataset(dataset_id, data, index_column=index_column)
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{dataset_id}.csv"
        partial = target.with_suffix(".csv.partial")
        partial.write_bytes(data)
        partial.replace(target)
        marker = self._index_marker(dataset_id)
        if index_column is None:
            marker.unlink(missing_ok=True)
        else:
            marker.write_text(index_column, encoding="utf-8")
        return dataset.info()

    def get(self, dataset_id: str) -> Dataset:
        """Return dataset ``dataset_id``, or raise `UnknownDatasetError` naming the others."""
        path = self.root / f"{check_dataset_id(dataset_id)}.csv"
        if not path.is_file():
            raise _unknown_dataset(dataset_id, self.ids())
        data = path.read_bytes()
        marker = self._index_marker(dataset_id)
        index_column = marker.read_text(encoding="utf-8") if marker.is_file() else None
        cached_column, cached = self._parsed.get(dataset_id, (None, None))
        if (
            cached is not None
            and cached_column == index_column
            and cached.fingerprint == hashlib.sha256(data).hexdigest()
        ):
            return cached
        dataset = parse_dataset(dataset_id, data, index_column=index_column)
        if len(self._parsed) >= MAX_CACHED:
            self._parsed.pop(next(iter(self._parsed)), None)
        self._parsed[dataset_id] = (index_column, dataset)
        return dataset

    def ids(self) -> list[str]:
        """Return every dataset id, sorted."""
        if not self.root.is_dir():
            return []
        return sorted(
            path.stem for path in self.root.glob("*.csv") if DATASET_ID.fullmatch(path.stem)
        )

    def _index_marker(self, dataset_id: str) -> Path:
        return self.root / f"{dataset_id}.index"


class MemoryDatasetStore:
    """Datasets from pandas objects, kept for this session in front of an optional ``base`` store.

    Nothing is written to disk, and an id here hides the same id in ``base``. A result's data
    version is the fingerprint of the frame as CSV, so a run records which data it used, though
    only the session that made it can read that data again.
    """

    def __init__(self, base: DatasetStore | None = None) -> None:
        self.base = base
        self._datasets: dict[str, Dataset] = {}

    def put(self, dataset_id: str, frame: Any) -> DatasetInfo:
        """Validate a DataFrame or Series and keep a copy as ``dataset_id``, replacing any other.

        Its index is the dataset's index, except an unnamed row counter, which is no index at all:
        a date column must then come first. Raises `DatasetError` as for a CSV file, for example
        when a column is not numeric or the dates are out of order.
        """
        check_dataset_id(dataset_id)
        data, index_column = _frame_csv(dataset_id, frame)
        dataset = parse_dataset(dataset_id, data, index_column=index_column)
        self._datasets[dataset_id] = dataset
        return dataset.info()

    def get(self, dataset_id: str) -> Dataset:
        """Return dataset ``dataset_id``, or raise `UnknownDatasetError` naming the others."""
        if dataset_id in self._datasets:
            return self._datasets[dataset_id]
        if self.base is not None and dataset_id in self.base.ids():
            return self.base.get(dataset_id)
        raise _unknown_dataset(dataset_id, self.ids())

    def ids(self) -> list[str]:
        """Return every dataset id, here and in the base store, sorted."""
        below = self.base.ids() if self.base is not None else []
        return sorted({*self._datasets, *below})


def _unknown_dataset(dataset_id: str, known: list[str]) -> UnknownDatasetError:
    return UnknownDatasetError(
        f"No dataset '{dataset_id}'; "
        + (f"the datasets are {', '.join(known)}." if known else "none have been added.")
    )


def _read_parquet(dataset_id: str, source: Path) -> Any:
    """Return a Parquet file as a DataFrame, checking its size before decoding any of it."""
    if source.stat().st_size > MAX_FILE_BYTES:
        raise DatasetError(f"Dataset '{dataset_id}' exceeds {MAX_FILE_BYTES} bytes.")
    try:
        import pandas  # noqa: F401  # to_pandas needs it; a missing one is reported here
        import pyarrow
        from pyarrow import parquet
    except ImportError as error:
        raise DatasetError(
            f"Dataset '{dataset_id}' is a Parquet file, which needs the parquet extra: install "
            "quant-agent-os[parquet], or add it as CSV."
        ) from error
    try:
        shape = parquet.read_metadata(source)
        if shape.num_rows > MAX_ROWS or shape.num_columns > MAX_COLUMNS:
            raise DatasetError(
                f"Dataset '{dataset_id}' exceeds {MAX_ROWS} rows or {MAX_COLUMNS} columns."
            )
        return parquet.read_table(source).to_pandas()
    except pyarrow.ArrowException as error:
        raise DatasetError(f"Dataset '{dataset_id}' cannot be read as Parquet: {error}") from error


def _frame_csv(dataset_id: str, frame: Any) -> tuple[bytes, str | None]:
    """Return a pandas object as CSV, and the column holding its index if it has one."""
    if hasattr(frame, "to_frame") and hasattr(frame, "name"):
        frame = frame.to_frame(dataset_id if frame.name is None else frame.name)
    if not all(hasattr(frame, name) for name in ("to_csv", "index", "columns")):
        raise DatasetError(
            f"Dataset '{dataset_id}' must be a pandas DataFrame or Series, not "
            f"{type(frame).__name__}. Add a file with `qaos data add`."
        )
    index = frame.index
    if index.nlevels > 1 or frame.columns.nlevels > 1:
        raise DatasetError(
            f"Dataset '{dataset_id}' has several index or column levels; it needs one date index "
            "and one column per series. Flatten it first, with `reset_index` or `pivot`."
        )
    # Written out, because a line of blank cells would read as no row at all.
    missing = "NaN"
    row_numbers = index.name is None and index.dtype.kind in "iu"
    if row_numbers and not all(parse_date(str(value)) for value in index):
        return frame.to_csv(index=False, na_rep=missing).encode("utf-8"), None
    unnamed = "date" if index.dtype.kind == "M" else "index"
    label = unnamed if index.name is None else str(index.name)
    return frame.to_csv(index_label=label, na_rep=missing).encode("utf-8"), label


def _plausible(day: date) -> date | None:
    return day if 1800 <= day.year <= 2200 else None


def _index_position(
    dataset_id: str, header: list[str], body: list[list[str]], index_column: str | None
) -> int | None:
    if index_column is not None:
        if index_column not in header:
            raise DatasetError(
                f"Dataset '{dataset_id}' has no column '{index_column}' to use as its index; its "
                f"columns are {', '.join(header)}."
            )
        return header.index(index_column)
    first = [row[0].strip() for row in body]
    named = header[0].lower() in INDEX_NAMES
    compact = all(cell.isdigit() and parse_date(cell) is not None for cell in first)
    return 0 if named or compact or not all(_value(cell) is not None for cell in first) else None


def _check_index(dataset_id: str, index: list[str], dates: list[date] | None) -> None:
    if not all(index):
        line = index.index("") + 2
        raise DatasetError(f"Dataset '{dataset_id}' line {line} has no index value.")
    if len(set(index)) != len(index):
        raise DatasetError(
            f"Dataset '{dataset_id}' repeats index values; it needs one row per date with one "
            "column per series."
        )
    if dates is not None and dates != sorted(dates):
        raise DatasetError(
            f"Dataset '{dataset_id}' is not in date order; sort it oldest first, because returns "
            "compound in row order."
        )


def _value(cell: str) -> float | None:
    """Return a cell's number, NaN for a missing value, or None when it is neither."""
    text = cell.strip()
    if text.lower() in MISSING_CELLS:
        return math.nan
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None
