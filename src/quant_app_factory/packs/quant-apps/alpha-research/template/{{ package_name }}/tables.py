"""The files applications exchange: one table per file, a date column, then numeric columns.

Signals, weights, returns, positions, and prices all use this shape, so one application's output
is the next one's input, and a firm can replace any stage with a system that writes the same
file. `save_table` also writes ``<file>.meta.json``, recording what made the file and from what.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .metrics import FloatArray

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class TableError(ValueError):
    """Raised when a table cannot be used; the message says what to fix."""


@dataclass(frozen=True)
class Table:
    """``values`` for ``columns`` at ``dates``, with missing values as NaN."""

    dates: list[dt.date]
    columns: list[str]
    values: FloatArray

    def __post_init__(self) -> None:
        if self.values.shape != (len(self.dates), len(self.columns)):
            raise TableError("A table needs one row per date and one column per name.")
        if not self.dates:
            raise TableError("A table needs at least one date.")
        if len(set(self.columns)) != len(self.columns):
            raise TableError("Column names must be unique.")
        for earlier, later in zip(self.dates, self.dates[1:], strict=False):
            if later <= earlier:
                raise TableError(f"Dates must be unique and ascending: {later} follows {earlier}.")

    def column(self, name: str) -> FloatArray:
        """Return one column by name."""
        if name not in self.columns:
            raise TableError(f"No column {name!r}; the table has {', '.join(self.columns)}.")
        return self.values[:, self.columns.index(name)]

    def until(self, last: dt.date) -> Table:
        """Return the rows up to and including ``last``."""
        keep = [index for index, day in enumerate(self.dates) if day <= last]
        if not keep:
            raise TableError(f"No rows on or before {last}; the table starts {self.dates[0]}.")
        return Table([self.dates[i] for i in keep], self.columns, self.values[keep])

    def select(self, names: list[str]) -> Table:
        """Return only the columns in ``names``, in that order."""
        columns = [self.column(name) for name in names]
        return Table(self.dates, names, np.column_stack(columns))

    def fingerprint(self) -> str:
        """SHA-256 of the dates, columns, and values: changing any of them changes it."""
        digest = hashlib.sha256()
        digest.update("\n".join(day.isoformat() for day in self.dates).encode())
        digest.update(b"\0" + "\n".join(self.columns).encode() + b"\0")
        canonical = np.where(np.isfinite(self.values), self.values, np.nan)  # one NaN pattern
        digest.update(np.ascontiguousarray(canonical, dtype="<f8").tobytes())
        return digest.hexdigest()


Loader = Callable[[Path], Table]


def load_table(path: Path, loaders: Mapping[str, Loader] | None = None) -> Table:
    """Load ``path``: by a firm's loader for its suffix if there is one, otherwise as CSV."""
    if not path.is_file():
        raise TableError(f"No file at {path}.")
    loader = (loaders or {}).get(path.suffix.lower())
    if loader is not None:
        return loader(path)
    if path.suffix.lower() == ".csv":
        return load_csv(path)
    raise TableError(
        f"Cannot read {path.name}; use a .csv file, or add a loader for "
        f"{path.suffix or 'this file'} in firm/loaders.py."
    )


def load_csv(path: Path) -> Table:
    """Read a header ``date,NAME1,NAME2,...`` and one row per date."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = [row for row in csv.reader(handle) if any(cell.strip() for cell in row)]
    if len(rows) < 2 or len(rows[0]) < 2:
        raise TableError(f"{path} needs a header with a date column and at least one row.")
    columns = [name.strip() for name in rows[0][1:]]
    values = []
    for number, row in enumerate(rows[1:], 2):
        if len(row) != len(rows[0]):
            raise TableError(f"{path}, line {number}: expected {len(rows[0])} cells.")
        values.append([_number(cell, path, number) for cell in row[1:]])
    dates = [parse_date(row[0]) for row in rows[1:]]
    return Table(dates, columns, np.array(values, dtype=np.float64))


def save_table(
    path: Path, table: Table, *, made_by: str, inputs: Mapping[str, str] | None = None
) -> Path:
    """Write ``table`` as CSV with its provenance beside it, and return ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["date", *table.columns])
        for day, row in zip(table.dates, table.values, strict=True):
            writer.writerow(
                [day.isoformat(), *("" if math.isnan(v) else repr(float(v)) for v in row)]
            )
    meta = {
        "made_by": made_by,
        "created": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "fingerprint": f"sha256:{table.fingerprint()}",
        "inputs": dict(inputs or {}),
    }
    meta_path(path).write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", "utf-8")
    return path


def read_meta(path: Path) -> dict[str, Any]:
    """Return the provenance saved beside ``path``, or an empty dict when there is none."""
    try:
        meta: dict[str, Any] = json.loads(meta_path(path).read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return meta


def meta_path(path: Path) -> Path:
    """Return where `save_table` keeps the provenance of ``path``."""
    return path.with_name(path.name + ".meta.json")


def parse_date(value: Any) -> dt.date:
    """Read a date: a ``date``, a midnight ``datetime``, or ISO text such as ``2024-01-09``."""
    if isinstance(value, dt.datetime):
        if value.time() != dt.time():
            raise TableError(f"{value} has a time of day; use one row per date.")
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    if _ISO_DATE.fullmatch(text):
        try:
            return dt.date.fromisoformat(text)
        except ValueError:
            pass
    raise TableError(
        f"{text!r} is not an ISO date; write dates as YYYY-MM-DD, or add a loader in "
        "firm/loaders.py."
    )


def _number(cell: str, path: Path, line: int) -> float:
    text = cell.strip()
    if text == "":
        return math.nan
    try:
        return float(text)
    except ValueError:
        raise TableError(f"{path}, line {line}: {text!r} is not a number.") from None
