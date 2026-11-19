"""Wrenfield Credit Opportunities' data as a `DatasetStore`: the S3 lake through DuckDB, and the
order-management database (RDS PostgreSQL on AWS), served as wide, numeric, dated datasets.

Every dataset is a declarative `Spec`: an id, the SQL that reads it in its source's dialect with
named parameters, and a shape. The SQL returns long rows, ``(index, column, value)``, and the
store pivots them to the wide table the platform's datasets are. Three datasets need Python
beyond that (marked ``FUND-SPECIFIC LOGIC``): the rating panels, which forward-fill a history, and
the bond reference table, which joins two sources.

Connection setup is in two functions, `connect_lake` and `connect_oms`, and nothing else opens a
connection. Credentials come from the environment, as ECS injects Secrets Manager values.

Conventions, so every tool reads the same thing:
- Dated datasets run over the business days of the lake's bond price table, oldest first.
  Prices, spreads, and durations are as the vendor gives them; a defaulted bond has missing
  (NaN) yield, OAS, and durations, never zero. A bond's total return is local currency.
- ``mv_*`` are USD market values (dirty), signed: shorts are negative; a bond not held that day is
  0.0. ``w_*`` are those over the book's capital.
- ``cds_*`` and ``idx_spread`` are spreads in bp; a name with no quote that day is NaN.
- Rating scores run from 1 (AAA) to 22 (D); see ``build_wrenfield.SCALE``.
- ``book_returns_net`` is the book's net P&L over its capital; the overlay book has no capital
  and is measured against the fund's.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit
from urllib.request import url2pathname

from quant_agent_os.data import Dataset, UnknownDatasetError, parse_dataset

BOOKS = ("hy_ls", "ig_rv", "distressed", "overlay")
LAKE_URI = "s3://wrenfield-data-lake"
OMS_URI = "rds://wrenfield-oms.cluster-c1x2y3.us-east-1.rds.amazonaws.com:5432/oms"
SCALE = tuple(
    [
        "AAA",
        "AA+",
        "AA",
        "AA-",
        "A+",
        "A",
        "A-",
        "BBB+",
        "BBB",
        "BBB-",
        "BB+",
        "BB",
        "BB-",
        "B+",
        "B",
        "B-",
        "CCC+",
        "CCC",
        "CCC-",
        "CC",
        "C",
        "D",
    ]
)
BOOK_CODES = {"hy_ls": 1, "ig_rv": 2, "distressed": 3, "overlay": 4}
POSTGRES_TTL_SECONDS = 60.0

Source = Literal["lake", "oms"]
Cell = float | None
Row = tuple[Any, ...]


@dataclass(frozen=True)
class Spec:
    """One dataset: where it comes from, its SQL, and how its long rows become a table.

    ``shape`` ``dated`` pivots ``(date, column, value)`` onto the business-day calendar; ``keyed``
    pivots ``(key, column, value)`` onto sorted keys. ``fill`` replaces a missing cell (None
    keeps it missing). ``params`` are named, as ``:book``.
    """

    id: str
    source: Source
    sql: str
    shape: Literal["dated", "keyed"] = "dated"
    params: Mapping[str, object] | None = None
    fill: float | None = None


def _px(column: str) -> str:
    return (
        f"SELECT strftime(dt, '%Y-%m-%d') AS d, bond_id, {column} FROM "
        "read_parquet('{lake}/curated/bond_px_daily/dt=*/part-0000.parquet', "
        "hive_partitioning=true)"
    )


def _cds(tenor: str) -> str:
    return (
        "SELECT strftime(dt, '%Y-%m-%d'), name_id, spread_bp FROM "
        "read_parquet('{lake}/curated/cds_daily/dt=*/part-0000.parquet', hive_partitioning=true) "
        f"WHERE kind = 'single' AND tenor = '{tenor}'"
    )


def _rates(prefix: str) -> str:
    return (
        "SELECT strftime(dt, '%Y-%m-%d'), series_id, value FROM "
        "read_parquet('{lake}/curated/rates_daily/dt=*/part-0000.parquet', hive_partitioning=true) "
        f"WHERE series_id LIKE '{prefix}_%'"
    )


_MV = "SELECT CAST(date AS TEXT), bond_id, mv_usd FROM pos_eod WHERE book = :book"
_W = (
    "SELECT CAST(p.date AS TEXT), p.bond_id, p.mv_usd / b.capital_usd FROM pos_eod p "
    "JOIN ref_books b ON b.book = p.book WHERE p.book = :book"
)
_RETURNS = (
    "SELECT CAST(p.date AS TEXT), p.book, p.{col} / COALESCE(b.capital_usd, "
    "(SELECT SUM(capital_usd) FROM ref_books)) FROM pnl_daily p JOIN ref_books b ON b.book = p.book"
)


def _specs() -> dict[str, Spec]:
    specs: list[Spec] = [
        Spec("px_clean", "lake", _px("clean_price")),
        Spec("px_accrued", "lake", _px("accrued_interest")),
        Spec("bond_ytw", "lake", _px("ytw_pct")),
        Spec("bond_oas", "lake", _px("oas_bp")),
        Spec("bond_sprdur", "lake", _px("spread_duration")),
        Spec("bond_moddur", "lake", _px("modified_duration")),
        Spec("bond_ttm", "lake", _px("years_to_maturity")),
        Spec("bond_tr", "lake", _px("total_return")),
        Spec("cds_3y", "lake", _cds("3Y")),
        Spec("cds_5y", "lake", _cds("5Y")),
        Spec("cds_10y", "lake", _cds("10Y")),
        Spec(
            "idx_spread",
            "lake",
            "SELECT strftime(dt, '%Y-%m-%d'), name_id, spread_bp FROM "
            "read_parquet('{lake}/curated/cds_daily/dt=*/part-0000.parquet', "
            "hive_partitioning=true) WHERE kind = 'index'",
        ),
        Spec("gov_usd", "lake", _rates("UST")),
        Spec("gov_eur", "lake", _rates("EURGOV")),
        Spec(
            "fx_eurusd",
            "lake",
            "SELECT strftime(dt, '%Y-%m-%d'), series_id, value FROM "
            "read_parquet('{lake}/curated/rates_daily/dt=*/part-0000.parquet', "
            "hive_partitioning=true) WHERE series_id = 'EURUSD'",
        ),
        Spec(
            "sig_spread_momentum",
            "lake",
            "SELECT dt, bond_id, value FROM "
            "read_parquet('{lake}/curated/sig_spread_momentum/month=*/part-0000.parquet', "
            "hive_partitioning=true)",
        ),
        Spec(
            "fm_loadings",
            "lake",
            "SELECT bond_id, factor_id, loading FROM "
            "read_parquet('{lake}/curated/fm_loadings/dt=*/part-0000.parquet')",
            shape="keyed",
            fill=0.0,
        ),
        Spec(
            "fm_factor_cov",
            "lake",
            "SELECT factor_id_1, factor_id_2, cov_annual FROM "
            "read_parquet('{lake}/curated/fm_factor_cov/dt=*/part-0000.parquet')",
            shape="keyed",
        ),
        Spec(
            "fm_specific",
            "lake",
            "SELECT bond_id, 'specific_var_annual', specific_var_annual FROM "
            "read_parquet('{lake}/curated/fm_specific/dt=*/part-0000.parquet')",
            shape="keyed",
        ),
        Spec(
            "cds_protection",
            "oms",
            "SELECT CAST(date AS TEXT), name_id, protection_notional_usd FROM pos_cds",
            fill=0.0,
        ),
        Spec(
            "idx_protection",
            "oms",
            "SELECT CAST(date AS TEXT), index_id, protection_notional_usd FROM pos_index",
            fill=0.0,
        ),
        Spec("repo_rate", "oms", "SELECT CAST(date AS TEXT), 'repo', rate_pct FROM fin_repo_rates"),
        Spec(
            "borrow_fee",
            "oms",
            "SELECT bond_id, 'fee_bp', fee_bp FROM fin_borrow_fees",
            shape="keyed",
        ),
        Spec("book_returns_net", "oms", _RETURNS.format(col="net_pnl_usd")),
        Spec("book_returns_gross", "oms", _RETURNS.format(col="gross_pnl_usd")),
        Spec(
            "nav",
            "oms",
            "SELECT CAST(p.date AS TEXT), p.book, COALESCE(b.capital_usd, "
            "(SELECT SUM(capital_usd) FROM ref_books)) FROM pnl_daily p "
            "JOIN ref_books b ON b.book = p.book UNION ALL "
            "SELECT CAST(date AS TEXT), 'fund', (SELECT SUM(capital_usd) FROM ref_books) "
            "FROM fin_repo_rates",
        ),
    ]
    for book in BOOKS[:3]:  # the overlay holds no bonds
        specs.append(Spec(f"mv_{book}", "oms", _MV, params={"book": book}, fill=0.0))
        specs.append(Spec(f"w_{book}", "oms", _W, params={"book": book}, fill=0.0))
    for book in BOOKS:  # one dataset per book, because access is by dataset, not by column
        for kind, column in (("net", "net_pnl_usd"), ("gross", "gross_pnl_usd")):
            sql = _RETURNS.format(col=column) + " WHERE p.book = :book"
            specs.append(Spec(f"ret_{kind}_{book}", "oms", sql, params={"book": book}))
    specs.append(
        Spec("mv_fund", "oms", "SELECT CAST(date AS TEXT), bond_id, mv_usd FROM pos_eod", fill=0.0)
    )
    return {spec.id: spec for spec in specs}


SPECS = _specs()
# FUND-SPECIFIC LOGIC: built in Python, not by one query.
CUSTOM = ("bond_static", "issuer_rating_score_me", "bond_rating")


# ---- connections: the only two places a connection is opened ---------------------------------


def connect_lake(uri_map: Mapping[str, str], base: Path) -> Any:
    """Return a DuckDB connection that reads the lake. On AWS this also runs ``INSTALL httpfs``
    and ``CREATE SECRET (TYPE s3, PROVIDER credential_chain)``, so the task role's credentials
    reach S3; here the bucket is a directory."""
    import duckdb

    connection = duckdb.connect(":memory:")
    connection.execute("SET threads TO 2")
    del uri_map, base
    return connection


def sqlite_url(path: Path) -> str:
    """Return the ``sqlite:///`` URL of a database file, with a drive letter kept on Windows."""
    return "sqlite:" + path.resolve().as_uri().removeprefix("file:")


