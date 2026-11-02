"""Datasets (plan step P2.3): CSV tables that tools read by id, with a fingerprint per version."""

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quant_agent_os.cli import app
from quant_agent_os.data import (
    DatasetError,
    DirectoryDatasetStore,
    UnknownColumnError,
    UnknownDatasetError,
    dataset_resources,
    dataset_tools,
    parse_dataset,
)
from quant_agent_os.mcp import read_resource
from quant_agent_os.runtime import ToolExecutor
from quant_agent_os.testing import check_tool
from quant_agent_os.tools import PermissionPolicy, ToolRegistry

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
        (b"a,b\n1,nan\n3,4\n", "not a finite number"),
    ],
)
def test_a_malformed_table_names_the_problem(data: bytes, message: str) -> None:
    with pytest.raises(DatasetError, match=message):
        parse_dataset("bad", data)


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
        app, ["dataset", "push", str(source), "--id", "returns", "--dir", str(directory)]
    )
    listed = CliRunner().invoke(app, ["dataset", "list", "--dir", str(directory)])
    refused = CliRunner().invoke(
        app, ["dataset", "push", str(source), "--id", "Bad Id", "--dir", str(directory)]
    )

    assert pushed.exit_code == 0 and "3 rows, columns fund, market" in pushed.output
    assert listed.exit_code == 0 and "returns" in listed.output
    assert refused.exit_code == 1


def test_the_cli_keeps_each_tenants_datasets_apart(tmp_path: Path) -> None:
    source = tmp_path / "returns.csv"
    source.write_bytes(PRICES)
    root = ["--dir", str(tmp_path / "datasets")]

    pushed = CliRunner().invoke(
        app, ["dataset", "push", str(source), "--id", "returns", "--tenant", "acme", *root]
    )
    refused = CliRunner().invoke(
        app, ["dataset", "push", str(source), "--id", "returns", "--tenant", "../x", *root]
    )

    assert pushed.exit_code == 0
    assert DirectoryDatasetStore(tmp_path / "datasets" / "acme").ids() == ["returns"]
    assert DirectoryDatasetStore(tmp_path / "datasets").ids() == []
    assert refused.exit_code == 1 and "Tenant id" in refused.output
