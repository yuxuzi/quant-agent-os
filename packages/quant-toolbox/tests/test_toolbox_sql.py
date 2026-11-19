"""The SQL connection: binds, normalised cells, read-only, credentials, and the long-to-wide pivot.

Runs on SQLite (standard library) and DuckDB through SQLAlchemy; without the ``sql`` extra it skips.
"""

from __future__ import annotations

import math
import sqlite3
import warnings
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

sa = pytest.importorskip("sqlalchemy")

from quant_toolbox.data import (  # noqa: E402
    Rows,
    SqlDataset,
    SqlError,
    fetch_rows,
    pivot_long,
    rows_to_csv,
    shape_rows,
    sql,
)

FLOWS = [
    ("2024-03-28", "FUND_I", 10.0),
    ("2024-03-29", "FUND_I", 78.25),
    ("2024-03-29", "FUND_I", 2.08),  # a call and a distribution on one day
    ("2024-03-29", "FUND_II", 5.0),
]


@pytest.fixture
def warehouse(tmp_path: Path) -> Path:
    path = tmp_path / "warehouse.db"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE flows (day TEXT, fund TEXT, amount REAL)")
        db.executemany("INSERT INTO flows VALUES (?, ?, ?)", FLOWS)
        db.execute("CREATE TABLE marks (day TEXT, fund TEXT, nav REAL, note TEXT)")
        db.execute("INSERT INTO marks VALUES ('2024-03-29', 'FUND_I', NULL, NULL)")
        db.execute("INSERT INTO marks VALUES ('2024-03-30', 'FUND_I', 101.5, 'est')")
        db.commit()
    return path


@pytest.fixture
def engine(warehouse: Path) -> Any:
    engine = sql.connect(f"sqlite:///{warehouse}")
    yield engine
    engine.dispose()


def test_binds_use_colon_names_and_a_list_binds_as_an_in_list(engine: Any) -> None:
    rows = fetch_rows(
        engine,
        "SELECT day, amount FROM flows WHERE fund = :fund AND day IN :days ORDER BY day",
        {"fund": "FUND_I", "days": ["2024-03-28", "2024-03-29"]},
    )

    assert rows.columns == ("day", "amount")
    assert [row[0] for row in rows.rows] == ["2024-03-28", "2024-03-29", "2024-03-29"]


def test_a_null_is_nan_in_a_column_of_numbers_and_none_elsewhere(engine: Any) -> None:
    rows = fetch_rows(engine, "SELECT nav, note FROM marks ORDER BY day")

    assert math.isnan(rows.rows[0][0])  # type: ignore[arg-type]
    assert rows.rows[0][1] is None
    assert rows.rows[1] == (101.5, "est")


def test_a_statement_that_returns_no_rows_is_refused(warehouse: Path) -> None:
    engine = sql.connect(f"sqlite:///{warehouse}", read_only=False)

    with pytest.raises(SqlError, match="returns no rows"):
        fetch_rows(engine, "DELETE FROM marks")
    engine.dispose()


def test_sqlite_refuses_a_write_when_read_only(engine: Any) -> None:
    with pytest.raises(sa.exc.OperationalError, match=r"readonly|read-only|read only"):
        fetch_rows(engine, "INSERT INTO flows VALUES ('2024-04-01', 'X', 1.0) RETURNING day")


def test_a_read_write_engine_can_write(warehouse: Path) -> None:
    engine = sql.connect(f"sqlite:///{warehouse}", read_only=False)
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO flows VALUES ('2024-04-01', 'X', 1.0)"))

    assert len(fetch_rows(engine, "SELECT * FROM flows").rows) == 5
    engine.dispose()


def test_read_only_sets_each_dialects_own_session_option() -> None:
    url = sa.engine.make_url
    args: dict[str, Any] = {"options": "-c search_path=oms"}

    sql._read_only(url("postgresql+psycopg://u@h/db"), args, [])
    changed = sql._read_only(url("sqlite:////tmp/a b.db"), {}, [])

    assert args["options"] == "-c search_path=oms -c default_transaction_read_only=on"
    assert changed.database == "file:/tmp/a b.db"
    assert changed.query["mode"] == "ro"


