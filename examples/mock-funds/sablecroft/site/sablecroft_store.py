"""Sablecroft's warehouse as a `DatasetStore`: one query per dataset id, long rows served wide.

Reads the serving views of `sablecroft_views.sql` through a `SqlWarehouse` (a Databricks SQL warehouse
in production, `LocalDeltaWarehouse` in tests), and never writes. Each dataset goes through the
platform's own CSV parser, so it gets the checks of `qaos data add`, and its fingerprint is the SHA-256
of that CSV.

Conventions:
- Returns are simple periodic returns as fractions, in the manager's own currency, except `ret_usd`
  (EUR managers converted at month-end EURUSD, unhedged). Dated datasets are monthly (month-end) and
  hold a missing value (NaN) for every manager and month with no figure at the data cut.
- `ret_final` holds the administrator's final figures only; `ret_est` the manager's estimates;
  `ret_best` the final where published, else the estimate. `ret_final_q` is the three quarterly
  reporters, on quarter-ends. `ret_final_<strategy>` and `ret_est_<strategy>` split by strategy.
- `pm_cashflows` is signed (calls negative, distributions positive), in each fund's currency, dated by
  the day of the flow; `pm_nav` is quarter-end, from the GP's capital-account statement.
- `mgr_terms` and `pm_terms` have one row per manager or fund and only numeric columns: strategies,
  currencies, and dates are coded (see sablecroft_views.sql); the investment date is yyyymmdd.
"""

from __future__ import annotations

import csv
import io
import os
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sablecroft_warehouse import LocalDeltaWarehouse, SqlWarehouse

from quant_agent_os.data import Dataset, UnknownDatasetError, parse_dataset

STRATEGIES = (
    "eq_ls", "macro", "event", "relval", "cta", "multistrat", "illiquid_credit", "real_assets",
)


# ---- connection: the one place the warehouse is chosen and its credentials are named -----------


def connect() -> SqlWarehouse:
    """Return the SQL warehouse: Databricks in production, the local Delta stand-in otherwise.

    Production would read DATABRICKS_SERVER_HOSTNAME, DATABRICKS_HTTP_PATH, and an Entra ID service
    principal's client id and secret (a Key Vault reference, surfaced as env vars), then call
    `databricks.sql.connect`. Not available here; see FINDINGS.md.
    """
    if os.environ.get("SAB_WAREHOUSE_KIND") == "databricks":
        raise SystemExit("The Databricks connection needs databricks-sql-connector (not included).")
    root = os.environ.get("SAB_LAKE_ROOT")
    if not root or not Path(root).is_dir():
        raise SystemExit("Set SAB_LAKE_ROOT to the built lake directory (build_sablecroft.py).")
    return LocalDeltaWarehouse(Path(root))


# ---- query and mapping: one declarative entry per dataset ---------------------------------------


@dataclass(frozen=True)
class Entry:
    """A dataset: its SQL (Databricks dialect, `?` parameters), and how the rows become a table.

    shape "long": the query returns (date, key, value) and is pivoted wide, NaN where a pair is
    missing. shape "table": the query's columns are the dataset's, the first is its index.
    """

    sql: str
    shape: str = "long"
    params: tuple[str, ...] = ()


SERVING = "sablecroft.serving"
_LONG = "SELECT {date}, {key}, value FROM " + SERVING + ".{view}"
ENTRIES: dict[str, Entry] = {
    "ret_final": Entry(_LONG.format(date="period_end", key="manager_id", view="v_ret_final")),
    "ret_est": Entry(_LONG.format(date="period_end", key="manager_id", view="v_ret_est")),
    "ret_best": Entry(_LONG.format(date="period_end", key="manager_id", view="v_ret_best")),
    "ret_usd": Entry(_LONG.format(date="period_end", key="manager_id", view="v_ret_usd")),
    "ret_final_q": Entry(_LONG.format(date="period_end", key="manager_id", view="v_ret_final_q")),
    "fx_eurusd": Entry(_LONG.format(date="period_end", key="pair", view="v_fx_eurusd")),
    "idx_public": Entry(_LONG.format(date="date", key="index_id", view="v_idx_public")),
    "fof_weights": Entry(_LONG.format(date="period_end", key="manager_id", view="v_fof_weights")),
    "fof_fund": Entry(f"SELECT * FROM {SERVING}.v_fof_fund", "table"),
    "pm_cashflows": Entry(_LONG.format(date="flow_date", key="fund_id", view="v_pm_cashflows")),
    "pm_nav": Entry(_LONG.format(date="nav_date", key="fund_id", view="v_pm_nav")),
    "pm_terms": Entry(f"SELECT * FROM {SERVING}.v_pm_terms", "table"),
    "mgr_terms": Entry(f"SELECT * FROM {SERVING}.v_mgr_terms", "table"),
    "ops_recon_diff_usd": Entry(_LONG.format(date="period_end", key="manager_id", view="v_ops_recon")),
}
for _base, _view in (("ret_final", "v_ret_final"), ("ret_est", "v_ret_est")):
    for _strategy in STRATEGIES:
        ENTRIES[f"{_base}_{_strategy}"] = Entry(
            _LONG.format(date="period_end", key="manager_id", view=_view) + " WHERE strategy = ?",
            params=(_strategy,),
        )
# The only dataset that is not one entry: none. (A fund needing Python logic per dataset marks it here.)


class SablecroftStore:
    """Sablecroft's datasets over a SQL warehouse, read-only, rebuilt when the data version changes."""

    def __init__(self, warehouse: SqlWarehouse) -> None:
        self.warehouse = warehouse
        self._lock = threading.Lock()
        self._version: str | None = None
        self._cache: dict[str, Dataset] = {}

    def ids(self) -> list[str]:
        """Return every dataset id, sorted."""
        return sorted(ENTRIES)

    def get(self, dataset_id: str) -> Dataset:
        """Return dataset ``dataset_id``, or raise `UnknownDatasetError` naming the others."""
        if dataset_id not in ENTRIES:
            raise UnknownDatasetError(
                f"No dataset '{dataset_id}'; the datasets are {', '.join(self.ids())}."
            )
        version = self.warehouse.data_version()  # caching and versioning
        with self._lock:
            if version != self._version:
                self._version, self._cache = version, {}
            cached = self._cache.get(dataset_id)
        if cached is not None:
            return cached
        entry = ENTRIES[dataset_id]
        columns, rows = self.warehouse.query(entry.sql, entry.params)
        text = _wide(rows) if entry.shape == "long" else _csv(columns, rows)
        dataset = parse_dataset(dataset_id, text.encode("utf-8"))  # fingerprint: SHA-256 of the CSV
        with self._lock:
            self._cache[dataset_id] = dataset
        return dataset


# ---- pivot and shape -----------------------------------------------------------------------------


def _cell(value: Any) -> object:
    if value is None:
        return "NaN"
    return repr(float(value)) if not isinstance(value, int | str) else value


def _csv(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    text = io.StringIO()
    writer = csv.writer(text, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([_cell(v) for v in row])
    return text.getvalue()


def _wide(rows: Sequence[Sequence[Any]]) -> str:
    """Pivot (date, key, value) rows: every date that has a row, every key, NaN elsewhere."""
    values: dict[tuple[str, str], Any] = {(str(d), str(k)): v for d, k, v in rows}
    keys = sorted({k for _, k in values})
    days = sorted({d for d, _ in values})
    return _csv(["date", *keys], [[d, *(values.get((d, k)) for k in keys)] for d in days])
