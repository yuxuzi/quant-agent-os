"""`analyze_risk` with the weights by reference: a column of assets, or one date of a frame."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from quant_agent_os.data import DirectoryDatasetStore, GuardedDatasetStore
from quant_agent_os.runtime import ToolCall, ToolExecutor
from quant_agent_os.tools import Caller, FileAccessPolicy, Permission, PermissionPolicy
from quant_agent_os.tools.access import acting_as
from quant_analytics.tools import analytics_tools

POLICY = PermissionPolicy(granted=PermissionPolicy().granted | {Permission.READ_LOCAL_DATA})
# Two uncorrelated assets: A alternates +1%/-1% and B +2%/+2%/-2%/-2% over four days.
RETURNS = (
    "date,A,B,C\n"
    "2024-01-02,0.01,0.02,0.0\n"
    "2024-01-03,-0.01,0.02,0.0\n"
    "2024-01-04,0.01,-0.02,0.0\n"
    "2024-01-05,-0.01,-0.02,0.0\n"
)
# The book: 50/50 on the 3rd, then 60% A and 40% B on the 5th; C is never held.
WEIGHTS = "date,A,B,C\n2024-01-03,0.5,0.5,NaN\n2024-01-05,0.6,0.4,NaN\n"
TARGETS = "asset,target,limit\nA,0.6,1.0\nB,0.4,1.0\nC,NaN,1.0\n"


@pytest.fixture
def store(tmp_path: Path) -> DirectoryDatasetStore:
    datasets = DirectoryDatasetStore(tmp_path / "datasets")
    for name, text in {"returns": RETURNS, "weights_book": WEIGHTS, "targets": TARGETS}.items():
        path = tmp_path / f"{name}.csv"
        path.write_text(text, "utf-8")
        datasets.put(name, path)
    return datasets


def _risk(store: Any, **arguments: Any) -> Any:
    return ToolExecutor(analytics_tools(store), POLICY).execute(
        ToolCall(
            tool="analyze_risk",
            arguments={
                "portfolio_id": "book",
                "asset_returns": {"dataset": "returns"},
                "periods_per_year": 252,
                **arguments,
            },
        )
    )


def _volatility(a: float, b: float) -> float:
    # Sample variances: A's is 4 * 0.01**2 / 3, B's 4 * 0.02**2 / 3, and their covariance is 0.
    variance = a * a * 4 * 0.01**2 / 3 + b * b * 4 * 0.02**2 / 3
    return math.sqrt(variance * 252)


def test_the_latest_row_of_a_dated_frame_is_the_book(store: DirectoryDatasetStore) -> None:
    record = _risk(store, weights_ref={"dataset": "weights_book"})

    assert record.status == "ok", record.error
    output = record.output
    assert output["volatility"] == pytest.approx(_volatility(0.6, 0.4))
    assert set(output["contributions"]) == {"A", "B"}
    assert output["conventions"]["weights"].startswith(
        "From weights_book on 2024-01-05, the latest date;"
    )
    assert "Left out 1 assets with no weight in weights_book on 2024-01-05" in " ".join(
        output["warnings"]
    )
    assert set(output["data_versions"]) == {"dataset:weights_book", "dataset:returns"}


def test_by_reference_equals_the_same_weights_inline(store: DirectoryDatasetStore) -> None:
    referenced = _risk(store, weights_ref={"dataset": "weights_book"}).output
    inline = _risk(store, weights={"A": 0.6, "B": 0.4}).output

    for key in ("volatility", "contributions", "parametric_var", "gross_exposure"):
        assert referenced[key] == inline[key]


def test_as_of_picks_an_earlier_date(store: DirectoryDatasetStore) -> None:
    record = _risk(store, weights_ref={"dataset": "weights_book", "as_of": "2024-01-03"})

    assert record.status == "ok", record.error
    assert record.output["volatility"] == pytest.approx(_volatility(0.5, 0.5))
    assert record.output["conventions"]["weights"].startswith("From weights_book on 2024-01-03;")


def test_a_column_of_a_dataset_whose_rows_are_assets(store: DirectoryDatasetStore) -> None:
    record = _risk(store, weights_ref={"dataset": "targets", "column": "target"})

    assert record.status == "ok", record.error
    assert record.output["volatility"] == pytest.approx(_volatility(0.6, 0.4))
    assert record.output["conventions"]["weights"].startswith("From targets.target;")


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({}, "Pass the weights one way"),
        (
            {"weights": {"A": 1.0}, "weights_ref": {"dataset": "weights_book"}},
            "Pass the weights one way",
        ),
        (
            {"weights_ref": {"dataset": "weights_book", "column": "A"}},
            "one row per date and one column per asset, so column does not apply",
        ),
        (
            {"weights_ref": {"dataset": "weights_book", "as_of": "2024-01-04"}},
            "no row for 2024-01-04; its dates run from 2024-01-03 to 2024-01-05",
        ),
        (
            {"weights_ref": {"dataset": "weights_book", "as_of": "soon"}},
            "as_of must be a date, such as 2024-01-05",
        ),
        (
            {"weights_ref": {"dataset": "targets"}},
            "name the weights column in weights_ref, one of target, limit",
        ),
        (
            {"weights_ref": {"dataset": "targets", "column": "target", "as_of": "2024-01-05"}},
            "so as_of does not apply",
        ),
        ({"weights_ref": {"dataset": "nowhere"}}, "No dataset 'nowhere'"),
    ],
)
def test_refusals_name_what_to_fix(
    store: DirectoryDatasetStore, arguments: dict[str, Any], message: str
) -> None:
    record = _risk(store, **arguments)

    assert record.status != "ok"
    assert message in (record.error or "")


def test_without_a_store_the_weights_cannot_be_referenced() -> None:
    record = ToolExecutor(analytics_tools(None), POLICY).execute(
        ToolCall(
            tool="analyze_risk",
            arguments={
                "portfolio_id": "book",
                "weights_ref": {"dataset": "weights_book"},
                "asset_returns": {"A": [0.01, -0.01], "B": [0.02, 0.02]},
                "periods_per_year": 252,
            },
        )
    )

    assert record.status != "ok"
    assert "This server has no datasets" in (record.error or "")


def test_a_refused_weights_dataset_looks_missing(
    store: DirectoryDatasetStore, tmp_path: Path
) -> None:
    rules = tmp_path / "access.toml"
    rules.write_text(
        '[groups.research]\ntools = ["analyze_risk"]\ndatasets = ["returns"]\n'
        '[groups.risk]\ntools = ["analyze_risk"]\ndatasets = ["returns", "weights_*"]\n'
    )
    guarded = GuardedDatasetStore(store, FileAccessPolicy(rules))
    arguments = {"weights_ref": {"dataset": "weights_book"}}

    with acting_as(Caller("rita", groups=frozenset({"risk"}))):
        allowed = _risk(guarded, **arguments)
    with acting_as(Caller("rhea", groups=frozenset({"research"}))):
        refused = _risk(guarded, **arguments)
        missing = _risk(guarded, weights_ref={"dataset": "weights_none"})
    with acting_as(None):
        nobody = _risk(guarded, **arguments)

    assert allowed.status == "ok", allowed.error
    assert refused.status == missing.status == nobody.status == "error"
    assert refused.error == (missing.error or "").replace("weights_none", "weights_book")