def test_a_dialect_with_no_read_only_setting_warns() -> None:
    with pytest.warns(UserWarning, match="read_only has no effect on mssql"):
        sql._read_only(sa.engine.make_url("mssql+pyodbc://u:p@h/db"), {}, [])


def test_the_credential_is_called_for_every_new_connection(warehouse: Path) -> None:
    tokens = iter(f"token-{n}" for n in range(1, 10))
    seen: list[object] = []
    engine = sql.connect(
        f"sqlite:///{warehouse}", credential=lambda: next(tokens), poolclass=sa.pool.NullPool
    )

    def record(dialect: object, rec: object, args: object, parameters: dict[str, Any]) -> None:
        seen.append(parameters.pop("password"))  # SQLite takes no password

    sa.event.listen(engine, "do_connect", record)
    for _ in range(3):
        fetch_rows(engine, "SELECT 1 AS one")

    assert seen == ["token-1", "token-2", "token-3"]
    engine.dispose()


def test_a_missing_column_names_the_ones_there_are(engine: Any) -> None:
    rows = fetch_rows(engine, "SELECT day, fund, amount FROM flows")

    with pytest.raises(SqlError, match=r"no column 'nope'.*day, fund, amount"):
        pivot_long(rows, "day", "nope", "amount")


def test_a_pivot_refuses_a_call_and_a_distribution_on_one_day(engine: Any) -> None:
    rows = fetch_rows(engine, "SELECT day, fund, amount FROM flows")

    with pytest.raises(SqlError, match=r"\(2024-03-29, FUND_I\) x2"):
        pivot_long(rows, "day", "fund", "amount")


def test_sum_nets_and_last_keeps_the_last_row_when_the_data_means_it(engine: Any) -> None:
    rows = fetch_rows(engine, "SELECT day, fund, amount FROM flows")

    summed = pivot_long(rows, "day", "fund", "amount", duplicates="sum")
    last = pivot_long(rows, "day", "fund", "amount", duplicates="last")

    assert summed.columns == ("day", "FUND_I", "FUND_II")
    assert summed.rows[1][1] == pytest.approx(80.33)
    assert last.rows[1][1] == 2.08
    assert math.isnan(summed.rows[0][2])  # type: ignore[arg-type]


def test_a_pivot_fills_absent_cells_but_not_nulls_and_follows_the_calendar() -> None:
    rows = Rows(
        ("d", "k", "v"),
        (("2024-01-02", "a", 1.0), ("2024-01-03", "a", math.nan), ("2024-01-04", "b", 3.0)),
    )

    wide = pivot_long(
        rows, "d", "k", "v", fill=0.0, index_values=["2024-01-02", "2024-01-03", "2024-01-05"]
    )

    assert wide.rows[0] == ("2024-01-02", 1.0, 0.0)
    assert math.isnan(wide.rows[1][1])  # type: ignore[arg-type]
    assert wide.rows[2] == ("2024-01-05", 0.0, 0.0)


def test_a_pivot_refuses_a_value_that_is_not_a_number() -> None:
    with pytest.raises(SqlError, match="not a number"):
        pivot_long(Rows(("d", "k", "v"), (("2024-01-02", "a", "x"),)), "d", "k", "v")


def test_shapes_default_to_the_first_columns_and_wide_puts_the_index_first() -> None:
    rows = Rows(("d", "k", "v"), (("2024-01-02", "a", 1.0), ("2024-01-03", "a", 2.0)))
    wide = Rows(("x", "when"), ((1.0, "2024-01-02"),))

    assert shape_rows(SqlDataset("a", "SELECT 1"), rows).columns == ("d", "a")
    assert shape_rows(SqlDataset("a", "SELECT 1", shape="wide", index="when"), wide).columns == (
        "when",
        "x",
    )
    assert shape_rows(SqlDataset("a", "SELECT 1", shape="table"), wide) is wide


