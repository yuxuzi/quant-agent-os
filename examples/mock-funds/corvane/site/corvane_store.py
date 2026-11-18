"""Corvane Equity Partners' datasets, read from Snowflake and the S3 lake as a `DatasetStore`.

Every dataset is one declarative entry: an id, SQL in Snowflake's dialect with bound parameters,
and a long result of (index, column, value) rows. The store pivots the rows to the wide table the
platform reads (dates by securities, or securities by attributes) and parses it with the
platform's own CSV parser, so a dataset here gets the checks a file added with `qaos data add`
gets, and its fingerprint is the SHA-256 of that CSV. There is no per-dataset Python logic.

The dataset ids, and what each holds:

- ``px_ret_usd``, ``px_ret_local``, ``px_close_local`` (raw, unadjusted), ``fx_ret``,
  ``adv_usd``: dates by securities (``CASH_USD`` is a column with zero returns).
- ``bench_ret`` (dates by benchmark), ``bench_w_<benchmark>``: end-of-day weights, drifted.
- ``weights_<account>``, ``perf_<account>`` (gross, net), ``mandate_flags_<account>``.
- ``scores_<factor>``: the latest delivered score, carried forward to every day.
- ``ref_security``: securities by ``sector_code`` (1 is Cash, then the sectors alphabetically),
  ``industry_code``, ``country_code``, ``currency_code`` (alphabetical, from 1), and ``mcap_usd``.

Missing cells are NaN, never zero, except weights, where a security not held is 0.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

from corvane_tools import SECTORS  # noqa: F401  (the legend of ref_security.sector_code)

from quant_agent_os.data import Dataset, UnknownDatasetError, parse_dataset

SITE = Path(__file__).resolve().parent
VIEWS = SITE / "sql" / "corvane_views.sql"
FACTORS = ("quality", "value", "momentum", "lowvol")
SCHEMAS = ("CURATED", "REF", "SERVING")
STAGED = {
    "SECURITY_MASTER": "CURATED",
    "BENCH_WEIGHTS": "CURATED",
    "BENCH_RETURNS": "CURATED",
    "HOLDINGS_DAILY": "CURATED",
    "TRADES": "CURATED",
    "CORP_ACTIONS": "CURATED",
    "ACCT_RETURNS": "CURATED",
    "ACCOUNTS": "REF",
    "MANDATE_RESTRICTIONS": "REF",
}
LAKE = ("prices_daily", "fx_daily", "factor_scores")


# ===== connection ================================================================================


class SqlConnection(Protocol):
    """What the store needs of a database: run SQL with ``?`` parameters, get rows back."""

    def fetch(self, sql: str, params: Sequence[object] = ()) -> list[tuple[Any, ...]]:
        """Return every row of ``sql``'s result."""
        ...


def connect(environ: Mapping[str, str] | None = None) -> SqlConnection:
    """Return the fund's database connection, from ``CORVANE_SQL_URL`` and the secrets it names.

    ``estate://<directory>`` is the offline stand-in (the generator's output, in DuckDB).
    ``snowflake://<account>/CORVANE?role=...&warehouse=...`` is the real thing: the user comes
    from ``CORVANE_SNOWFLAKE_USER`` and a key-pair private key from ``CORVANE_SNOWFLAKE_PRIVATE_KEY``
    (a Secrets Manager value that ECS injects), neither of them in the URL or the image.
    """
    env = os.environ if environ is None else environ
    url = env.get("CORVANE_SQL_URL", "")
    parts = urlsplit(url)
    if parts.scheme == "estate":
        return EstateConnection(Path(url.removeprefix("estate://")))
    if parts.scheme == "snowflake":
        return _snowflake(parts.hostname or "", parts.path.strip("/"), parse_qs(parts.query), env)
    raise ValueError(
        f"Set CORVANE_SQL_URL to estate://<directory> or snowflake://<account>/<database>, "
        f"not {url!r}."
    )


