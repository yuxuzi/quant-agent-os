"""Known answers for historical stress and a vendor factor model in `analyze_risk`."""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.runtime import ToolCall, ToolExecutor
from quant_agent_os.testing import check_tool
from quant_agent_os.tools import Permission, PermissionPolicy
from quant_analytics.tools import analytics_tools

COVID = (date(2020, 2, 19), date(2020, 3, 23))
POLICY = PermissionPolicy(granted=PermissionPolicy().granted | {Permission.READ_LOCAL_DATA})


def _store(tmp_path: Path, files: dict[str, str]) -> DirectoryDatasetStore:
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = DirectoryDatasetStore(tmp_path / "datasets")
    for name, text in files.items():
        path = tmp_path / f"{name}.csv"
        path.write_text(text, "utf-8")
        store.put(name, path)
    return store


def _run(store: DirectoryDatasetStore | None, **arguments: Any) -> Any:
    return ToolExecutor(analytics_tools(store), POLICY).execute(
        ToolCall(tool="analyze_risk", arguments=arguments)
    )


def _daily_csv() -> tuple[str, int]:
    """Business days of early 2020: A falls 1% a day in the COVID window, B rises 0.5%."""
    days = [
        day
        for day in (date(2020, 1, 2) + timedelta(days=n) for n in range(120))
        if day.weekday() < 5
    ]
    lines = ["date,A,B"]
    inside = 0
    for at, day in enumerate(days):
        if COVID[0] < day <= COVID[1]:
            inside += 1
            lines.append(f"{day},-0.01,0.005")
        else:
            lines.append(f"{day},{0.001 * (at % 3)},{-0.001 * (at % 2)}")
    return "\n".join(lines) + "\n", inside


def test_historical_stress_applies_todays_weights_to_a_covered_window(tmp_path: Path) -> None:
    text, inside = _daily_csv()
    store = _store(tmp_path, {"daily": text})

    record = _run(
        store,
        portfolio_id="book",
        weights={"A": 0.6, "B": -0.2},
        asset_returns={"dataset": "daily"},
    )

    assert record.status == "ok", record.error
    expected = 0.6 * (0.99**inside - 1) - 0.2 * (1.005**inside - 1)
    stress = record.output["historical_stress"]
    assert stress == {"COVID crash (2020)": pytest.approx(expected)}
    assert record.output["historical_stress_note"] == (
        "11 of 12 scenario windows are not covered by the dates."
    )
    assert "historical_stress" in record.output["conventions"]
    assert any("COVID crash (2020)" in item["statement"] for item in record.output["evidence"])


def test_historical_stress_is_none_with_a_reason_otherwise(tmp_path: Path) -> None:
    monthly = "month,A\n" + "\n".join(f"2020-{m:02d},{0.01 * (m % 3)}" for m in range(1, 13))
    store = _store(tmp_path, {"monthly": monthly + "\n"})

    by_month = _run(
        store, portfolio_id="b", weights={"A": 1.0}, asset_returns={"dataset": "monthly"}
    )
    undated = _run(
        None,
        portfolio_id="b",
        weights={"A": 1.0},
        asset_returns={"A": [0.01, -0.02, 0.005]},
        periods_per_year=252,
    )
    uncovered = _run(
        _store(tmp_path / "late", {"late": _daily_csv()[0].replace("2020-", "2021-")}),
        portfolio_id="b",
        weights={"A": 1.0},
        asset_returns={"dataset": "late"},
    )

    assert by_month.output["historical_stress"] is None
    assert "needs daily returns" in by_month.output["historical_stress_note"]
    assert undated.output["historical_stress"] is None
    assert "no dates" in undated.output["historical_stress_note"]
    assert uncovered.output["historical_stress"] is None
    assert "cover none of the 12" in uncovered.output["historical_stress_note"]


MODEL = {
    "loadings": "asset,market,value\nA,1.0,0.2\nB,0.5,\nC,1.5,-0.3\n",
    "covariance": "factor,market,value\nmarket,0.04,0.002\nvalue,0.002,0.01\n",
    "specific": "asset,variance\nA,0.01\nB,0.02\nC,0.03\n",
}
FACTOR_MODEL = {
    "loadings": "loadings",
    "factor_covariance": "covariance",
    "specific_variance": {"dataset": "specific", "column": "variance"},
}


def _model_store(tmp_path: Path, **changes: str) -> DirectoryDatasetStore:
    text, _ = _daily_csv()
    return _store(tmp_path, {"daily": text, **MODEL, **changes})


def test_a_vendor_model_gives_volatility_from_b_f_b_plus_d(tmp_path: Path) -> None:
    store = _model_store(tmp_path)

    record = _run(
        store,
        portfolio_id="book",
        weights={"A": 0.5, "B": 0.5},
        asset_returns={"dataset": "daily"},
        factor_model=FACTOR_MODEL,
    )

    assert record.status == "ok", record.error
    model = record.output["factor_model"]
    # x = B'w: market 0.75, value 0.1 (B's missing value loading counts as 0).
    market, value = 0.75, 0.1
    factor_variance = market**2 * 0.04 + 2 * market * value * 0.002 + value**2 * 0.01
    specific = 0.25 * 0.01 + 0.25 * 0.02
    total = factor_variance + specific
    assert model["exposures"] == pytest.approx({"market": market, "value": value})
    assert model["volatility"] == pytest.approx(math.sqrt(total))
    assert model["specific_share"] == pytest.approx(specific / total)
    assert model["factor_shares"]["market"] == pytest.approx(
        market * (0.04 * market + 0.002 * value) / total
    )
    assert sum(model["contributions"].values()) == pytest.approx(math.sqrt(total))
    assert any("count as 0" in warning for warning in record.output["warnings"])
    assert {"dataset:loadings", "dataset:covariance", "dataset:specific"} <= set(
        record.output["data_versions"]
    )