def test_a_dataset_declaration_is_checked() -> None:
    with pytest.raises(SqlError, match="all of index, column, and value, or none"):
        SqlDataset("a", "SELECT 1", index="d")
    with pytest.raises(SqlError, match="for long"):
        SqlDataset("a", "SELECT 1", shape="table", value="v")
    with pytest.raises(SqlError, match="binds :book but has no value"):
        SqlDataset("a", "SELECT :book").params_for("SELECT :book")
    assert SqlDataset("a", "SELECT 1", params={"b": 1, "c": 2}).params_for("x :b") == {"b": 1}


def test_csv_writes_nan_none_and_shortest_floats() -> None:
    rows = Rows(("d", "v", "w"), (("2024-01-02", 0.1, math.nan), ("2024-01-03", 2.0, None)))

    assert rows_to_csv(rows) == "d,v,w\n2024-01-02,0.1,NaN\n2024-01-03,2.0,\n"


# ---- DuckDB through duckdb-engine: Decimal, dates, identifier case, read-only files -------------


@pytest.fixture
def duck() -> Any:
    pytest.importorskip("duckdb_engine")
    engine = sql.connect("duckdb:///:memory:")
    yield engine
    engine.dispose()


def test_decimals_become_floats_and_dates_become_iso_text(duck: Any) -> None:
    rows = fetch_rows(
        duck,
        "SELECT DATE '2024-03-29' AS d, TIMESTAMP '2024-03-29 00:00:00' AS t, "
        "TIMESTAMP '2024-03-29 16:30:00' AS u, CAST(12.50 AS DECIMAL(10, 2)) AS amount",
    )

    assert rows.rows == (("2024-03-29", "2024-03-29", "2024-03-29T16:30:00", 12.5),)
    assert type(rows.rows[0][3]) is float


def test_upper_case_identifiers_read_as_lower_case_and_collisions_are_refused(duck: Any) -> None:
    rows = fetch_rows(duck, 'SELECT 1 AS "BOND_ID", \'ABC\' AS "Name"')

    assert rows.columns == ("bond_id", "name")
    assert rows.rows == ((1, "ABC"),)  # values keep their case
    with pytest.raises(SqlError, match="collide"):
        fetch_rows(duck, 'SELECT 1 AS "A", 2 AS "a"')


def test_a_duckdb_file_opens_read_only(tmp_path: Path) -> None:
    duckdb = pytest.importorskip("duckdb")
    pytest.importorskip("duckdb_engine")
    path = tmp_path / "lake.duckdb"
    with duckdb.connect(str(path)) as db:
        db.execute("CREATE TABLE t AS SELECT 1 AS x")
    engine = sql.connect(f"duckdb:///{path}")

    assert fetch_rows(engine, "SELECT x FROM t").rows == ((1,),)
    with pytest.raises(sa.exc.DBAPIError, match="read-only"):
        fetch_rows(engine, "INSERT INTO t VALUES (2)")
    engine.dispose()


def test_sqlalchemy_duckdb_engine_and_duckdb_work_together_over_parquet(
    duck: Any, tmp_path: Path
) -> None:
    duckdb = pytest.importorskip("duckdb")
    folder = tmp_path / "px" / "dt=2024-03-29"
    folder.mkdir(parents=True)
    with duckdb.connect(":memory:") as db:
        db.execute(f"COPY (SELECT 'A' AS bond, 99.5 AS px) TO '{folder}/part.parquet'")

    rows = fetch_rows(
        duck,
        f"SELECT CAST(dt AS VARCHAR) AS d, bond, px FROM "
        f"read_parquet('{tmp_path}/px/dt=*/*.parquet', hive_partitioning=true) WHERE px > :floor",
        {"floor": 50},
    )

    assert rows.rows == (("2024-03-29", "A", 99.5),)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert pivot_long(rows, "d", "bond", "px").rows == (("2024-03-29", 99.5),)


def test_index_name_renames_the_index_column_of_any_shape() -> None:
    rows = Rows(("strftime(dt)", "k", "v"), (("2024-01-02", "a", 1.0),))
    long = SqlDataset("a", "SELECT 1", index_name="Date")

    assert shape_rows(long, rows).columns == ("date", "a")
    assert (
        shape_rows(SqlDataset("a", "SELECT 1", shape="table", index_name="d"), rows).columns[0]
        == "d"
    )
