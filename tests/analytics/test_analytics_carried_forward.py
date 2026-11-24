"""Known answers for the carried-forward price warnings in `analyze_risk` (warnings only)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.runtime import ToolCall, ToolExecutor
from quant_agent_os.tools import Permission, PermissionPolicy
from quant_analytics.computed import RiskInput, carried_forward_warnings, portfolio_risk
from quant_analytics.tools import analytics_tools

# Eight business days, with Thursday 2024-03-07 a closure: every asset is exactly zero that day.
DAYS = [
    date(2024, 3, 4),  # Mon
    date(2024, 3, 5),  # Tue
    date(2024, 3, 6),  # Wed
    date(2024, 3, 7),  # Thu, the closure
    date(2024, 3, 8),  # Fri
    date(2024, 3, 11),  # Mon
    date(2024, 3, 12),  # Tue
    date(2024, 3, 13),  # Wed
]
# A carries three exact zeros in a row, plus one more; B has two exact zeros (a share above 5%);
# C has one exact zero, which is a lone zero and flags nothing.
ASSETS = {
    "A": [0.01, 0.0, 0.0, 0.0, 0.02, -0.01, 0.0, 0.005],
    "B": [0.02, 0.01, -0.01, 0.0, 0.02, 0.01, 0.0, -0.02],
    "C": [0.01, -0.02, 0.005, 0.0, -0.01, 0.02, 0.003, -0.004],
}
WEIGHTS = {"A": 0.5, "B": 0.3, "C": 0.2}
POLICY = PermissionPolicy(granted=PermissionPolicy().granted | {Permission.READ_LOCAL_DATA})
EXPECTED = [
    "Exact zero returns suggest carried-forward prices in 2 assets: A (4 of 8 exact zero, longest "
    "run 3); B (2 of 8 exact zero, longest run 1). The figures use the returns as given.",
    "1 weekday(s) have an exact zero return for every asset (2024-03-07): a market closure carried "
    "forward, or no new prices. The figures count them as trading days.",
]


def test_runs_shares_and_a_closure_are_named_with_their_counts() -> None:
    assert carried_forward_warnings(ASSETS, DAYS) == EXPECTED


def test_without_dates_the_closure_is_not_checked_but_the_runs_are() -> None:
    assert carried_forward_warnings(ASSETS, None) == [EXPECTED[0]]


def test_a_lone_zero_and_weekend_zeros_are_not_warned_about() -> None:
    weekend = [date(2024, 3, 9), date(2024, 3, 10)]  # Sat, Sun
    quiet = {"C": [0.01, 0.0], "D": [-0.02, 0.0]}

    assert carried_forward_warnings(quiet, weekend) == []
    assert carried_forward_warnings({"A": [0.01, 0.0, 0.02]}, None) == []


def test_the_figures_are_the_same_with_the_warnings_as_without_them() -> None:
    expected = portfolio_risk(
        RiskInput(
            portfolio_id="book",
            weights=WEIGHTS,
            asset_returns=ASSETS,
            periods_per_year=252,
        )
    )

    record = ToolExecutor(analytics_tools(None), POLICY).execute(
        ToolCall(
            tool="analyze_risk",
            arguments={
                "portfolio_id": "book",
                "weights": WEIGHTS,
                "asset_returns": ASSETS,
                "periods_per_year": 252,
            },
        )
    )

    assert record.status == "ok", record.error
    assert record.output is not None
    assert record.output["volatility"] == pytest.approx(expected.volatility)
    warnings = record.output["warnings"]
    assert EXPECTED[0] in warnings and EXPECTED[1] not in warnings  # no dates: no closure note


def _store(tmp_path: Path, text: str) -> DirectoryDatasetStore:
    store = DirectoryDatasetStore(tmp_path / "datasets")
    path = tmp_path / "daily.csv"
    path.write_text(text, "utf-8")
    store.put("daily", path)
    return store


def test_a_dataset_gets_both_warnings_with_its_dates(tmp_path: Path) -> None:
    lines = ["date,A,B,C"]
    lines += [
        f"{day.isoformat()},{ASSETS['A'][i]},{ASSETS['B'][i]},{ASSETS['C'][i]}"
        for i, day in enumerate(DAYS)
    ]
    store = _store(tmp_path, "\n".join(lines) + "\n")

    record = ToolExecutor(analytics_tools(store), POLICY).execute(
        ToolCall(
            tool="analyze_risk",
            arguments={
                "portfolio_id": "book",
                "weights": WEIGHTS,
                "asset_returns": {"dataset": "daily"},
            },
        )
    )

    assert record.status == "ok", record.error
    assert record.output is not None
    warnings: list[Any] = record.output["warnings"]
    assert [item for item in warnings if item in EXPECTED] == EXPECTED
