"""Larchmere Loan Partners' data as a `DatasetStore`: declared datasets over Azure SQL and the lake.

Every dataset is one declarative entry in `DATASETS`: an id, the SQL it runs (in the source's own
dialect, with `?` parameters), and its shape: long `(date, key, value)` rows to pivot wide, or a
table read as it is. Only the entries marked `DERIVED` need Python. Connection setup is in one
function per source (`open_sql`, `open_lake`), taking its secret from the environment as Key Vault
would inject it.

Sources:
- ``sql``: Azure SQL Database, schema ``clo`` (views ``clo.v_*``). Offline it is a SQLite file
  attached as ``clo``; production would be pyodbc with ``Authentication=ActiveDirectoryMsi``.
- ``lake``: curated Parquet on ADLS Gen2 (``abfss://curated@larchmereadls.dfs.core.windows.net``),
  read with DuckDB over ``read_parquet(..., hive_partitioning=true)``. A catalog file maps each
  abfss:// URI to a directory.

Conventions: dataset ids are lowercase, a deal's data ends in its slug (``larch_2021_1``), dates are
trustee report dates (month-end) unless the id says daily, prices are points of par, par is USD,
and a loan a deal does not hold on a date is a missing value, never zero.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import threading
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from quant_agent_os.data import Dataset, UnknownDatasetError, parse_dataset

DEALS = ("LARCH-2021-1", "LARCH-2018-2")
SQL_ENV = "LARCHMERE_SQL_CONNECTION"  # a Key Vault reference in production; here a file path
LAKE_ENV = "LARCHMERE_LAKE_CATALOG"


def slug(deal_id: str) -> str:
    """Return a deal id as it appears in dataset ids: ``larch_2021_1``."""
    return deal_id.lower().replace("-", "_")


@dataclass(frozen=True)
class Entry:
    """One dataset: where it comes from, its SQL, and how its rows become a table."""

    source: Literal["sql", "lake"]
    query: str
    shape: Literal["long", "table"]  # long: (index, key, value) rows; table: index + columns
    deal: str | None = None  # every ``?`` in the query is bound to this deal id
    derived: Callable[[Any, Any, str], str] | None = None  # DERIVED: Python builds the CSV


# ---- connection: the only place a connection is made -------------------------------------------


def open_sql(connection: str | None = None) -> sqlite3.Connection:
    """Open the SQL source read-only, with the deal database attached as schema ``clo``.

    Production: ``pyodbc.connect(<connection string from Key Vault>, attrs_before=<Entra token>)``,
    and the views are already ``clo.v_*``.
    """
    path = Path(connection or os.environ[SQL_ENV])
    if not path.is_file():
        raise UnknownDatasetError(f"The deal database {path} is missing.")
    db = sqlite3.connect(":memory:", uri=True)
    db.execute("ATTACH DATABASE ? AS clo", (f"{path.resolve().as_uri()}?mode=ro",))
    return db


def open_lake(catalog: Path | None = None) -> Any:
    """Open the lake with DuckDB: one view per curated table, over its Parquet folders."""
    import duckdb  # inside the function: the platform's wheel does not ship it

    path = catalog or Path(os.environ[LAKE_ENV])
    description = json.loads(path.read_text())
    root = path.parent.parent
    lake = duckdb.connect(":memory:")
    for table, meta in description["tables"].items():
        location = meta["location"].rstrip("/")
        for uri, directory in description["uris"].items():
            if location.startswith(uri):
                folder = root / directory / location.removeprefix(uri).strip("/")
                break
        else:
            raise UnknownDatasetError(f"No directory is mapped for {location}.")
        suffix = "parquet" if description["format"] == "parquet" else "csv"
        reader = "read_parquet" if suffix == "parquet" else "read_csv_auto"
        lake.execute(
            f"CREATE VIEW {table} AS SELECT * FROM {reader}('{folder}/**/*.{suffix}', "
            "hive_partitioning=true)"
        )
    return lake


# ---- declared datasets ---------------------------------------------------------------------------

HOLD = "SELECT report_date, loan_id, {col} FROM clo.v_holdings WHERE deal_id = ?"
STRUCTURE = " UNION ALL ".join(
    [
        "SELECT report_date, CONCAT('bal_', tranche), balance FROM clo.v_structure "
        "WHERE deal_id = ?",
        "SELECT report_date, CONCAT('spread_', tranche), spread_bps FROM clo.v_structure "
        "WHERE deal_id = ? AND spread_bps IS NOT NULL",
        "SELECT report_date, CONCAT('oc_trig_', tranche), oc_trigger FROM clo.v_structure "
        "WHERE deal_id = ? AND oc_trigger IS NOT NULL",
        "SELECT report_date, CONCAT('ic_trig_', tranche), ic_trigger FROM clo.v_structure "
        "WHERE deal_id = ? AND ic_trigger IS NOT NULL",
        "SELECT report_date, 'principal_cash', principal_cash FROM clo.v_cash WHERE deal_id = ?",
        "SELECT report_date, 'interest_cash', interest_cash FROM clo.v_cash WHERE deal_id = ?",
        "SELECT c.report_date, CONCAT('limit_', l.test_name), l.level FROM clo.v_cash c "
        "JOIN clo.v_test_limit l ON l.deal_id = c.deal_id WHERE c.deal_id = ?",
    ]
)
RETURNS = (
    "SELECT report_date, loan_id, market_price / LAG(market_price) OVER "
    "(PARTITION BY loan_id ORDER BY report_date) - 1 FROM clo.v_holdings WHERE deal_id = ?"
)


def _equity_cashflows(sql: Any, lake: Any, deal: str) -> str:
    """DERIVED: dated equity flows from two sources: invested (first NAV), distributions (lake
    payment dates), terminal NAV (last NAV). No single query gives it."""
    navs = sql.execute(
        "SELECT report_date, nav FROM clo.v_equity_nav WHERE deal_id = ? ORDER BY report_date",
        (deal,),
    ).fetchall()
    paid = lake.execute(
        "SELECT CAST(dt AS VARCHAR), interest_paid FROM tranche_cashflows "
        "WHERE deal_id = ? AND tranche = 'EQ' ORDER BY dt",
        [deal],
    ).fetchall()
    rows: dict[str, list[float]] = {navs[0][0]: [navs[0][1], 0.0, 0.0]}
    for day, amount in paid:
        rows.setdefault(day, [0.0, 0.0, 0.0])[1] += amount
    rows.setdefault(navs[-1][0], [0.0, 0.0, 0.0])[2] = navs[-1][1]
    return _csv(
        ["date", "invested", "distribution", "terminal_nav"],
        ([d, *v] for d, v in sorted(rows.items())),
    )


def _entries() -> dict[str, Entry]:
    out: dict[str, Entry] = {
        "loan_static": Entry("sql", "SELECT * FROM clo.v_loan_static ORDER BY loan_id", "table"),
        "rf_all": Entry(
            "sql",
            "SELECT report_date, loan_id, rating_factor FROM clo.v_loan_rating_factor",
            "long",
        ),
        "sofr": Entry("lake", "SELECT rate_date, 'sofr_pct', sofr_pct FROM sofr", "long"),
        "loan_price_daily": Entry(
            "lake", "SELECT price_date, loan_id, price FROM loan_prices", "long"
        ),
        "loan_return_daily": Entry(
            "lake",
            "SELECT price_date, loan_id, price / LAG(price) OVER (PARTITION BY loan_id "
            "ORDER BY price_date) - 1 FROM loan_prices",
            "long",
        ),
    }
    for deal in DEALS:
        key = slug(deal)
        out[f"par_{key}"] = Entry("sql", HOLD.format(col="par"), "long", deal)
        out[f"price_{key}"] = Entry("sql", HOLD.format(col="market_price"), "long", deal)
        out[f"discount_{key}"] = Entry("sql", HOLD.format(col="100 - market_price"), "long", deal)
        out[f"loan_return_monthly_{key}"] = Entry("sql", RETURNS, "long", deal)
        out[f"structure_{key}"] = Entry("sql", STRUCTURE, "long", deal)
        out[f"equity_returns_{key}"] = Entry(
            "sql",
            "SELECT report_date, 'equity', monthly_return FROM clo.v_equity_nav WHERE deal_id = ?",
            "long",
            deal,
        )
        out[f"equity_cf_{key}"] = Entry("sql", "", "table", deal, derived=_equity_cashflows)
    return out


DATASETS = _entries()


# ---- pivot and shape ---------------------------------------------------------------------------


def _csv(header: Sequence[str], rows: Any) -> str:
    text = io.StringIO()
    writer = csv.writer(text, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(["NaN" if cell is None else _number(cell) for cell in row])
    return text.getvalue()


def _number(cell: object) -> object:
    return repr(cell) if isinstance(cell, float) else cell


def _pivot(rows: list[tuple[Any, ...]]) -> str:
    """Pivot (date, key, value) rows to a dated table: every date, every key, NaN where absent."""
    values = {(str(day)[:10], key): value for day, key, value in rows}
    keys = sorted({key for _, key in values})
    days = sorted({day for day, _ in values})
    return _csv(["date", *keys], ([d, *(values.get((d, k)) for k in keys)] for d in days))


class LarchmereDatasetStore:
    """The deals' data as wide, read-only datasets, rebuilt when a source changes."""

    def __init__(self, sql: Path, lake_catalog: Path) -> None:
        self.sql_path, self.catalog = sql, lake_catalog
        self._lock = threading.Lock()
        self._stamp: tuple[float, ...] | None = None
        self._cache: dict[str, Dataset] = {}

    def ids(self) -> list[str]:
        """Return every dataset id, sorted."""
        return sorted(DATASETS)

    def get(self, dataset_id: str) -> Dataset:
        """Return dataset ``dataset_id``, or raise `UnknownDatasetError` naming the others."""
        entry = DATASETS.get(dataset_id)
        if entry is None:
            raise UnknownDatasetError(
                f"No dataset '{dataset_id}'; the datasets are {', '.join(self.ids())}."
            )
        self._check_stamp()
        with self._lock:
            cached = self._cache.get(dataset_id)
        if cached is not None:
            return cached
        dataset = parse_dataset(dataset_id, self._build(entry).encode("utf-8"))
        with self._lock:
            self._cache[dataset_id] = dataset
        return dataset

    def _build(self, entry: Entry) -> str:
        with closing(open_sql(str(self.sql_path))) as sql:
            lake = open_lake(self.catalog)
            try:
                if entry.derived is not None:
                    assert entry.deal is not None
                    return entry.derived(sql, lake, entry.deal)
                params = (entry.deal,) * entry.query.count("?") if entry.deal else ()
                if entry.source == "sql":
                    cursor = sql.execute(entry.query, params)
                    rows, header = cursor.fetchall(), [c[0] for c in cursor.description]
                else:
                    result = lake.execute(entry.query, list(params))
                    rows, header = result.fetchall(), [c[0] for c in result.description]
            finally:
                lake.close()
        return _pivot(rows) if entry.shape == "long" else _csv(header, rows)

    def _check_stamp(self) -> None:
        stamp: list[float] = []
        for path in [self.sql_path, *self.catalog.parent.rglob("*.parquet")]:
            try:
                stamp.append(path.stat().st_mtime_ns)
            except OSError:
                continue
        with self._lock:
            if tuple(stamp) != self._stamp:
                self._stamp = tuple(stamp)
                self._cache.clear()