def _snowflake(
    account: str, database: str, query: dict[str, list[str]], env: Mapping[str, str]
) -> SqlConnection:  # pragma: no cover - needs snowflake-connector-python, not installed here
    try:
        import snowflake.connector  # type: ignore[import-not-found,unused-ignore]
    except ImportError as error:
        raise ConnectionError(
            "Reading Snowflake needs snowflake-connector-python, which this platform does not "
            "install; add it to the fund's own image."
        ) from error
    connection = snowflake.connector.connect(
        account=account,
        database=database,
        user=env["CORVANE_SNOWFLAKE_USER"],
        private_key=env["CORVANE_SNOWFLAKE_PRIVATE_KEY"].encode(),
        role=query.get("role", ["CORVANE_PLATFORM_READER"])[0],
        warehouse=query.get("warehouse", ["CORVANE_WH_XS"])[0],
        paramstyle="qmark",
    )

    class Live:
        def fetch(self, sql: str, params: Sequence[object] = ()) -> list[tuple[Any, ...]]:
            cursor = connection.cursor()
            try:
                cursor.execute(sql, list(params))
                rows: list[tuple[Any, ...]] = cursor.fetchall()
                return rows
            finally:
                cursor.close()

    return Live()


class EstateConnection:
    """The offline stand-in: the generator's staged files and lake in an in-memory DuckDB.

    Same names and SQL as production: attached as ``CORVANE``, schemas ``CURATED``, ``REF``, and
    ``SERVING``, the lake as ``*_EXT`` views over the Parquet files, and the serving views built
    from the same SQL file Snowflake runs.
    """

    def __init__(self, root: Path) -> None:
        import duckdb  # imported here: the platform's wheel does not ship it

        self._lock = threading.Lock()
        self._db = duckdb.connect(":memory:")
        mapping: dict[str, str] = json.loads((root / "s3_map.json").read_text())
        curated = root / mapping["s3://corvane-curated"] / "curated"
        self._db.execute("ATTACH ':memory:' AS CORVANE")
        for schema in SCHEMAS:
            self._db.execute(f"CREATE SCHEMA CORVANE.{schema}")
        for table, schema in STAGED.items():
            files = root / "snowflake_stage" / table / "*.parquet"
            self._db.execute(
                f"CREATE TABLE CORVANE.{schema}.{table} AS SELECT * FROM read_parquet('{files}')"
            )
        for name in LAKE:
            files = curated / name / "*" / "*.parquet"
            self._db.execute(
                f"CREATE VIEW CORVANE.CURATED.{name.upper()}_EXT AS "
                f"SELECT * FROM read_parquet('{files}', hive_partitioning = true)"
            )
        for statement in VIEWS.read_text().split("\n--;\n"):
            self._db.execute(statement)

    def fetch(self, sql: str, params: Sequence[object] = ()) -> list[tuple[Any, ...]]:
        """Return every row of ``sql``'s result (DuckDB takes ``?`` parameters, as Snowflake does)."""
        with self._lock:
            return list(self._db.execute(sql, list(params)).fetchall())


# ===== mapping: dataset id -> SQL -> long rows ===================================================


@dataclass(frozen=True)
class Entry:
    """One dataset: SQL giving (index, column, value) rows, and how to shape them.

    ``bind`` names the parameter this entry takes (an account, a factor, a benchmark): the
    dataset id is ``prefix + <that value, lower case>``, and the SQL's ``?`` is the value in
    upper case. ``fill`` is what a missing cell becomes; None keeps it missing (NaN).
    """

    prefix: str
    sql: str
    index_name: str = "date"
    bind: str | None = None
    fill: float | None = None


