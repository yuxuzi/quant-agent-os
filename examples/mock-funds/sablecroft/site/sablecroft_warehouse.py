"""The Databricks side of Sablecroft's estate, and a local stand-in for it.

Production: a Databricks SQL warehouse over Unity Catalog `sablecroft`, reached with
`databricks-sql-connector` (`databricks.sql.connect(server_hostname, http_path, ...)`, authenticated
as an Entra ID service principal by OAuth M2M). That driver is not installed here and is not added:
`DatabricksWarehouse` below is what the fund would write, and is never run in tests.

Tests: `LocalDeltaWarehouse` is DuckDB (dev dependency, imported inside the function: the wheel does
not ship it) with an attached catalog `sablecroft`, its schemas `curated` and `ref` as views
over the
Parquet files each Delta table's `_delta_log` lists (`read_parquet([...], hive_partitioning=true)`),
and `sablecroft_views.sql` run unchanged: three-part names work as they do in Unity Catalog. It is a
stand-in for the SQL the views use, not for Databricks.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Protocol

CATALOG = "sablecroft"
VIEWS = Path(__file__).with_name("sablecroft_views.sql")


class SqlWarehouse(Protocol):
    """What the dataset store needs of a SQL warehouse: rows for a query, and a data version."""

    def query(
        self, statement: str, params: tuple[Any, ...] = ()
    ) -> tuple[list[str], list[tuple[Any, ...]]]:
        """Run a read-only statement with bound parameters; return its column names and rows."""
        ...

    def data_version(self) -> str:
        """Return a string that changes when any served table's data changes."""
        ...

    def check(self) -> None:
        """Read the warehouse once, uncached, and raise if a served table cannot be read."""
        ...


def abfss_to_path(uri: str, lake_map: dict[str, str], root: Path) -> Path:
    """Return the local directory an `abfss://container@account.dfs.core.windows.net/path` URI
    maps to."""
    for prefix, folder in lake_map.items():
        if uri == prefix or uri.startswith(prefix + "/"):
            return root / folder / uri[len(prefix) :].lstrip("/")
    raise ValueError(f"No ADLS container is mapped for {uri}.")


def delta_files(table: Path) -> list[Path]:
    """Return the Parquet files a Delta table's log says are live (adds minus removes)."""
    live: dict[str, None] = {}
    for commit in sorted((table / "_delta_log").glob("*.json")):
        for line in commit.read_text().splitlines():
            action = json.loads(line)
            if "add" in action:
                live[action["add"]["path"]] = None
            if "remove" in action:
                live.pop(action["remove"]["path"], None)
    return [table / path for path in live]


def delta_version(table: Path) -> int:
    """Return the latest commit number of a Delta table."""
    return max(int(p.stem) for p in (table / "_delta_log").glob("*.json"))


class LocalDeltaWarehouse:
    """Delta tables on a local 'lake', queried with DuckDB under Unity Catalog's three-part
    names."""

    def __init__(self, root: Path) -> None:
        import duckdb

        self.root = root
        self._table_dirs: list[Path] = []
        lake_map: dict[str, str] = json.loads((root / "lake_map.json").read_text())
        account = next(iter(lake_map)).split("@", 1)[1]
        base = abfss_to_path(f"abfss://uc@{account}/{CATALOG}", lake_map, root)
        self._db = duckdb.connect(":memory:")
        self._lock = threading.Lock()
        self._db.execute(f"ATTACH ':memory:' AS {CATALOG}")
        versions: list[str] = []
        for schema in sorted(p for p in base.iterdir() if p.is_dir()):
            self._db.execute(f"CREATE SCHEMA {CATALOG}.{schema.name}")
            for table in sorted(p for p in schema.iterdir() if (p / "_delta_log").is_dir()):
                self._table_dirs.append(table)
                files = [str(f) for f in delta_files(table)]
                self._db.execute(
                    f"CREATE VIEW {CATALOG}.{schema.name}.{table.name} AS SELECT * FROM "
                    f"read_parquet({files!r}, hive_partitioning = true, union_by_name = true)"
                )
                versions.append(f"{schema.name}.{table.name}@{delta_version(table)}")
        self._version = ";".join(versions)
        text = "\n".join(
            line for line in VIEWS.read_text().splitlines() if not line.strip().startswith("--")
        )
        for statement in text.split(";"):
            if statement.strip():
                self._db.execute(statement)

    def query(
        self, statement: str, params: tuple[Any, ...] = ()
    ) -> tuple[list[str], list[tuple[Any, ...]]]:
        """Run a SELECT with `?` parameters; return its column names and rows."""
        with self._lock:
            cursor = self._db.execute(statement, list(params))
            columns = [str(d[0]) for d in cursor.description]
            return columns, [tuple(row) for row in cursor.fetchall()]

    def data_version(self) -> str:
        """Return the Delta versions of every table, joined."""
        return self._version

    def check(self) -> None:
        """Re-read the table folders from disk (not the views loaded at start) and the database."""
        for table in self._table_dirs:
            if not (table / "_delta_log").is_dir():
                raise FileNotFoundError(f"The table {table} is gone from the lake.")
            delta_version(table)
        self.query("SELECT 1")

    def close(self) -> None:
        """Close the database."""
        self._db.close()


class DatabricksWarehouse:
    """What the fund would run: `databricks-sql-connector` against the serving SQL warehouse.

    Sketch only; not imported or run (the driver is not a dependency of this repository):

        from databricks import sql
        connection = sql.connect(server_hostname=HOST, http_path=HTTP_PATH,
                                 credentials_provider=lambda: oauth_m2m(client_id, secret))
        cursor.execute(statement, parameters); rows = cursor.fetchall()   # native `:name` markers
        data_version: DESCRIBE HISTORY <table> LIMIT 1, per served table
    """