def sqlite_path(url: str) -> Path:
    """Return the database file a ``sqlite:///`` URL names."""
    return Path(url2pathname(urlsplit(url).path))


def connect_oms(url: str, environ: Mapping[str, str | None]) -> Any:
    """Return a DB-API connection to the order-management database, read-only where it can be.

    ``sqlite:///path`` is the offline stand-in; ``postgresql://host/db`` is RDS, read through
    psycopg with the password in ``WRENFIELD_OMS_PASSWORD`` (a Secrets Manager value that ECS
    injects), the ``oms`` schema on the search path, and a read-only session.
    """
    parts = urlsplit(url)
    if parts.scheme == "sqlite":
        return sqlite3.connect(f"{sqlite_path(url).resolve().as_uri()}?mode=ro", uri=True)
    import psycopg

    connection = psycopg.connect(
        url,
        password=environ.get("WRENFIELD_OMS_PASSWORD"),
        options="-c search_path=oms -c default_transaction_read_only=on",
        autocommit=True,
    )
    return connection


def _named(sql: str, params: Mapping[str, object] | None, postgres: bool) -> tuple[str, Any]:
    """Return ``sql`` with ``:name`` parameters in the driver's style (``%(name)s`` for psycopg)."""
    values = dict(params or {})
    if not postgres:
        return sql, values
    out = sql
    for name in values:
        out = out.replace(f":{name}", f"%({name})s")
    return out, values


