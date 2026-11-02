"""Datasets: numeric tables that tools read by id, so numbers never pass through a model.

A dataset is a CSV file with a header row. The first column is an index (dates, say) when its
values are not all numbers; every other column must be numeric. An operator adds datasets with
``quant-agent-os dataset push`` (a write, so it is a command, not a tool), and tools name them
by id. Each dataset's fingerprint is the SHA-256 of its file, recorded as a data version so a
result says exactly which data it came from.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
import shutil
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

DATASET_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_ROWS = 200_000
MAX_COLUMNS = 500


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


class Dataset(BaseModel):
    """A dataset's values: numeric columns aligned by row, with an optional text index."""

    id: str
    columns: dict[str, list[float]]
    index_name: str | None = None
    index: list[str] | None = None
    fingerprint: str

    @property
    def rows(self) -> int:
        """The number of rows."""
        return len(next(iter(self.columns.values()), []))

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
        )


def check_dataset_id(dataset_id: str) -> str:
    """Return ``dataset_id`` or raise `DatasetError` describing a valid id."""
    if not DATASET_ID.fullmatch(dataset_id):
        raise DatasetError(
            f"Dataset id '{dataset_id}' must be lowercase letters, digits, '.', '_', or '-', "
            "start with a letter or digit, and be at most 64 characters."
        )
    return dataset_id


def parse_dataset(dataset_id: str, data: bytes) -> Dataset:
    """Parse CSV ``data`` into a `Dataset`, or raise `DatasetError` naming the bad row."""
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
    has_index = not all(_is_number(row[0]) for row in body)
    names = header[1:] if has_index else header
    columns: dict[str, list[float]] = {name: [] for name in names}
    for number, row in enumerate(body, start=2):
        for name, cell in zip(names, row[1:] if has_index else row, strict=True):
            value = _number(cell)
            if value is None:
                raise DatasetError(
                    f"Dataset '{dataset_id}' line {number}, column '{name}': '{cell}' is not a "
                    "finite number."
                )
            columns[name].append(value)
    if not columns:
        raise DatasetError(f"Dataset '{dataset_id}' has no numeric column.")
    return Dataset(
        id=dataset_id,
        columns=columns,
        index_name=header[0] if has_index else None,
        index=[row[0].strip() for row in body] if has_index else None,
        fingerprint=hashlib.sha256(data).hexdigest(),
    )


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

    def put(self, dataset_id: str, source: Path) -> DatasetInfo:
        """Validate ``source`` and copy it in as ``dataset_id``, replacing any earlier version."""
        dataset = parse_dataset(check_dataset_id(dataset_id), source.read_bytes())
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{dataset_id}.csv"
        partial = target.with_suffix(".csv.partial")
        shutil.copyfile(source, partial)
        partial.replace(target)
        return dataset.info()

    def get(self, dataset_id: str) -> Dataset:
        """Return dataset ``dataset_id``, or raise `UnknownDatasetError` naming the others."""
        path = self.root / f"{check_dataset_id(dataset_id)}.csv"
        if not path.is_file():
            known = self.ids()
            raise UnknownDatasetError(
                f"No dataset '{dataset_id}'; "
                + (f"the datasets are {', '.join(known)}." if known else "none have been added.")
            )
        return parse_dataset(dataset_id, path.read_bytes())

    def ids(self) -> list[str]:
        """Return every dataset id, sorted."""
        if not self.root.is_dir():
            return []
        return sorted(
            path.stem for path in self.root.glob("*.csv") if DATASET_ID.fullmatch(path.stem)
        )


def _number(cell: str) -> float | None:
    try:
        value = float(cell.strip())
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _is_number(cell: str) -> bool:
    return _number(cell) is not None
