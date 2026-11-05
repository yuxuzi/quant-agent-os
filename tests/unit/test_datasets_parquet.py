"""Parquet files added as datasets: stored as CSV, then read like any other dataset."""

from __future__ import annotations

import hashlib
import math
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from quant_agent_os.data import DatasetError, DirectoryDatasetStore, datasets, parse_dataset
from quant_platform.cli import app

pandas = pytest.importorskip("pandas")
pyarrow = pytest.importorskip("pyarrow")
parquet = pytest.importorskip("pyarrow.parquet")

VALUES = [0.1 + 0.2, math.nan, 1e-17, -0.25, 0.5]
DAYS = pandas.bdate_range("2024-01-02", periods=len(VALUES))
DATES = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5), date(2024, 1, 8)]


def _assert_same_values(actual: list[float], expected: list[float]) -> None:
    assert len(actual) == len(expected)
    for left, right in zip(actual, expected, strict=True):
        assert left == right or (math.isnan(left) and math.isnan(right))


def _store(tmp_path: Path) -> DirectoryDatasetStore:
    return DirectoryDatasetStore(tmp_path / "datasets")


def _table(path: Path, **columns: Any) -> Path:
    """Write columns with no pandas metadata, as polars or Spark would."""
    parquet.write_table(pyarrow.table(columns), path)
    return path


@pytest.mark.parametrize("compression", ["snappy", "zstd", "gzip", None])
def test_a_frame_written_to_parquet_reads_back_exactly(
    tmp_path: Path, compression: str | None
) -> None:
    source = tmp_path / "fund.parquet"
    frame = pandas.DataFrame({"fund": VALUES}, index=DAYS.rename("date"))
    frame.to_parquet(source, engine="pyarrow", compression=compression)
    store = _store(tmp_path)

    info = store.put("fund", source)

    dataset = store.get("fund")
    _assert_same_values(dataset.column("fund"), VALUES)
    assert dataset.dates == DATES
    assert info.periods_per_year == 252
    assert info.missing_cells == 1


def test_the_stored_file_is_csv_and_the_fingerprint_is_of_that_file(tmp_path: Path) -> None:
    source = tmp_path / "fund.parquet"
    pandas.DataFrame({"fund": VALUES}, index=DAYS.rename("date")).to_parquet(source)
    store = _store(tmp_path)

    info = store.put("fund", source)

    stored = store.root / "fund.csv"
    assert info.fingerprint == hashlib.sha256(stored.read_bytes()).hexdigest()
    assert parse_dataset("fund", stored.read_bytes()).info() == info
    assert store.ids() == ["fund"]


def test_an_unnamed_date_index_is_the_index(tmp_path: Path) -> None:
    source = tmp_path / "fund.parquet"
    pandas.DataFrame({"fund": VALUES}, index=DAYS).to_parquet(source)
    store = _store(tmp_path)

    info = store.put("fund", source)

    assert info.index_name == "date"
    assert store.get("fund").dates == DATES


def test_a_row_counter_is_not_an_index(tmp_path: Path) -> None:
    source = tmp_path / "fund.parquet"
    pandas.DataFrame({"fund": VALUES}).to_parquet(source)

    info = _store(tmp_path).put("fund", source)

    assert info.index_name is None
    assert info.rows == len(VALUES)


def test_a_date_column_first_in_a_plain_table_is_the_index(tmp_path: Path) -> None:
    source = _table(
        tmp_path / "fund.parquet", date=pyarrow.array(DATES, pyarrow.date32()), fund=VALUES
    )
    store = _store(tmp_path)

    info = store.put("fund", source)

    assert info.index_name == "date"
    assert info.columns == ["fund"]
    assert store.get("fund").dates == DATES


def test_a_date_column_elsewhere_is_named_with_index_and_stays_named(tmp_path: Path) -> None:
    source = _table(
        tmp_path / "fund.parquet", fund=VALUES, day=pyarrow.array(DATES, pyarrow.date32())
    )
    store = _store(tmp_path)

    with pytest.raises(DatasetError, match="not a number"):
        store.put("fund", source)
    info = store.put("fund", source, index_column="day")

    assert info.index_name == "day"
    assert DirectoryDatasetStore(store.root).get("fund").dates == DATES


def test_a_timezone_aware_timestamp_gives_the_dates(tmp_path: Path) -> None:
    stamps = pyarrow.array(list(DAYS.tz_localize("UTC")), pyarrow.timestamp("ns", tz="UTC"))
    source = _table(tmp_path / "fund.parquet", date=stamps, fund=VALUES)
    store = _store(tmp_path)

    store.put("fund", source)

    assert store.get("fund").dates == DATES