def test_a_model_given_per_day_is_annualised(tmp_path: Path) -> None:
    daily = {
        "covariance": "factor,market,value\nmarket,0.0002,0.00001\nvalue,0.00001,0.00005\n",
        "specific": "asset,variance\nA,0.00005\nB,0.0001\nC,0.00015\n",
    }
    annual, per_day = _model_store(tmp_path / "a"), _model_store(tmp_path / "d", **daily)
    weights = {"A": 0.5, "B": 0.5}

    given = _run(
        annual,
        portfolio_id="b",
        weights=weights,
        asset_returns={"dataset": "daily"},
        factor_model=FACTOR_MODEL,
    )
    scaled = _run(
        per_day,
        portfolio_id="b",
        weights=weights,
        asset_returns={"dataset": "daily"},
        factor_model={**FACTOR_MODEL, "periods_per_year": 200},
    )

    assert scaled.output["factor_model"]["volatility"] == pytest.approx(
        given.output["factor_model"]["volatility"]
    )


@pytest.mark.parametrize(
    ("changes", "weights", "message"),
    [
        ({}, {"A": 0.5, "D": 0.5}, "specific variance 'specific' has no row for D"),
        (
            {"covariance": "factor,market,size\nmarket,0.04,0\nsize,0,0.01\n"},
            {"A": 1.0},
            "name factors the covariance 'covariance' has not: value",
        ),
        (
            {"covariance": "factor,market,value\nmarket,0.04,0.002\nother,0.002,0.01\n"},
            {"A": 1.0},
            "must have one row per column",
        ),
        (
            {"specific": "asset,variance\nA,0.01\nC,0.03\n"},
            {"A": 0.5, "B": 0.5},
            "has no row for B",
        ),
        ({"specific": "asset,variance\nA,-0.01\nB,0.02\n"}, {"A": 1.0}, "zero or more"),
        (
            {"covariance": "factor,market,value\nmarket,0.04,0.002\nvalue,0.003,0.01\n"},
            {"A": 1.0},
            "not symmetric",
        ),
    ],
)
def test_a_model_that_does_not_line_up_is_refused(
    tmp_path: Path, changes: dict[str, str], weights: dict[str, float], message: str
) -> None:
    store = _model_store(tmp_path, **changes)
    daily = _daily_csv()[0].replace("date,A,B", "date,A,D") if "D" in weights else None
    if daily:
        store = _store(tmp_path / "d", {"daily": daily, **MODEL})

    record = _run(
        store,
        portfolio_id="b",
        weights=weights,
        asset_returns={"dataset": "daily"},
        factor_model=FACTOR_MODEL,
    )

    assert record.status == "error"
    assert message in (record.error or "")


def test_risk_with_stress_and_a_model_honours_the_contract(tmp_path: Path) -> None:
    store = _model_store(tmp_path)

    check_tool(
        analytics_tools(store).get("analyze_risk"),
        {
            "portfolio_id": "book",
            "weights": {"A": 0.5, "B": 0.5},
            "asset_returns": {"dataset": "daily"},
            "factor_model": FACTOR_MODEL,
        },
    )


def test_an_asset_with_no_loading_has_specific_risk_only(tmp_path: Path) -> None:
    text, _ = _daily_csv()
    store = _store(
        tmp_path,
        {
            "daily": text.replace("date,A,B", "date,A,C"),
            **MODEL,
            "loadings": "asset,market,value\nA,1.0,0.2\nB,0.5,0\n",
        },
    )

    record = _run(
        store,
        portfolio_id="book",
        weights={"A": 0.5, "C": 0.5},
        asset_returns={"dataset": "daily"},
        factor_model=FACTOR_MODEL,
    )

    assert record.status == "ok", record.error
    assert "have no row for C; their risk is specific only." in " ".join(record.output["warnings"])
    model = record.output["factor_model"]
    factor_variance = 0.25 * (0.04 + 2 * 0.2 * 0.002 + 0.04 * 0.01)
    specific = 0.25 * 0.01 + 0.25 * 0.03
    assert model["volatility"] == pytest.approx((factor_variance + specific) ** 0.5)


def test_an_asset_with_short_history_is_left_out_not_every_date(tmp_path: Path) -> None:
    text, _ = _daily_csv()
    lines = text.splitlines()
    rows = len(lines) - 1
    # C lists halfway through: before, its cells are empty.
    widened = [lines[0] + ",C"] + [
        f"{line},{'' if at < rows // 2 else 0.002}" for at, line in enumerate(lines[1:])
    ]
    store = _store(tmp_path, {"daily": "\n".join(widened) + "\n"})

    record = _run(
        store,
        portfolio_id="book",
        weights={"A": 0.4, "B": 0.4, "C": 0.2},
        asset_returns={"dataset": "daily"},
    )

    assert record.status == "ok", record.error
    assert record.output["periods"] == rows
    assert set(record.output["contributions"]) == {"A", "B"}
    left = next(w for w in record.output["warnings"] if w.startswith("Left out 1 assets"))
    assert ": C." in left and "20.00%" in left


def test_a_weight_with_no_returns_column_is_refused_naming_it(tmp_path: Path) -> None:
    store = _model_store(tmp_path)

    record = _run(
        store, portfolio_id="b", weights={"A": 0.5, "Q": 0.5}, asset_returns={"dataset": "daily"}
    )

    assert record.status == "error"
    assert "have no column for: Q." in (record.error or "")