class WrenfieldDatasetStore:
    """The estate's tables as wide datasets: read-only, cached, rebuilt when the sources change."""

    def __init__(
        self,
        base: Path,
        *,
        oms_url: str | None = None,
        environ: Mapping[str, str | None] | None = None,
    ) -> None:
        self.base = base
        self.uri_map: dict[str, str] = json.loads((base / "uri_map.json").read_text("utf-8"))
        self.environ = environ if environ is not None else os.environ
        self.oms_url = oms_url or self.environ.get("WRENFIELD_OMS_URL") or self._local_oms_url()
        self.postgres = urlsplit(self.oms_url).scheme.startswith("postgres")
        self._lock = threading.Lock()
        self._stamp: tuple[object, ...] | None = None
        self._checked = 0.0
        self._cache: dict[str, Dataset] = {}
        self._lake: Any = None

    def _local_oms_url(self) -> str:
        return sqlite_url(self.base / self.uri_map[OMS_URI])

    def lake_dir(self) -> Path:
        """Return the directory the lake's bucket is mapped to."""
        return self.base / self.uri_map[LAKE_URI]

    def ids(self) -> list[str]:
        """Return every dataset id, sorted."""
        return sorted([*SPECS, *CUSTOM])

    def get(self, dataset_id: str) -> Dataset:
        """Return dataset ``dataset_id``, or raise `UnknownDatasetError` naming the others."""
        if dataset_id not in self.ids():
            raise UnknownDatasetError(f"No dataset '{dataset_id}'; the datasets are "
                                      f"{', '.join(self.ids())}.")  # fmt: skip
        self._check_stamp()
        with self._lock:
            cached = self._cache.get(dataset_id)
        if cached is not None:
            return cached
        text = self._build(dataset_id)
        dataset = parse_dataset(dataset_id, text.encode("utf-8"))
        with self._lock:
            self._cache[dataset_id] = dataset
        return dataset

    # ---- caching -----------------------------------------------------------------------

    def _check_stamp(self) -> None:
        """Forget every dataset when a source changed. SQLite and the lake show it in file
        metadata; PostgreSQL has no cheap stamp, so it is trusted for `POSTGRES_TTL_SECONDS`."""
        now = time.monotonic()
        with self._lock:
            if (
                self.postgres
                and self._stamp is not None
                and now - self._checked < POSTGRES_TTL_SECONDS
            ):
                return
            self._checked = now
        lake = self.lake_dir() / "curated"
        parts = sorted(lake.glob("*/*/part-0000.parquet"))
        stamp: tuple[object, ...] = (
            len(parts),
            max((p.stat().st_mtime_ns for p in parts), default=0),
            None if self.postgres else sqlite_path(self.oms_url).stat().st_mtime_ns,
        )
        with self._lock:
            if stamp != self._stamp:
                self._stamp = stamp
                self._cache.clear()

    # ---- query -------------------------------------------------------------------------

    def _rows(self, source: Source, sql: str, params: Mapping[str, object] | None) -> list[Row]:
        if source == "lake":
            with self._lock:
                if self._lake is None:
                    self._lake = connect_lake(self.uri_map, self.base)
                text = sql.replace("{lake}", str(self.lake_dir()))
                rows: list[Row] = self._lake.execute(text).fetchall()
            return rows
        statement, values = _named(sql, params, self.postgres)
        with closing(connect_oms(self.oms_url, self.environ)) as db:
            return list(db.execute(statement, values).fetchall())

    # ---- shape -------------------------------------------------------------------------

    def _calendar(self) -> list[str]:
        rows = self._rows(
            "lake",
            "SELECT DISTINCT strftime(dt, '%Y-%m-%d') FROM "
            "read_parquet('{lake}/curated/bond_px_daily/dt=*/part-0000.parquet', "
            "hive_partitioning=true) ORDER BY 1",
            None,
        )
        return [row[0] for row in rows]

    def _build(self, dataset_id: str) -> str:
        if dataset_id in CUSTOM:
            return self._custom(dataset_id)
        spec = SPECS[dataset_id]
        rows = self._rows(spec.source, spec.sql, spec.params)
        if spec.shape == "keyed":
            return _pivot(rows, None, spec.fill, "key")
        return _pivot(rows, self._calendar(), spec.fill, "date")

    def _custom(self, dataset_id: str) -> str:
        """FUND-SPECIFIC LOGIC: the three datasets one query cannot make."""
        history = self._rows(
            "lake",
            "SELECT issuer_id, strftime(CAST(effective_date AS DATE), '%Y-%m-%d'), rating "
            "FROM read_parquet('{lake}/curated/rating_history/dt=*/part-0000.parquet') "
            "WHERE agency = 'SP' ORDER BY 1, 2",
            None,
        )
        by_issuer: dict[str, list[tuple[str, int]]] = {}
        for issuer, when, rating in history:
            by_issuer.setdefault(issuer, []).append((when, SCALE.index(rating) + 1))
        if dataset_id == "issuer_rating_score_me":
            months = [f"{2023 + m // 12}-{m % 12 + 1:02d}" for m in range(36)]
            ends = [_month_end(month) for month in months]
            return _score_table(by_issuer, ends)
        bonds = self._rows(
            "lake",
            "SELECT b.bond_id, b.issuer_id, b.currency, b.coupon_pct, b.seniority, i.sector_code "
            "FROM read_parquet('{lake}/curated/ref_bond/dt=*/part-0000.parquet') b JOIN "
            "read_parquet('{lake}/curated/ref_issuer/dt=*/part-0000.parquet') i "
            "USING (issuer_id) ORDER BY 1",
            None,
        )
        if dataset_id == "bond_rating":
            issuer_of = {row[0]: row[1] for row in bonds}
            calendar = self._calendar()
            issuer_scores = _score_rows(by_issuer, calendar)
            header = ["date", *sorted(issuer_of)]
            return _csv(header, ([d, *(issuer_scores[issuer_of[b]][i] for b in sorted(issuer_of))]
                                 for i, d in enumerate(calendar)))  # fmt: skip
        book_of = {
            row[0]: row[1]
            for row in self._rows("oms", "SELECT DISTINCT bond_id, book FROM pos_eod", None)
        }
        assumed = {1: 0.60, 2: 0.40, 3: 0.15}
        rows = [
            [b, sector, seniority, 1 if currency == "USD" else 2, coupon, assumed[seniority],
             BOOK_CODES.get(book_of.get(b, ""), 0)]
            for b, _issuer, currency, coupon, seniority, sector in bonds
        ]  # fmt: skip
        header = ["bond_id", "sector_code", "seniority_code", "currency_code", "coupon_pct",
                  "recovery_assumption", "book_code"]  # fmt: skip
        return _csv(header, rows)


