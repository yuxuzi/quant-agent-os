"""`SqlDatasetStore` (decision 0032): a firm's SQL databases and Parquet lake as datasets."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

pytest.importorskip("sqlalchemy")
pytest.importorskip("duckdb_engine")
pytest.importorskip("empyrical")
import duckdb

from quant_agent_os.api import add_key
from quant_agent_os.data import (
    DirectoryDatasetStore,
    GuardedDatasetStore,
    UnknownDatasetError,
)
from quant_agent_os.runs import MemoryRunStore
from quant_agent_os.tools import FileAccessPolicy, PermissionPolicy
from quant_agent_os.tools.access import Caller, acting_as
from quant_platform import SqlDatasetStore, build_server_app
from quant_platform.sql_store import DatasetReadError
from quant_toolbox.data import Rows, SqlDataset, SqlError, sql

DAYS = ["2024-01-31", "2024-02-29", "2024-03-29"]
BASE = "http://127.0.0.1:8000"


@pytest.fixture
def oms(tmp_path: Path) -> Path:
    path = tmp_path / "oms.db"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE returns (day TEXT, desk TEXT, ret REAL)")
        db.executemany(
            "INSERT INTO returns VALUES (?, ?, ?)",
            [
                (day, desk, value)
                for desk, values in {
                    "growth": [0.01, -0.02, 0.03],
                    "value": [0.0, 0.01, 0.02],
                }.items()
                for day, value in zip(DAYS, values, strict=True)
            ],
        )
        db.execute("CREATE TABLE flows (day TEXT, fund TEXT, amount REAL)")
        db.executemany(
            "INSERT INTO flows VALUES (?, ?, ?)",
            [("2024-01-31", "I", 5.0), ("2024-02-29", "I", 78.25), ("2024-02-29", "I", 2.08)],
        )
        db.execute("CREATE TABLE version (n INTEGER)")
        db.execute("INSERT INTO version VALUES (1)")
        db.commit()
    return path


@pytest.fixture
def lake(tmp_path: Path) -> Path:
    root = tmp_path / "lake" / "prices"
    with duckdb.connect(":memory:") as db:
        for day, price in zip(DAYS, (100.0, 101.0, 99.5), strict=True):
            folder = root / f"dt={day}"
            folder.mkdir(parents=True)
            db.execute(f"COPY (SELECT 'ABC' AS ticker, {price} AS px) TO '{folder}/part-0.parquet'")
    return root


@pytest.fixture
def sources(oms: Path, lake: Path) -> Iterator[dict[str, Any]]:
    engines = {"oms": sql.connect(f"sqlite:///{oms}"), "lake": sql.connect("duckdb:///:memory:")}
    yield engines
    for engine in engines.values():
        engine.dispose()


def declared(lake: Path) -> list[SqlDataset]:
    return [
        SqlDataset(
            "returns-all",
            "SELECT day AS date, desk, ret FROM returns",
            source="oms",
        ),
        SqlDataset(
            "growth-returns",
            "SELECT day AS date, desk, ret FROM returns WHERE desk = :desk",
            params={"desk": "growth"},
            source="oms",
        ),
        SqlDataset("flows", "SELECT day, fund, amount FROM flows", source="oms"),
        SqlDataset(
            "flows-gross",
            "SELECT day, fund, amount FROM flows",
            duplicates="sum",
            source="oms",
        ),
        SqlDataset(
            "px",
            "SELECT CAST(dt AS VARCHAR) AS date, ticker, px FROM "
            f"read_parquet('{lake}/dt=*/*.parquet', hive_partitioning=true)",
            source="lake",
        ),
    ]


def test_a_dataset_is_the_wide_table_its_sql_describes(sources: dict[str, Any], lake: Path) -> None:
    store = SqlDatasetStore(sources, declared(lake))

    returns = store.get("returns-all")

    assert returns.index == DAYS
    assert returns.columns == {"growth": [0.01, -0.02, 0.03], "value": [0.0, 0.01, 0.02]}
    assert store.get("growth-returns").columns == {"growth": [0.01, -0.02, 0.03]}
    assert returns.periods_per_year == 12


def test_the_lake_is_read_through_duckdb_over_hive_partitioned_parquet(
    sources: dict[str, Any], lake: Path
) -> None:
    prices = SqlDatasetStore(sources, declared(lake)).get("px")

    assert prices.index == DAYS
    assert prices.columns == {"ABC": [100.0, 101.0, 99.5]}


def test_the_fingerprint_is_the_one_a_file_with_the_same_cells_has(
    sources: dict[str, Any], lake: Path, tmp_path: Path
) -> None:
    store = SqlDatasetStore(sources, declared(lake))
    file = tmp_path / "growth-returns.csv"
    # Bytes, so Windows does not write \r\n: the fingerprint is of the file's exact bytes.
    file.write_bytes(b"date,growth\n2024-01-31,0.01\n2024-02-29,-0.02\n2024-03-29,0.03\n")
    on_disk = DirectoryDatasetStore(tmp_path / "datasets")
    on_disk.put("growth-returns", file)

    assert store.get("growth-returns").fingerprint == on_disk.get("growth-returns").fingerprint


def test_a_call_and_a_distribution_on_one_day_are_refused_not_netted(
    sources: dict[str, Any], lake: Path
) -> None:
    store = SqlDatasetStore(sources, declared(lake))

    with pytest.raises(SqlError, match=r"Dataset 'flows'.*\(2024-02-29, I\) x2"):
        store.get("flows")
    assert store.get("flows-gross").column("I")[1] == pytest.approx(80.33)


def test_an_unknown_id_names_the_others_and_ids_are_sorted(
    sources: dict[str, Any], lake: Path
) -> None:
    store = SqlDatasetStore(sources, declared(lake))

    assert store.ids() == sorted(store.ids())
    with pytest.raises(UnknownDatasetError, match="No dataset 'nope'; the datasets are flows"):
        store.get("nope")


def test_a_derived_dataset_is_python_over_both_sources(sources: dict[str, Any], lake: Path) -> None:
    def desk_vs_price(fetch: Any) -> Rows:
        returns = fetch(
            "oms", "SELECT day, ret FROM returns WHERE desk = :d ORDER BY day", {"d": "growth"}
        )
        prices = fetch(
            "lake",
            "SELECT CAST(dt AS VARCHAR), px FROM "
            f"read_parquet('{lake}/dt=*/*.parquet', hive_partitioning=true) ORDER BY 1",
        )
        rows = tuple(
            (day, ret, px) for (day, ret), (_, px) in zip(returns.rows, prices.rows, strict=True)
        )
        return Rows(("date", "ret", "px"), rows)

    store = SqlDatasetStore(sources, [], derived={"growth-vs-px": desk_vs_price})

    assert store.ids() == ["growth-vs-px"]
    assert store.get("growth-vs-px").column("px") == [100.0, 101.0, 99.5]


def test_a_calendar_gives_every_day_a_row_and_fill_replaces_a_missing_cell(
    sources: dict[str, Any],
) -> None:
    item = SqlDataset(
        "sparse",
        "SELECT day, fund, amount FROM flows",
        duplicates="sum",
        fill=0.0,
        calendar="SELECT DISTINCT day FROM returns ORDER BY day",
        source="oms",
    )

    assert SqlDatasetStore(sources, [item]).get("sparse").column("I") == [
        5.0,
        pytest.approx(80.33),
        0.0,
    ]


def test_a_dataset_naming_no_or_an_unknown_source_is_refused_at_construction(
    sources: dict[str, Any],
) -> None:
    with pytest.raises(ValueError, match="names no source, and there are several"):
        SqlDatasetStore(sources, [SqlDataset("a", "SELECT 1")])
    with pytest.raises(ValueError, match="reads source 'nope'"):
        SqlDatasetStore(sources, [SqlDataset("a", "SELECT 1", source="nope")])
    with pytest.raises(ValueError, match="Two datasets share an id"):
        SqlDatasetStore(sources, [SqlDataset("a", "SELECT 1", source="oms")] * 2)
    with pytest.raises(ValueError, match="stamp names no source"):
        SqlDatasetStore(sources, [], stamps={"nope": 1})


# ---- change stamps -----------------------------------------------------------------------------


def _bump(path: Path) -> None:
    with closing(sqlite3.connect(path)) as db:
        db.execute("UPDATE returns SET ret = 0.5 WHERE desk = 'growth' AND day = '2024-01-31'")
        db.execute("UPDATE version SET n = n + 1")
        db.commit()


def test_a_version_query_rebuilds_a_dataset_only_when_the_version_changes(
    sources: dict[str, Any], lake: Path, oms: Path
) -> None:
    store = SqlDatasetStore(sources, declared(lake), stamps={"oms": "SELECT n FROM version"})
    first = store.get("growth-returns")
    with closing(sqlite3.connect(oms)) as db:  # a change that did not bump the version
        db.execute("UPDATE returns SET ret = 0.5 WHERE desk = 'growth'")
        db.commit()

    assert store.get("growth-returns") is first
    _bump(oms)
    assert store.get("growth-returns").column("growth")[0] == 0.5


def test_a_ttl_rebuilds_after_the_time_to_live_by_the_clock(
    sources: dict[str, Any], lake: Path, oms: Path
) -> None:
    now = [1000.0]
    store = SqlDatasetStore(
        sources, declared(lake), stamps={"oms": 60, "lake": 60}, clock=lambda: now[0]
    )
    before = store.get("growth-returns")
    _bump(oms)

    now[0] += 30
    assert store.get("growth-returns") is before
    now[0] += 60
    assert store.get("growth-returns").column("growth")[0] == 0.5


def test_a_callable_stamp_and_a_stamp_on_an_unused_source(
    sources: dict[str, Any], lake: Path, oms: Path
) -> None:
    mtime = [0]
    store = SqlDatasetStore(
        sources, declared(lake), stamps={"oms": lambda: mtime[0], "lake": 10**9}
    )
    first = store.get("growth-returns")
    _bump(oms)
    assert store.get("growth-returns") is first

    mtime[0] = 1
    assert store.get("growth-returns") is not first


# ---- a declared column that is missing, and a query the database refuses -----------------------


def test_a_declared_column_missing_from_the_result_names_the_dataset_and_the_columns_found(
    sources: dict[str, Any], lake: Path
) -> None:
    item = SqlDataset(
        "returns-renamed",
        "SELECT day, desk, ret AS ret_1d FROM returns",
        index="date",
        column="desk",
        value="ret",
        source="oms",
    )

    with pytest.raises(DatasetReadError) as caught:
        SqlDatasetStore(sources, [item]).get("returns-renamed")

    assert str(caught.value) == (
        "Dataset 'returns-renamed' has no columns 'date', 'ret', which its declaration names; "
        "its columns are day, desk, ret_1d."
    )


def test_a_wide_dataset_without_its_index_column_names_it(sources: dict[str, Any]) -> None:
    item = SqlDataset(
        "wide-renamed",
        "SELECT day, desk, ret FROM returns",
        shape="wide",
        index="date",
        source="oms",
    )

    with pytest.raises(DatasetReadError, match=r"has no column 'date', which its declaration"):
        SqlDatasetStore(sources, [item]).get("wide-renamed")


def test_a_query_the_database_refuses_is_a_dataset_error_that_shows_no_sql(
    sources: dict[str, Any],
) -> None:
    item = SqlDataset(
        "returns-stale", "SELECT day AS date, desk, ret_1d AS ret FROM returns", source="oms"
    )

    with pytest.raises(DatasetReadError) as caught:
        SqlDatasetStore(sources, [item]).get("returns-stale")

    message = str(caught.value)
    assert message.startswith("Dataset 'returns-stale' could not be read")
    assert "SELECT" not in message and "FROM" not in message and "ret_1d" not in message
    assert caught.value.__cause__ is not None  # the driver's error is kept for the chain


def test_a_failed_read_is_not_cached_and_the_repaired_dataset_is_read_again(
    sources: dict[str, Any], oms: Path
) -> None:
    item = SqlDataset("returns-live", "SELECT day AS date, desk, ret FROM returns", source="oms")
    store = SqlDatasetStore(sources, [item], stamps={"oms": "SELECT n FROM version"})
    before = store.get("returns-live")
    with closing(
        sqlite3.connect(oms)
    ) as db:  # the data team renames a column and bumps the version
        db.execute("ALTER TABLE returns RENAME COLUMN ret TO ret_1d")
        db.execute("UPDATE version SET n = n + 1")
        db.commit()

    for _ in range(2):  # each call reads again: the failure was not cached
        with pytest.raises(DatasetReadError):
            store.get("returns-live")
    with closing(sqlite3.connect(oms)) as db:
        db.execute("ALTER TABLE returns RENAME COLUMN ret_1d TO ret")
        db.execute("UPDATE version SET n = n + 1")
        db.commit()

    after = store.get("returns-live")
    assert after is not before
    assert after.column("growth")[0] == 0.01


def test_a_refused_dataset_with_a_broken_declaration_still_looks_missing(
    sources: dict[str, Any], lake: Path, tmp_path: Path
) -> None:
    rules = tmp_path / "access.toml"
    rules.write_text('[groups.growth]\ndatasets = ["growth-*"]\ntools = ["list_datasets"]\n')
    broken = SqlDataset(
        "returns-broken", "SELECT day AS date, desk, ret_1d FROM returns", source="oms"
    )
    guarded = GuardedDatasetStore(
        SqlDatasetStore(sources, [*declared(lake), broken]), FileAccessPolicy(rules)
    )

    with acting_as(Caller("carol")), pytest.raises(UnknownDatasetError, match="none have been"):
        guarded.get("returns-broken")


# ---- per-caller access and the server ---------------------------------------------------------


def test_a_guarded_store_shows_a_refused_dataset_as_missing(
    sources: dict[str, Any], lake: Path, tmp_path: Path
) -> None:
    rules = tmp_path / "access.toml"
    rules.write_text('[groups.growth]\ndatasets = ["growth-*"]\ntools = ["list_datasets"]\n')
    guarded = GuardedDatasetStore(SqlDatasetStore(sources, declared(lake)), FileAccessPolicy(rules))

    with acting_as(Caller("alice", groups=frozenset({"growth"}))):
        assert guarded.ids() == ["growth-returns"]
        assert guarded.get("growth-returns").columns == {"growth": [0.01, -0.02, 0.03]}
        with pytest.raises(UnknownDatasetError, match="the datasets are growth-returns"):
            guarded.get("returns-all")
    with acting_as(Caller("carol")), pytest.raises(UnknownDatasetError, match="none have been"):
        guarded.get("growth-returns")


def test_the_store_serves_through_the_platform_with_an_access_file(
    sources: dict[str, Any], lake: Path, tmp_path: Path
) -> None:
    rules = tmp_path / "access.toml"
    rules.write_text(
        '[groups.growth]\ntools = ["analyze_performance", "list_datasets"]\n'
        'datasets = ["growth-*"]\n'
    )
    keys = tmp_path / "keys.toml"
    key = add_key(keys, "alice", ["tools:list", "tools:call"], groups=["growth"])
    headers = {"Authorization": f"Bearer {key}"}
    args = {"returns": {"dataset": "growth-returns", "column": "growth"}}

    with (
        build_server_app(
            [],
            datasets=SqlDatasetStore(sources, declared(lake)),
            policy=PermissionPolicy(),
            access=FileAccessPolicy(rules),
            keys=keys,
            runs=MemoryRunStore(),
        ) as app,
        TestClient(app, base_url=BASE) as client,
    ):
        listed = client.post(
            "/v1/tools/list_datasets/call", json={"arguments": {}}, headers=headers
        )
        allowed = client.post(
            "/v1/tools/analyze_performance/call", json={"arguments": args}, headers=headers
        )
        refused = client.post(
            "/v1/tools/analyze_performance/call",
            json={"arguments": {"returns": {"dataset": "returns-all", "column": "growth"}}},
            headers=headers,
        )

    assert [item["id"] for item in listed.json()["output"]["datasets"]] == ["growth-returns"]
    assert allowed.json()["status"] == "ok"
    assert allowed.json()["data_versions"] == {
        "dataset:growth-returns": SqlDatasetStore(sources, declared(lake))
        .get("growth-returns")
        .fingerprint
    }
    assert refused.json()["status"] == "error"
    assert "No dataset 'returns-all'" in refused.json()["error"]


def test_check_reads_the_source_again_when_a_cached_dataset_would_still_answer(
    oms: Path,
) -> None:
    engine = sql.connect(f"sqlite:///{oms}")
    store = SqlDatasetStore(
        {"oms": engine},
        [SqlDataset("returns", "SELECT day, desk, ret FROM returns", index_name="day")],
        stamps={"oms": 3600},
    )
    try:
        store.check()
        assert store.get("returns").rows == 3
        oms.unlink()

        assert store.get("returns").rows == 3  # the dataset is cached for an hour
        with pytest.raises(ConnectionError, match="oms"):
            store.check()  # the probe is not cached, so it sees the deleted file
    finally:
        engine.dispose()


def test_check_gives_up_on_a_source_that_hangs_and_starts_no_second_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    from quant_platform import sql_store

    release = threading.Event()
    attempts: list[int] = []

    class HungEngine:
        def dispose(self) -> None:
            pass

        def connect(self) -> None:
            attempts.append(1)
            release.wait()
            raise RuntimeError("released")

    monkeypatch.setattr(sql_store, "PROBE_TIMEOUT_SECONDS", 0.05)
    store = SqlDatasetStore({"hung": HungEngine()}, [])  # type: ignore[dict-item]
    try:
        with pytest.raises(ConnectionError, match="hung"):
            store.check()
        with pytest.raises(ConnectionError, match="hung"):
            store.check()  # the first probe still runs, so none is started
        assert len(attempts) == 1
    finally:
        release.set()
