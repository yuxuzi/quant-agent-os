"""Kestrel Point Capital's warehouse as a `DatasetStore`: long tables read in place, served wide.

The warehouse is long and normalised (one row per date, instrument, and value); the platform's
datasets are wide (one row per date, one column per instrument). This adapter pivots each table
into the dataset ids `facility/access.toml` names, when a tool asks for one, and never writes to
the warehouse. Each dataset goes through the platform's own CSV parser, so it gets the same checks
as a file added with `qaos data add`, and its fingerprint is the SHA-256 of that CSV: it changes
exactly when the data a tool read changes.

Conventions, so every tool reads the same thing:
- Dated datasets run from the warehouse's first business day to its last, oldest first, with one
  row per day for every instrument, so no instrument looks delisted. The first day has no return,
  so its return cells are missing values (NaN), never zero.
- Returns are simple daily total returns as fractions (0.01 is 1%), as the warehouse holds them.
- `desk_returns_<desk>` is the desk's gross daily return: the previous day's end-of-day weights
  times each instrument's total return, summed; commissions are not subtracted.
- The vendor model is its latest month-end snapshot: `fm_loadings` (instruments by factors, with
  the zero loadings the vendor omits written as 0), `fm_factor_cov` (factors by factors, annual),
  and `fm_specific` (instruments, one column `specific_var_annual`, annual).
"""

from __future__ import annotations

import csv
import io
import sqlite3
import threading
from collections.abc import Callable, Iterable, Sequence
from contextlib import closing
from itertools import pairwise
from pathlib import Path

from quant_agent_os.data import Dataset, UnknownDatasetError, parse_dataset

MARKET = ("px_returns", "px_close", "bench_returns", "adv_usd")
VENDOR_MODEL = ("fm_loadings", "fm_factor_cov", "fm_specific")
SIGNALS = ("momentum", "carry", "value")
PER_DESK = ("positions", "weights", "desk_returns")

Cell = float | None


