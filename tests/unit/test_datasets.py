"""Datasets (plan step P2.3): CSV tables that tools read by id, with a fingerprint per version."""

import hashlib
import json
import math
from datetime import date, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quant_agent_os.data import (
    DatasetError,
    DirectoryDatasetStore,
    UnknownColumnError,
    UnknownDatasetError,
    dataset_resources,
    dataset_tools,
    infer_periods_per_year,
    parse_dataset,
    parse_date,
)
from quant_agent_os.mcp import read_resource
from quant_agent_os.runtime import ToolExecutor
from quant_agent_os.testing import check_tool
from quant_agent_os.tools import PermissionPolicy, ToolRegistry
from quant_platform.cli import app

PRICES = b"date,fund,market\n2024-01-02,0.01,0.005\n2024-01-03,-0.02,-0.01\n2024-01-04,0.03,0.02\n"


def _store(tmp_path: Path) -> DirectoryDatasetStore:
    source = tmp_path / "returns.csv"
    source.write_bytes(PRICES)
    store = DirectoryDatasetStore(tmp_path / "datasets")
    store.put("returns", source)
    return store


def test_a_text_first_column_is_the_index_and_the_rest_are_numbers() -> None:
    dataset = parse_dataset("returns", PRICES)

    assert dataset.index_name == "date" and dataset.index == [
        "2024-01-02",
        "2024-01-03",
        "2024-01-04",
    ]
    assert dataset.column("fund") == [0.01, -0.02, 0.03]
    assert dataset.rows == 3
    assert dataset.fingerprint == hashlib.sha256(PRICES).hexdigest()


def test_an_all_numeric_table_has_no_index() -> None:
    dataset = parse_dataset("plain", b"a,b\n1,2\n3,4\n")

    assert dataset.index is None and list(dataset.columns) == ["a", "b"]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"a,b\n1,2\n", "at least two rows"),
        (b"a,a\n1,2\n3,4\n", "unique"),
        (b"d,a\nx,1\ny,oops\n", "line 3, column 'a': 'oops'"),
        (b"a,b\n1,2\n3\n", "line 3 has 1 cells"),
        (b"a,b\n1,inf\n3,4\n", "not a number or a missing value"),
        (b"date,a\n,1\n2024-01-03,2\n", "line 2 has no index value"),
        (b"date,a\n2024-01-02,1\n2024-01-02,2\n", "repeats index values"),
        (b"date,a\n2024-01-03,1\n2024-01-02,2\n", "not in date order"),
    ],
)
def test_a_malformed_table_names_the_problem(data: bytes, message: str) -> None:
    with pytest.raises(DatasetError, match=message):
        parse_dataset("bad", data)


@pytest.mark.parametrize(
    "data",
    [
        b"date,fund\n20240102,0.01\n20240103,0.02\n20240104,0.03\n",  # CRSP style
        b"anything,fund\n20240102,0.01\n20240103,0.02\n20240104,0.03\n",
    ],
)
def test_compact_dates_are_the_index_and_never_a_data_column(data: bytes) -> None:
    dataset = parse_dataset("crsp", data)

    assert list(dataset.columns) == ["fund"]
    assert dataset.dates == [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]


def test_year_month_integers_are_dates_as_in_french_factor_files() -> None:
    dataset = parse_dataset("ff", b"ym,mkt\n199201,0.01\n199202,0.02\n199203,0.03\n")

    assert dataset.index_name == "ym" and dataset.periods_per_year == 12


def test_a_first_column_named_like_a_date_is_the_index_even_when_numeric() -> None:
    dataset = parse_dataset("t", b"period,a\n1,0.1\n2,0.2\n3,0.3\n")

    assert dataset.index == ["1", "2", "3"] and dataset.dates is None
    assert list(dataset.columns) == ["a"]


