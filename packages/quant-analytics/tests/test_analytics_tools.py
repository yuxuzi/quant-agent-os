"""The analytics tools honour the platform contract and state their conventions."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from quant_agent_os.testing import check_tool
from quant_agent_os.tools import READ_ONLY
from quant_analytics.computed import PerformanceInput, performance
from quant_analytics.tools import analytics_tools

FUND = [0.012, -0.004, 0.021, -0.015, 0.008, 0.017, -0.006, 0.011, 0.002, -0.009, 0.014, 0.005]
MARKET = [0.010, -0.002, 0.015, -0.012, 0.006, 0.012, -0.004, 0.009, 0.001, -0.007, 0.010, 0.004]


def test_analyze_performance_honours_the_contract_and_states_conventions() -> None:
    record = check_tool(
        analytics_tools().get("analyze_performance"),
        {"series_id": "fund", "returns": FUND, "benchmark": MARKET, "periods_per_year": 12},
    )

    assert record.output is not None
    output: dict[str, Any] = record.output
    assert output["periods"] == 12 and output["warnings"] == []
    assert {"sharpe_ratio", "sortino_ratio", "beta", "information_ratio"} <= set(output["metrics"])
    assert output["conventions"]["source"].startswith("empyrical-reloaded")
    assert set(output["conventions"]) >= set(output["metrics"])
    statements = [item["statement"] for item in output["evidence"]]
    assert all(item["kind"] == "computed" for item in output["evidence"])
    assert any(statement.startswith("Sharpe ratio of fund is") for statement in statements)


def test_without_a_benchmark_there_are_no_benchmark_metrics() -> None:
    output = performance(PerformanceInput(series_id="fund", returns=FUND, periods_per_year=12))

    assert "beta" not in output.metrics and "alpha" not in output.metrics


def test_short_and_flat_series_are_flagged() -> None:
    output = performance(
        PerformanceInput(series_id="cash", returns=[0.001] * 4, periods_per_year=12)
    )

    assert output.metrics["sharpe_ratio"] is None
    assert any("unreliable" in warning for warning in output.warnings)
    assert any("does not vary" in warning for warning in output.warnings)


@pytest.mark.parametrize(
    "fields",
    [
        {"returns": [0.01, float("nan")]},
        {"returns": [0.01, -1.5]},
        {"returns": FUND, "benchmark": MARKET[:5]},
    ],
)
def test_bad_inputs_are_refused(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PerformanceInput(series_id="x", periods_per_year=12, **fields)


def test_without_a_store_the_tools_need_no_permissions_and_refuse_references() -> None:
    registry = analytics_tools()

    assert all(spec.permissions <= READ_ONLY and not spec.permissions for spec in registry.specs())
    assert {spec.name for spec in registry.specs()} == {"analyze_performance", "analyze_risk"}
    record = check_tool(
        registry.get("analyze_performance"),
        {"returns": {"dataset": "returns", "column": "fund"}},
        expect="error",
    )
    assert "no datasets" in (record.error or "")