class WarehouseDatasetStore:
    """The warehouse's tables as wide datasets, read-only, rebuilt when the file changes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._stamp: tuple[int, ...] | None = None
        self._cache: dict[str, Dataset] = {}
        self._desks: list[str] | None = None

    def ids(self) -> list[str]:
        """Return every dataset id the warehouse serves, sorted."""
        desks = self.desks()
        per_desk = [f"{kind}_{desk}" for kind in PER_DESK for desk in desks]
        return sorted([*MARKET, *VENDOR_MODEL, *(f"signals_{s}" for s in SIGNALS), *per_desk])

    def desks(self) -> list[str]:
        """Return the desks that hold positions."""
        self._check_stamp()
        if self._desks is None:
            with closing(self._connect()) as db:
                self._desks = [row[0] for row in db.execute(_DESKS)]
        return self._desks

    def get(self, dataset_id: str) -> Dataset:
        """Return dataset ``dataset_id``, or raise `UnknownDatasetError` naming the others."""
        if dataset_id not in self.ids():
            raise UnknownDatasetError(
                f"No dataset '{dataset_id}'; the datasets are {', '.join(self.ids())}."
            )
        with self._lock:
            cached = self._cache.get(dataset_id)
        if cached is not None:
            return cached
        with closing(self._connect()) as db:
            text = self._build(db, dataset_id)
        dataset = parse_dataset(dataset_id, text.encode("utf-8"))
        with self._lock:
            self._cache[dataset_id] = dataset
        return dataset

    def _connect(self) -> sqlite3.Connection:
        # A connection per call, because tools run on a pool of threads; read-only, so the
        # platform can never change the warehouse.
        if not self.path.is_file():
            raise UnknownDatasetError(f"The warehouse {self.path} is missing.")
        return sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)

    def _check_stamp(self) -> None:
        """Forget every built dataset when the warehouse file (or its journal) changes."""
        stamp: list[int] = []
        for suffix in ("", "-wal"):
            try:
                info = Path(f"{self.path}{suffix}").stat()
            except OSError:
                continue
            stamp += [info.st_ino, info.st_size, info.st_mtime_ns]
        with self._lock:
            if tuple(stamp) != self._stamp:
                self._stamp = tuple(stamp)
                self._cache.clear()
                self._desks = None

    def _build(self, db: sqlite3.Connection, dataset_id: str) -> str:
        dates = [row[0] for row in db.execute("SELECT DISTINCT date FROM mkt_px_daily ORDER BY 1")]
        kind, _, name = dataset_id.partition("_")
        if dataset_id in PIVOTS:
            sql = PIVOTS[dataset_id]
            return _pivot(db, None if dataset_id == "adv_usd" else dates, sql)
        if dataset_id in VENDOR_BUILDERS:
            return VENDOR_BUILDERS[dataset_id](db)
        if kind == "signals":
            return _pivot(db, dates, _SIGNAL, (name,))
        if dataset_id.startswith("desk_returns_"):
            return _desk_returns(db, dates, dataset_id.removeprefix("desk_returns_"))
        column = "market_value" if kind == "positions" else "weight"
        return _pivot(db, dates, _POSITIONS.format(column=column), (name,))


_DESKS = "SELECT DISTINCT desk FROM pos_eod ORDER BY 1"
_SIGNAL = "SELECT date, instrument_id, value FROM sig_daily WHERE signal_name = ?"
_POSITIONS = "SELECT date, instrument_id, {column} FROM pos_eod WHERE desk = ?"
PIVOTS = {
    "px_returns": "SELECT date, instrument_id, total_return FROM mkt_px_daily",
    "px_close": "SELECT date, instrument_id, close FROM mkt_px_daily",
    "bench_returns": "SELECT date, benchmark_id, total_return FROM bench_daily",
    "adv_usd": "SELECT date, instrument_id, adv_usd FROM mkt_adv",
}


def _csv(header: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    text = io.StringIO()
    writer = csv.writer(text, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(["NaN" if cell is None else _number(cell) for cell in row])
    return text.getvalue()


def _number(cell: object) -> object:
    return repr(cell) if isinstance(cell, float) else cell


def _pivot(
    db: sqlite3.Connection,
    dates: list[str] | None,
    sql: str,
    params: tuple[object, ...] = (),
) -> str:
    """Pivot (date, column, value) rows into a dated table: every date in ``dates`` (or every
    date the rows hold), every column, and a missing value wherever the warehouse has none."""
    values: dict[tuple[str, str], Cell] = {}
    for day, name, value in db.execute(sql, params):
        values[day, name] = value
    columns = sorted({name for _, name in values})
    days = dates if dates is not None else sorted({day for day, _ in values})
    rows = ([day, *(values.get((day, name)) for name in columns)] for day in days)
    return _csv(["date", *columns], rows)


def _desk_returns(db: sqlite3.Connection, dates: list[str], desk: str) -> str:
    weights: dict[tuple[str, str], float] = {
        (day, name): weight
        for day, name, weight in db.execute(
            "SELECT date, instrument_id, weight FROM pos_eod WHERE desk = ?", (desk,)
        )
    }
    returns: dict[tuple[str, str], float] = {
        (day, name): value
        for day, name, value in db.execute(
            "SELECT date, instrument_id, total_return FROM mkt_px_daily "
            "WHERE total_return IS NOT NULL"
        )
    }
    names = sorted({name for _, name in weights})
    rows: list[list[object]] = [[dates[0], None]]
    for before, day in pairwise(dates):
        terms = [weights.get((before, n), 0.0) * returns.get((day, n), 0.0) for n in names]
        rows.append([day, sum(terms)])
    return _csv(["date", "gross_return"], rows)


def _model_date(db: sqlite3.Connection) -> str:
    row = db.execute("SELECT MAX(as_of_date) FROM fm_loadings").fetchone()
    if row is None or row[0] is None:
        raise UnknownDatasetError("The warehouse holds no vendor factor model.")
    return str(row[0])


def _vendor_loadings(db: sqlite3.Connection) -> str:
    model_date = _model_date(db)
    factors = [row[0] for row in db.execute("SELECT factor_id FROM fm_factors ORDER BY rowid")]
    loadings = {
        (name, factor): value
        for name, factor, value in db.execute(
            "SELECT instrument_id, factor_id, loading FROM fm_loadings WHERE as_of_date = ?",
            (model_date,),
        )
    }
    names = [row[0] for row in db.execute("SELECT instrument_id FROM ref_instruments ORDER BY 1")]
    rows = ([name, *(loadings.get((name, f), 0.0) for f in factors)] for name in names)
    return _csv(["instrument_id", *factors], rows)


def _vendor_covariance(db: sqlite3.Connection) -> str:
    model_date = _model_date(db)
    factors = [row[0] for row in db.execute("SELECT factor_id FROM fm_factors ORDER BY rowid")]
    cov = {
        (a, b): value
        for a, b, value in db.execute(
            "SELECT factor_id_1, factor_id_2, cov_annual FROM fm_factor_cov WHERE as_of_date = ?",
            (model_date,),
        )
    }
    rows = ([a, *(cov.get((a, b)) for b in factors)] for a in factors)
    return _csv(["factor_id", *factors], rows)


def _vendor_specific(db: sqlite3.Connection) -> str:
    model_date = _model_date(db)
    rows = db.execute(
        "SELECT instrument_id, specific_var_annual FROM fm_specific WHERE as_of_date = ? "
        "ORDER BY 1",
        (model_date,),
    ).fetchall()
    return _csv(["instrument_id", "specific_var_annual"], rows)


VENDOR_BUILDERS: dict[str, Callable[[sqlite3.Connection], str]] = {
    "fm_loadings": _vendor_loadings,
    "fm_factor_cov": _vendor_covariance,
    "fm_specific": _vendor_specific,
}