ENTRIES: tuple[Entry, ...] = (
    Entry(
        "px_ret_usd",
        "SELECT DT, SECURITY_ID, RET_USD FROM CORVANE.SERVING.V_RET_USD "
        "UNION ALL SELECT DT, 'CASH_USD', 0.0 FROM CORVANE.SERVING.V_CAL "
        "WHERE DT > (SELECT MIN(DT) FROM CORVANE.SERVING.V_CAL)",
    ),
    Entry(
        "px_ret_local",
        "SELECT DT, SECURITY_ID, TOTAL_RETURN_LOCAL FROM CORVANE.CURATED.PRICES_DAILY_EXT "
        "WHERE TOTAL_RETURN_LOCAL IS NOT NULL "
        "UNION ALL SELECT DT, 'CASH_USD', 0.0 FROM CORVANE.SERVING.V_CAL "
        "WHERE DT > (SELECT MIN(DT) FROM CORVANE.SERVING.V_CAL)",
    ),
    Entry(
        "px_close_local",
        "SELECT DT, SECURITY_ID, CLOSE_LOCAL FROM CORVANE.CURATED.PRICES_DAILY_EXT",
    ),
    Entry(
        "fx_ret",
        "SELECT DT, CURRENCY, FX_RET FROM CORVANE.SERVING.V_FX_RET "
        "WHERE DT > (SELECT MIN(DT) FROM CORVANE.SERVING.V_CAL)",
    ),
    Entry("adv_usd", "SELECT DT, SECURITY_ID, ADV_USD FROM CORVANE.SERVING.V_ADV_USD"),
    Entry(
        "bench_ret",
        'SELECT "DATE", BENCHMARK_ID, TOTAL_RETURN_USD FROM CORVANE.CURATED.BENCH_RETURNS '
        "WHERE TOTAL_RETURN_USD IS NOT NULL",
    ),
    Entry(
        "bench_w_",
        "SELECT DT, SECURITY_ID, WEIGHT FROM CORVANE.SERVING.V_BENCH_W_DAILY WHERE BENCHMARK_ID = ?",
        bind="benchmark",
        fill=0.0,
    ),
    Entry(
        "weights_",
        "SELECT DT, SECURITY_ID, WEIGHT FROM CORVANE.SERVING.V_WEIGHTS_DAILY WHERE ACCOUNT_ID = ?",
        bind="account",
        fill=0.0,
    ),
    Entry(
        "perf_",
        'SELECT "DATE", \'gross\', GROSS_RETURN FROM CORVANE.CURATED.ACCT_RETURNS '
        "WHERE ACCOUNT_ID = ? AND GROSS_RETURN IS NOT NULL "
        'UNION ALL SELECT "DATE", \'net\', NET_RETURN FROM CORVANE.CURATED.ACCT_RETURNS '
        "WHERE ACCOUNT_ID = ? AND NET_RETURN IS NOT NULL",
        bind="account",
    ),
    Entry(
        "mandate_flags_",
        "SELECT m.SECURITY_ID, 'excluded', CASE WHEN EXISTS (SELECT 1 FROM "
        "CORVANE.REF.MANDATE_RESTRICTIONS r WHERE r.ACCOUNT_ID = ? "
        "AND r.RULE_TYPE = 'exclude_industry' AND r.RULE_VALUE = m.INDUSTRY) "
        "THEN 1.0 ELSE 0.0 END FROM CORVANE.CURATED.SECURITY_MASTER m",
        index_name="security_id",
        bind="account",
    ),
    Entry(
        "scores_",
        "SELECT DT, SECURITY_ID, SCORE FROM CORVANE.SERVING.V_SCORES_DAILY WHERE UPPER(FACTOR_ID) = ?",
        bind="factor",
    ),
    Entry(
        "ref_security",
        "SELECT SECURITY_ID, 'sector_code', DENSE_RANK() OVER (ORDER BY SECTOR) "
        "FROM CORVANE.CURATED.SECURITY_MASTER "
        "UNION ALL SELECT SECURITY_ID, 'industry_code', DENSE_RANK() OVER (ORDER BY INDUSTRY) "
        "FROM CORVANE.CURATED.SECURITY_MASTER "
        "UNION ALL SELECT SECURITY_ID, 'country_code', DENSE_RANK() OVER (ORDER BY COUNTRY) "
        "FROM CORVANE.CURATED.SECURITY_MASTER "
        "UNION ALL SELECT SECURITY_ID, 'currency_code', DENSE_RANK() OVER (ORDER BY CURRENCY) "
        "FROM CORVANE.CURATED.SECURITY_MASTER "
        "UNION ALL SELECT SECURITY_ID, 'mcap_usd', MCAP_USD FROM CORVANE.CURATED.SECURITY_MASTER",
        index_name="security_id",
    ),
)
BINDINGS = {
    "account": "SELECT ACCOUNT_ID FROM CORVANE.REF.ACCOUNTS ORDER BY 1",
    "benchmark": "SELECT DISTINCT BENCHMARK_ID FROM CORVANE.REF.ACCOUNTS ORDER BY 1",
    "factor": "SELECT DISTINCT FACTOR_ID FROM CORVANE.CURATED.FACTOR_SCORES_EXT ORDER BY 1",
}


