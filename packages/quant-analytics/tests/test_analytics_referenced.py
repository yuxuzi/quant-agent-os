"""Series by reference (plan step P2.3): dataset columns instead of copied values."""

from pathlib import Path
from typing import Any

import pytest

from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.runtime import PlanStep, ToolExecutor
from quant_agent_os.testing import check_tool
from quant_agent_os.tools import Permission, PermissionPolicy
from quant_analytics.tools import analytics_tools

FUND = [0.012, -0.004, 0.021, -0.015, 0.008, 0.017, -0.006, 0.011, 0.002, -0.009, 0.014, 0.005]
MARKET = [0.010, -0.002, 0.015, -0.012, 0.006, 0.012, -0.004, 0.009, 0.001, -0.007, 0.010, 0.004]
BOND = [0.002, 0.003, -0.001, 0.004, 0.001, -0.002, 0.003, 0.000, 0.002, 0.001, -0.001, 0.002]


def _csv(path: Path, columns: dict[str, list[float]], start: int = 1) -> Path:
    rows = ["month," + ",".join(columns)]
    rows += [
        f"2024-{month:02d}," + ",".join(str(values[row]) for values in columns.values())
        for row, month in enumerate(range(start, start + 12))
    ]
    path.write_text("\n".join(rows) + "\n")
    return path


@pytest.fixture
def store(tmp_path: Path) -> DirectoryDatasetStore:
    datasets = DirectoryDatasetStore(tmp_path / "datasets")
    datasets.put(
        "returns", _csv(tmp_path / "r.csv", {"fund": FUND, "market": MARKET, "bond": BOND})
    )
    datasets.put("factors", _csv(tmp_path / "f.csv", {"mkt": MARKET}))
    return datasets


def _call(store: DirectoryDatasetStore, tool: str, arguments: dict[str, Any]) -> Any:
    executor = ToolExecutor(analytics_tools(store), PermissionPolicy())
    return executor.execute(PlanStep(tool=tool, arguments=arguments))


def test_performance_by_reference_equals_performance_by_value(store: DirectoryDatasetStore) -> None:
    by_value = _call(
        store,
        "analyze_performance",
        {"series_id": "returns.fund", "returns": FUND, "benchmark": MARKET},
    )
    by_reference = _call(
        store,
        "analyze_performance",
        {
            "returns": {"dataset": "returns", "column": "fund"},
            "benchmark": {"dataset": "returns", "column": "market"},
        },
    )

    assert by_reference.status == "ok", by_reference.error
    assert {**by_reference.output, "data_versions": {}} == by_value.output
    assert by_reference.data_versions == {"dataset:returns": store.get("returns").fingerprint}


def test_risk_takes_assets_and_factors_by_reference(store: DirectoryDatasetStore) -> None:
    by_value = _call(
        store,
        "analyze_risk",
        {
            "portfolio_id": "book",
            "weights": {"fund": 0.6, "bond": 0.4},
            "asset_returns": {"fund": FUND, "bond": BOND},
            "factor_returns": {"mkt": MARKET},
        },
    )
    by_reference = _call(
        store,
        "analyze_risk",
        {
            "portfolio_id": "book",
            "weights": {"fund": 0.6, "bond": 0.4},
            "asset_returns": {"dataset": "returns"},
            "factor_returns": {"dataset": "factors", "columns": ["mkt"]},
        },
    )

    assert by_reference.status == "ok", by_reference.error
    assert {**by_reference.output, "data_versions": {}} == by_value.output
    assert set(by_reference.data_versions) == {"dataset:returns", "dataset:factors"}


def test_a_missing_column_or_misaligned_datasets_are_reported(
    store: DirectoryDatasetStore, tmp_path: Path
) -> None:
    missing = _call(
        store, "analyze_performance", {"returns": {"dataset": "returns", "column": "cash"}}
    )
    store.put("later", _csv(tmp_path / "l.csv", {"mkt": MARKET}, start=2))  # one month later
    misaligned = _call(
        store,
        "analyze_performance",
        {
            "returns": {"dataset": "returns", "column": "fund"},
            "benchmark": {"dataset": "later", "column": "mkt"},
        },
    )

    assert missing.status == "error" and "its columns are fund, market, bond" in missing.error
    assert misaligned.status == "error" and "cover different rows" in misaligned.error


def test_the_referenced_tools_honour_the_contract_and_read_local_data(
    store: DirectoryDatasetStore,
) -> None:
    for spec in analytics_tools(store).specs():
        assert spec.permissions == frozenset({Permission.READ_LOCAL_DATA})
        assert "list_datasets" in spec.description

    record = check_tool(
        analytics_tools(store).get("analyze_performance"),
        {"returns": {"dataset": "returns", "column": "fund"}, "periods_per_year": 12},
    )
    assert record.output is not None and record.output["series_id"] == "returns.fund"
    assert check_tool(analytics_tools(store).get("analyze_performance"), {"returns": FUND}).output
