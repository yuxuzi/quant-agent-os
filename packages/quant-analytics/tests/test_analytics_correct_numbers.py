"""Known answers for what used to change a number silently: frequency, alignment, and gaps.

Before these were decided by the tools, daily data was annualised as monthly (a Sharpe of 0.20
for a true 0.90), and a benchmark one row off gave beta -0.05 for a true 1.0, both with status
``ok`` and no warning.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.runtime import PlanStep, ToolCallRecord, ToolExecutor
from quant_agent_os.tools import PermissionPolicy
from quant_analytics import metrics
from quant_analytics.tools import analytics_tools

generator = random.Random(7)
DAILY = [generator.gauss(0.0005, 0.01) for _ in range(300)]
OTHER = [generator.gauss(0.0003, 0.008) for _ in range(300)]


def _business_days(count: int) -> list[date]:
    days = (date(2020, 1, 1) + timedelta(days=step) for step in range(count * 2))
    return [day for day in days if day.weekday() < 5][:count]


DAYS = _business_days(301)


def _write(
    store: DirectoryDatasetStore,
    tmp_path: Path,
    name: str,
    header: str,
    rows: Iterable[tuple[Any, ...]],
) -> None:
    source = tmp_path / f"{name}.csv"
    lines = [header, *(",".join(str(cell) for cell in row) for row in rows)]
    source.write_text("\n".join(lines) + "\n")
    store.put(name, source)


def _iso(day: date) -> str:
    return day.isoformat()


def _compact(day: date) -> str:
    return day.strftime("%Y%m%d")


@pytest.fixture
def store(tmp_path: Path) -> DirectoryDatasetStore:
    return DirectoryDatasetStore(tmp_path / "datasets")


def _call(store: DirectoryDatasetStore, tool: str, **arguments: Any) -> ToolCallRecord:
    executor = ToolExecutor(analytics_tools(store), PermissionPolicy())
    return executor.execute(PlanStep(tool=tool, arguments=arguments))


def _ok(record: ToolCallRecord) -> dict[str, Any]:
    assert record.status == "ok", record.error
    assert record.output is not None
    return record.output


def test_daily_dates_annualise_as_daily_without_being_told(
    store: DirectoryDatasetStore, tmp_path: Path
) -> None:
    _write(store, tmp_path, "daily", "date,fund", zip(map(_iso, DAYS), DAILY, strict=False))

    output = _ok(
        _call(store, "analyze_performance", returns={"dataset": "daily", "column": "fund"})
    )

    mean = sum(DAILY) / len(DAILY)
    deviation = math.sqrt(sum((value - mean) ** 2 for value in DAILY) / (len(DAILY) - 1))
    assert output["periods_per_year"] == 252
    assert output["metrics"]["sharpe_ratio"] == pytest.approx(mean / deviation * math.sqrt(252))
    assert output["conventions"]["periods_per_year"] == "252, inferred from the dates"


def test_a_given_frequency_that_disagrees_with_the_dates_is_used_with_a_warning(
    store: DirectoryDatasetStore, tmp_path: Path
) -> None:
    _write(store, tmp_path, "daily", "date,fund", zip(map(_iso, DAYS), DAILY, strict=False))

    output = _ok(
        _call(
            store,
            "analyze_performance",
            returns={"dataset": "daily", "column": "fund"},
            periods_per_year=12,
        )
    )

    assert output["periods_per_year"] == 12
    assert output["metrics"]["sharpe_ratio"] == pytest.approx(metrics.sharpe_ratio(DAILY, 12))
    assert any("look like 252" in warning for warning in output["warnings"])


def test_no_dates_and_no_frequency_is_refused_not_guessed(
    store: DirectoryDatasetStore, tmp_path: Path
) -> None:
    _write(store, tmp_path, "plain", "fund", ((value,) for value in DAILY))

    by_reference = _call(
        store, "analyze_performance", returns={"dataset": "plain", "column": "fund"}
    )
    by_value = _call(store, "analyze_performance", returns=DAILY)
    risk = _call(
        store, "analyze_risk", portfolio_id="p", weights={"a": 1.0}, asset_returns={"a": DAILY}
    )

    assert by_reference.status == "error" and "pass periods_per_year" in (by_reference.error or "")
    assert by_value.status == "invalid_input" and "periods_per_year" in (by_value.error or "")
    assert risk.status == "invalid_input" and "periods_per_year" in (risk.error or "")


def test_a_benchmark_one_row_off_is_joined_on_its_dates(
    store: DirectoryDatasetStore, tmp_path: Path
) -> None:
    """The same values on the same dates, but the second file starts a day later: beta is 1."""
    fund = zip(map(_compact, DAYS[:-1]), DAILY, strict=True)
    bench = zip(map(_compact, DAYS[1:]), [*DAILY[1:], 0.001], strict=True)
    _write(store, tmp_path, "fund", "date,fund", fund)
    _write(store, tmp_path, "bench", "date,bench", bench)

    output = _ok(
        _call(
            store,
            "analyze_performance",
            returns={"dataset": "fund", "column": "fund"},
            benchmark={"dataset": "bench", "column": "bench"},
        )
    )

    assert output["periods"] == 299
    assert output["metrics"]["beta"] == pytest.approx(1.0)
    assert output["periods_per_year"] == 252
    assert sum("left out 1 rows" in warning for warning in output["warnings"]) == 2


def test_dates_in_different_forms_still_join(store: DirectoryDatasetStore, tmp_path: Path) -> None:
    _write(store, tmp_path, "iso", "date,a", zip(map(_iso, DAYS), DAILY, strict=False))
    _write(store, tmp_path, "compact", "date,b", zip(map(_compact, DAYS), DAILY, strict=False))

    output = _ok(
        _call(
            store,
            "analyze_performance",
            returns={"dataset": "iso", "column": "a"},
            benchmark={"dataset": "compact", "column": "b"},
        )
    )

    assert output["metrics"]["beta"] == pytest.approx(1.0)
    assert not any("left out" in warning for warning in output["warnings"])


def test_datasets_that_cannot_be_joined_are_refused(
    store: DirectoryDatasetStore, tmp_path: Path
) -> None:
    _write(store, tmp_path, "dated", "date,a", zip(map(_iso, DAYS), DAILY, strict=False))
    _write(store, tmp_path, "plain", "b", ((value,) for value in DAILY))
    later = (date(2030, 1, 1) + timedelta(days=step) for step in range(300))
    _write(store, tmp_path, "later", "date,c", zip(map(_iso, later), DAILY, strict=True))

    undated = _call(
        store,
        "analyze_performance",
        returns={"dataset": "dated", "column": "a"},
        benchmark={"dataset": "plain", "column": "b"},
    )
    disjoint = _call(
        store,
        "analyze_performance",
        returns={"dataset": "dated", "column": "a"},
        benchmark={"dataset": "later", "column": "c"},
    )

    assert undated.status == "error" and "'plain' has no dates" in (undated.error or "")
    assert disjoint.status == "error" and "share no dates" in (disjoint.error or "")


def test_missing_cells_are_left_out_and_counted_never_filled(
    store: DirectoryDatasetStore, tmp_path: Path
) -> None:
    values: list[Any] = list(DAILY)
    values[10], values[20] = "", "NaN"
    _write(store, tmp_path, "gaps", "date,fund", zip(map(_iso, DAYS), values, strict=False))

    output = _ok(_call(store, "analyze_performance", returns={"dataset": "gaps", "column": "fund"}))

    kept = [value for at, value in enumerate(DAILY) if at not in (10, 20)]
    assert output["periods"] == 298
    assert output["metrics"]["sharpe_ratio"] == pytest.approx(metrics.sharpe_ratio(kept, 252))
    assert any("Left out 2 rows where gaps.fund" in warning for warning in output["warnings"])


def test_a_gap_in_either_series_removes_the_row_from_both(
    store: DirectoryDatasetStore, tmp_path: Path
) -> None:
    fund: list[Any] = list(DAILY)
    bench: list[Any] = list(OTHER)
    fund[5], bench[6] = "", ""
    rows = zip(map(_iso, DAYS), fund, bench, strict=False)
    _write(store, tmp_path, "both", "date,fund,bench", rows)

    output = _ok(
        _call(
            store,
            "analyze_performance",
            returns={"dataset": "both", "column": "fund"},
            benchmark={"dataset": "both", "column": "bench"},
        )
    )

    kept = [at for at in range(300) if at not in (5, 6)]
    expected = metrics.benchmark_metrics(
        [DAILY[at] for at in kept], [OTHER[at] for at in kept], 252
    )
    assert output["periods"] == 298
    assert output["metrics"]["beta"] == pytest.approx(expected["beta"])


def test_risk_uses_the_rows_every_series_has(store: DirectoryDatasetStore, tmp_path: Path) -> None:
    first: list[Any] = list(DAILY)
    second: list[Any] = list(OTHER)
    first[0], second[1] = "", "NA"
    rows = zip(map(_iso, DAYS), first, second, strict=False)
    _write(store, tmp_path, "book", "date,a,b", rows)

    output = _ok(
        _call(
            store,
            "analyze_risk",
            portfolio_id="p",
            weights={"a": 0.5, "b": 0.5},
            asset_returns={"dataset": "book"},
        )
    )

    assert output["periods"] == 298 and output["periods_per_year"] == 252
    assert sum("Left out 1 rows" in warning for warning in output["warnings"]) == 2


def test_too_few_complete_rows_is_refused(store: DirectoryDatasetStore, tmp_path: Path) -> None:
    rows = zip(map(_iso, DAYS), [0.01, "", ""], strict=False)
    _write(store, tmp_path, "thin", "date,a", rows)

    record = _call(
        store,
        "analyze_performance",
        returns={"dataset": "thin", "column": "a"},
        periods_per_year=252,
    )

    assert record.status == "error" and "Fewer than 2 rows" in (record.error or "")


def test_series_must_be_passed_the_same_way(store: DirectoryDatasetStore, tmp_path: Path) -> None:
    _write(store, tmp_path, "daily", "date,fund", zip(map(_iso, DAYS), DAILY, strict=False))

    record = _call(
        store,
        "analyze_performance",
        returns={"dataset": "daily", "column": "fund"},
        benchmark=DAILY,
    )

    assert record.status == "error" and "the same way" in (record.error or "")


def test_returns_that_look_like_percentages_are_flagged_or_refused(
    store: DirectoryDatasetStore, tmp_path: Path
) -> None:
    """Percent data with a loss past -1 cannot be a fraction; milder data can only be flagged."""
    months = [f"{2020 + at // 12}-{at % 12 + 1:02d}" for at in range(60)]
    mild = [abs(value) * 100 for value in DAILY[:60]]
    harsh = [value * 100 for value in DAILY[:60]]
    _write(store, tmp_path, "mild", "month,fund", zip(months, mild, strict=True))
    _write(store, tmp_path, "harsh", "month,fund", zip(months, harsh, strict=True))

    flagged = _ok(
        _call(store, "analyze_performance", returns={"dataset": "mild", "column": "fund"})
    )
    refused = _call(store, "analyze_performance", returns={"dataset": "harsh", "column": "fund"})
    fractions = _ok(_call(store, "analyze_performance", returns=DAILY[:60], periods_per_year=12))

    assert any("look like percentages" in warning for warning in flagged["warnings"])
    assert refused.status == "error" and "0.01 for 1%" in (refused.error or "")
    assert not any("percentages" in warning for warning in fractions["warnings"])