# ===== pivot: long rows -> wide CSV ==============================================================


def _text(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def pivot(index_name: str, rows: Sequence[tuple[Any, ...]], fill: float | None) -> str:
    """Return ``rows`` of (index, column, value) as a wide CSV: sorted index, sorted columns."""
    cells: dict[tuple[str, str], float | None] = {}
    for index, column, value in rows:
        cells[_text(index), _text(column)] = None if value is None else float(value)
    names = sorted({column for _, column in cells})
    indexes = sorted({index for index, _ in cells})
    text = io.StringIO()
    writer = csv.writer(text, lineterminator="\n")
    writer.writerow([index_name, *names])
    for index in indexes:
        row: list[str] = []
        for name in names:
            value = cells.get((index, name), fill)
            row.append("NaN" if value is None or math.isnan(value) else repr(value))
        writer.writerow([index, *row])
    return text.getvalue()


# ===== the store: ids, caching, fingerprint ======================================================


class CorvaneDatasetStore:
    """The fund's datasets, built on request, cached for ``ttl_seconds``, and never written."""

    def __init__(self, connection: SqlConnection, ttl_seconds: float = 300.0) -> None:
        self.connection = connection
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, Dataset]] = {}
        self._ids: list[str] | None = None

    def ids(self) -> list[str]:
        """Return every dataset id, sorted."""
        with self._lock:
            if self._ids is None:
                found: list[str] = []
                for entry in ENTRIES:
                    if entry.bind is None:
                        found.append(entry.prefix)
                    else:
                        values = self.connection.fetch(BINDINGS[entry.bind])
                        found += [f"{entry.prefix}{str(v[0]).lower()}" for v in values]
                self._ids = sorted(found)
            return list(self._ids)

    def get(self, dataset_id: str) -> Dataset:
        """Return dataset ``dataset_id``, or raise `UnknownDatasetError` naming the others."""
        known = self.ids()
        if dataset_id not in known:
            raise UnknownDatasetError(f"No dataset '{dataset_id}'; the datasets are {', '.join(known)}.")
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(dataset_id)
            if cached is not None and now - cached[0] < self.ttl_seconds:
                return cached[1]
        entry = next(e for e in ENTRIES if dataset_id == e.prefix or dataset_id.startswith(e.prefix) and e.bind)
        params: list[object] = []
        if entry.bind:
            params = [dataset_id.removeprefix(entry.prefix).upper()] * entry.sql.count("?")
        rows = self.connection.fetch(entry.sql, params)
        dataset = parse_dataset(dataset_id, pivot(entry.index_name, rows, entry.fill).encode())
        with self._lock:
            self._cache[dataset_id] = (now, dataset)
        return dataset