def test_a_decimal_and_a_float32_column_are_numbers(tmp_path: Path) -> None:
    source = _table(
        tmp_path / "fund.parquet",
        date=pyarrow.array(DATES, pyarrow.date32()),
        small=pyarrow.array([1.5, 2.5, 3.5, 4.5, 5.5], pyarrow.float32()),
        exact=pyarrow.array([1, 2, 3, 4, 5], pyarrow.decimal128(10, 2)),
    )
    store = _store(tmp_path)

    store.put("fund", source)

    assert store.get("fund").column("small") == [1.5, 2.5, 3.5, 4.5, 5.5]
    assert store.get("fund").column("exact") == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_the_suffix_is_matched_in_any_case(tmp_path: Path) -> None:
    source = tmp_path / "FUND.PQ"
    pandas.DataFrame({"fund": VALUES}, index=DAYS.rename("date")).to_parquet(source)

    assert _store(tmp_path).put("fund", source).rows == len(VALUES)


@pytest.mark.parametrize(
    ("write", "message"),
    [
        (lambda path: path.write_bytes(b"not parquet at all"), "cannot be read as Parquet"),
        (
            lambda path: _table(
                path,
                date=pyarrow.array(DATES, pyarrow.date32()),
                nested=[[1.0], [2.0], [3.0], [4.0], [5.0]],
            ),
            "not a number",
        ),
        (
            lambda path: _table(
                path, date=pyarrow.array(DATES[::-1], pyarrow.date32()), fund=VALUES
            ),
            "not in date order",
        ),
    ],
)
def test_a_bad_file_is_refused_and_nothing_is_stored(
    tmp_path: Path, write: Callable[[Path], Any], message: str
) -> None:
    source = tmp_path / "fund.parquet"
    write(source)
    store = _store(tmp_path)

    with pytest.raises(DatasetError, match=message):
        store.put("fund", source)

    assert store.ids() == []


def test_the_limits_are_checked_before_the_file_is_decoded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "fund.parquet"
    pandas.DataFrame({"fund": VALUES}, index=DAYS.rename("date")).to_parquet(source)
    store = _store(tmp_path)

    def must_not_decode(*_: Any, **__: Any) -> None:
        raise AssertionError("the file was decoded")

    monkeypatch.setattr(parquet, "read_table", must_not_decode)
    with monkeypatch.context() as limited:
        limited.setattr(datasets, "MAX_ROWS", 2)
        with pytest.raises(DatasetError, match="exceeds 2 rows"):
            store.put("fund", source)
    with monkeypatch.context() as limited:
        limited.setattr(datasets, "MAX_FILE_BYTES", 2)
        with pytest.raises(DatasetError, match="exceeds 2 bytes"):
            store.put("fund", source)


@pytest.mark.parametrize("module", ["pyarrow", "pandas"])
def test_without_the_extra_the_refusal_names_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: str
) -> None:
    source = tmp_path / "fund.parquet"
    pandas.DataFrame({"fund": VALUES}, index=DAYS.rename("date")).to_parquet(source)
    monkeypatch.setitem(sys.modules, module, None)

    with pytest.raises(DatasetError, match=r"quant-agent-os\[parquet\]"):
        _store(tmp_path).put("fund", source)


def test_a_csv_file_is_still_stored_byte_for_byte(tmp_path: Path) -> None:
    source = tmp_path / "fund.csv"
    source.write_bytes(b"date,fund\r\n2024-01-02,0.10\r\n2024-01-03,0.20\r\n")
    store = _store(tmp_path)

    info = store.put("fund", source)

    assert (store.root / "fund.csv").read_bytes() == source.read_bytes()
    assert info.fingerprint == hashlib.sha256(source.read_bytes()).hexdigest()


def test_the_command_adds_a_parquet_file_under_its_name(tmp_path: Path) -> None:
    source = tmp_path / "returns.parquet"
    pandas.DataFrame({"fund": VALUES}, index=DAYS.rename("date")).to_parquet(source)
    directory = tmp_path / "datasets"

    result = CliRunner().invoke(app, ["data", "add", str(source), "--dir", str(directory)])

    assert result.exit_code == 0, result.output
    assert "Added dataset returns" in result.output
    assert DirectoryDatasetStore(directory).get("returns").rows == len(VALUES)


def test_the_command_reports_an_unreadable_file(tmp_path: Path) -> None:
    source = tmp_path / "returns.parquet"
    source.write_bytes(b"not parquet at all")

    result = CliRunner().invoke(
        app, ["data", "add", str(source), "--dir", str(tmp_path / "datasets")]
    )

    assert result.exit_code == 1