def _month_end(month: str) -> str:
    year, number = int(month[:4]), int(month[5:])
    nxt = year * 12 + number
    from datetime import date, timedelta

    return (date(nxt // 12, nxt % 12 + 1, 1) - timedelta(days=1)).isoformat()


def _score_rows(
    by_issuer: Mapping[str, list[tuple[str, int]]], dates: Sequence[str]
) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for issuer, events in by_issuer.items():
        scores = []
        for day in dates:
            current = events[0][1]
            for when, score in events:
                if when <= day:
                    current = score
            scores.append(float(current))
        out[issuer] = scores
    return out


def _score_table(by_issuer: Mapping[str, list[tuple[str, int]]], dates: Sequence[str]) -> str:
    scores = _score_rows(by_issuer, dates)
    names = sorted(scores)
    return _csv(
        ["date", *names], ([d, *(scores[n][i] for n in names)] for i, d in enumerate(dates))
    )


def _csv(header: Sequence[str], rows: Any) -> str:
    text = io.StringIO()
    writer = csv.writer(text, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(["NaN" if cell is None else _number(cell) for cell in row])
    return text.getvalue()


def _number(cell: object) -> object:
    return repr(float(cell)) if isinstance(cell, float) else cell


def _pivot(
    rows: Sequence[Row], calendar: list[str] | None, fill: float | None, index_name: str
) -> str:
    """Pivot ``(index, column, value)`` rows to CSV: every calendar day when given, every key
    otherwise; a cell with no row is ``fill`` (missing when None), a NULL value stays missing."""
    values: dict[tuple[str, str], Cell] = {(str(r[0]), str(r[1])): r[2] for r in rows}
    columns = sorted({column for _, column in values})
    index = calendar if calendar is not None else sorted({key for key, _ in values})
    default: Cell = fill
    body = ([key, *(values.get((key, c), default) for c in columns)] for key in index)
    return _csv([index_name if calendar is None else "date", *columns], body)