def test_index_column_names_any_column_and_a_wrong_name_is_refused() -> None:
    data = b"fund,obs\n0.1,1\n0.2,2\n0.3,3\n"

    assert parse_dataset("x", data, index_column="obs").index == ["1", "2", "3"]
    assert list(parse_dataset("x", data, index_column="obs").columns) == ["fund"]
    with pytest.raises(DatasetError, match=r"no column 'day'.*fund, obs"):
        parse_dataset("x", data, index_column="day")


@pytest.mark.parametrize("cell", [b"", b"NaN", b"nan", b"NA", b"n/a", b"null", b" "])
def test_missing_cells_are_kept_as_nan_and_counted(cell: bytes) -> None:
    data = b"date,a,b\n2024-01-02,0.01," + cell + b"\n2024-01-03,0.02,0.03\n2024-01-04,0.01,0.01\n"

    dataset = parse_dataset("gaps", data)

    assert math.isnan(dataset.column("b")[0]) and dataset.column("b")[1:] == [0.03, 0.01]
    assert dataset.info().missing_cells == 1


def test_a_missing_value_in_a_numeric_first_column_does_not_make_it_an_index() -> None:
    dataset = parse_dataset("plain", b"a,b\n1,2\n,4\n5,6\n")

    assert dataset.index is None and math.isnan(dataset.column("a")[1])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2024-01-31", date(2024, 1, 31)),
        ("2024/01/31", date(2024, 1, 31)),
        ("2024-01-31T16:00:00", date(2024, 1, 31)),
        ("20240131", date(2024, 1, 31)),
        ("2024-01", date(2024, 1, 1)),
        ("202401", date(2024, 1, 1)),
        ("01/02/2024", None),  # day-first or month-first: ambiguous, so not guessed
        ("20241301", None),
        ("123456", None),
        ("2024", None),
        ("fund-a", None),
    ],
)
def test_parse_date_reads_unambiguous_forms_only(text: str, expected: date | None) -> None:
    assert parse_date(text) == expected


def _every(days: int, count: int = 30) -> list[date]:
    return [date(2020, 1, 6) + timedelta(days=days * step) for step in range(count)]


def _business_days(count: int = 60) -> list[date]:
    days = (date(2020, 1, 6) + timedelta(days=step) for step in range(count * 2))
    return [day for day in days if day.weekday() < 5][:count]


@pytest.mark.parametrize(
    ("dates", "expected"),
    [
        (_business_days(), 252),
        (_every(1), 365),  # weekends trade: crypto, say
        (_every(7), 52),
        (_every(30), 12),
        (_every(91), 4),
        (_every(182), 2),
        (_every(365), 1),
        (_every(14), None),  # no standard frequency, so the caller must say
        (_every(1, count=2), None),
    ],
)
def test_the_frequency_comes_from_the_dates(dates: list[date], expected: int | None) -> None:
    assert infer_periods_per_year(dates) == expected


def test_the_store_keeps_an_index_choice_and_the_file_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "t.csv"
    source.write_bytes(b"fund,obs\n0.1,1\n0.2,2\n0.3,3\n")
    store = DirectoryDatasetStore(tmp_path / "datasets")

    store.put("t", source, index_column="obs")
    assert store.get("t").index == ["1", "2", "3"]
    assert DirectoryDatasetStore(tmp_path / "datasets").get("t").index_name == "obs"
    assert store.get("t").fingerprint == hashlib.sha256(source.read_bytes()).hexdigest()
    assert store.ids() == ["t"]

    store.put("t", source)
    assert store.get("t").index is None


def test_the_store_reuses_a_parse_until_the_file_changes(tmp_path: Path) -> None:
    store = _store(tmp_path)

    first = store.get("returns")
    assert store.get("returns") is first

    (tmp_path / "datasets" / "returns.csv").write_bytes(PRICES + b"2024-01-05,0.01,0.01\n")
    assert store.get("returns").rows == 4


def test_the_store_adds_replaces_and_finds_datasets(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.ids() == ["returns"]
    assert store.get("returns").info().columns == ["fund", "market"]
    with pytest.raises(UnknownDatasetError, match="the datasets are returns"):
        store.get("prices")
    with pytest.raises(UnknownColumnError, match="its columns are fund, market"):
        store.get("returns").column("bench")
    with pytest.raises(DatasetError, match="Dataset id"):
        store.get("../secrets")


def test_a_bad_file_is_refused_and_leaves_the_earlier_version(tmp_path: Path) -> None:
    store = _store(tmp_path)
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,x\n2,3\n")

    with pytest.raises(DatasetError):
        store.put("returns", bad)
    assert store.get("returns").fingerprint == hashlib.sha256(PRICES).hexdigest()


def test_list_datasets_describes_without_values_and_honours_the_contract(tmp_path: Path) -> None:
    (spec,) = dataset_tools(_store(tmp_path))

    record = check_tool(spec, {})
    assert record.output is not None
    (info,) = record.output["datasets"]
    assert info["columns"] == ["fund", "market"] and info["last_index"] == "2024-01-04"
    assert record.data_versions == {"dataset:returns": hashlib.sha256(PRICES).hexdigest()}
    assert check_tool(spec, {"dataset_id": "nope"}, expect="error").error


def test_the_dataset_resource_reads_through_list_datasets(tmp_path: Path) -> None:
    executor = ToolExecutor(ToolRegistry(dataset_tools(_store(tmp_path))), PermissionPolicy())

    contents = read_resource(executor, dataset_resources(), "qaos://dataset/returns")

    assert json.loads(contents["text"])["datasets"][0]["id"] == "returns"


def test_the_cli_pushes_and_lists_datasets(tmp_path: Path) -> None:
    source = tmp_path / "returns.csv"
    source.write_bytes(PRICES)
    directory = tmp_path / "datasets"

    pushed = CliRunner().invoke(
        app, ["data", "add", str(source), "--id", "returns", "--dir", str(directory)]
    )
    listed = CliRunner().invoke(app, ["data", "list", "--dir", str(directory)])
    refused = CliRunner().invoke(
        app, ["data", "add", str(source), "--id", "Bad Id", "--dir", str(directory)]
    )

    assert pushed.exit_code == 0 and "3 rows, columns fund, market" in pushed.output
    assert listed.exit_code == 0 and "returns" in listed.output
    assert refused.exit_code == 1


def test_the_cli_reports_the_frequency_or_warns_and_takes_an_index(tmp_path: Path) -> None:
    source = tmp_path / "t.csv"
    source.write_text("fund,day\n0.1,20240102\n,20240103\n0.3,20240104\n")
    directory = ["--dir", str(tmp_path / "datasets")]

    undated = CliRunner().invoke(app, ["data", "add", str(source), "--id", "t", *directory])
    dated = CliRunner().invoke(
        app, ["data", "add", str(source), "--id", "t", "--index", "day", *directory]
    )

    assert "no dates showing a frequency" in undated.output
    assert "252 periods per year" in dated.output and "1 cells are missing" in dated.output


def test_the_cli_keeps_each_tenants_datasets_apart(tmp_path: Path) -> None:
    source = tmp_path / "returns.csv"
    source.write_bytes(PRICES)
    root = ["--dir", str(tmp_path / "datasets")]

    pushed = CliRunner().invoke(
        app, ["data", "add", str(source), "--id", "returns", "--tenant", "acme", *root]
    )
    refused = CliRunner().invoke(
        app, ["data", "add", str(source), "--id", "returns", "--tenant", "../x", *root]
    )

    assert pushed.exit_code == 0
    assert DirectoryDatasetStore(tmp_path / "datasets" / "acme").ids() == ["returns"]
    assert DirectoryDatasetStore(tmp_path / "datasets").ids() == []
    assert refused.exit_code == 1 and "Tenant id" in refused.output
