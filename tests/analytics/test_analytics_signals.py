"""Known answers for `analyze_signal` and `run_backtest`, on frames read by reference.

Expected values are written out here from the definitions (Spearman per date, bucket means, a
book's return and turnover), independently of Quant Toolbox.
"""

from __future__ import annotations

import math
import random
import statistics
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.runtime import ToolCall, ToolExecutor
from quant_agent_os.testing import check_tool
from quant_agent_os.tools import Permission, PermissionPolicy
from quant_analytics.tools import analytics_tools

DAYS = [d for d in (date(2024, 1, 1) + timedelta(days=n) for n in range(200)) if d.weekday() < 5]
NAMES = [f"S{i}" for i in range(10)]
Rows = list[list[float]]


def _write(directory: Path, name: str, rows: Rows, names: list[str], days: list[date]) -> Path:
    lines = ["date," + ",".join(names)]
    lines += [
        f"{day}," + ",".join("" if math.isnan(v) else repr(v) for v in row)
        for day, row in zip(days, rows, strict=True)
    ]
    path = directory / f"{name}.csv"
    path.write_text("\n".join(lines) + "\n", "utf-8")
    return path


def _store(tmp_path: Path, frames: dict[str, tuple[Rows, list[str], list[date]]]) -> Any:
    store = DirectoryDatasetStore(tmp_path / "datasets")
    for name, (rows, names, days) in frames.items():
        store.put(name, _write(tmp_path, name, rows, names, days))
    return store


def _call(store: DirectoryDatasetStore, tool: str, **arguments: Any) -> Any:
    policy = PermissionPolicy(granted=PermissionPolicy().granted | {Permission.READ_LOCAL_DATA})
    record = ToolExecutor(analytics_tools(store), policy).execute(
        ToolCall(tool=tool, arguments=arguments)
    )
    assert record.status == "ok", record.error
    return record.output


def _random_returns(seed: int, days: int = 120, names: int = 10) -> Rows:
    rng = random.Random(seed)
    return [[round(rng.gauss(0.0005, 0.01), 6) for _ in range(names)] for _ in range(days)]


def _spearman(x: list[float], y: list[float]) -> float:
    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        result = [0.0] * len(values)
        for rank, at in enumerate(order, start=1):
            result[at] = float(rank)
        return result

    return statistics.correlation(ranks(x), ranks(y))


FRAMES = {"signal": {"dataset": "signal"}, "returns": {"dataset": "returns"}}


def test_a_signal_that_knows_the_traded_return_has_rank_ic_one(tmp_path: Path) -> None:
    returns = _random_returns(1)
    days = DAYS[: len(returns)]
    # With the default lag of one bar, the signal on date t earns the return of row t + 2.
    signal = [returns[t + 2] if t + 2 < len(returns) else [math.nan] * 10 for t in range(120)]
    store = _store(tmp_path, {"signal": (signal, NAMES, days), "returns": (returns, NAMES, days)})

    output = _call(store, "analyze_signal", **FRAMES, horizons=[1, 5])

    summary = output["summary"]
    assert summary["mean_rank_ic"] == pytest.approx(1.0)
    assert summary["mean_ic"] == pytest.approx(1.0)
    assert summary["dates"] == 118
    assert [item["horizon"] for item in output["horizons"]] == [1, 5]
    assert output["horizons"][1]["mean_rank_ic"] < 1.0
    # Top fifth minus bottom fifth: the two largest and two smallest returns of each traded row.
    spreads = [
        statistics.mean(sorted(row)[-2:]) - statistics.mean(sorted(row)[:2]) for row in returns[2:]
    ]
    assert summary["top_minus_bottom"] == pytest.approx(statistics.mean(spreads))
    assert any("unusually high" in warning for warning in output["warnings"])
    assert output["data_versions"] == {
        "dataset:signal": store.get("signal").fingerprint,
        "dataset:returns": store.get("returns").fingerprint,
    }
    assert output["evidence"][0]["kind"] == "computed"


def test_mean_rank_ic_is_the_mean_of_per_date_spearman(tmp_path: Path) -> None:
    returns = _random_returns(2)
    days = DAYS[: len(returns)]
    rng = random.Random(3)
    signal = [
        [
            (returns[t + 2][j] if t + 2 < len(returns) else 0.0) + rng.gauss(0, 0.02)
            for j in range(10)
        ]
        for t in range(120)
    ]
    store = _store(tmp_path, {"signal": (signal, NAMES, days), "returns": (returns, NAMES, days)})

    output = _call(store, "analyze_signal", **FRAMES, horizons=[1])

    expected = [_spearman(signal[t], returns[t + 2]) for t in range(118)]
    head = output["horizons"][0]
    assert head["mean_rank_ic"] == pytest.approx(statistics.mean(expected))
    assert head["rank_ic_std"] == pytest.approx(statistics.stdev(expected))
    assert head["rank_ic_ir"] == pytest.approx(
        statistics.mean(expected) / statistics.stdev(expected)
    )
    assert head["positive_rank_ic_share"] == pytest.approx(
        sum(value > 0 for value in expected) / len(expected)
    )


def test_quantile_returns_follow_the_buckets(tmp_path: Path) -> None:
    # Instrument j returns 0.1% * j every day and the signal ranks them the same way.
    days = DAYS[:40]
    returns = [[0.001 * j for j in range(10)] for _ in days]
    signal = [[float(j) for j in range(10)] for _ in days]
    store = _store(tmp_path, {"signal": (signal, NAMES, days), "returns": (returns, NAMES, days)})

    output = _call(store, "analyze_signal", **FRAMES, quantiles=5)

    buckets = output["quantile_returns"]
    assert buckets["mean_returns"] == pytest.approx([0.0005, 0.0025, 0.0045, 0.0065, 0.0085])
    assert buckets["spread"] == pytest.approx(0.008)
    assert output["summary"]["mean_rank_ic"] == pytest.approx(1.0)
    assert output["signal_autocorrelation"] == pytest.approx(1.0)


def test_lag_zero_scores_the_next_return_and_is_warned_about(tmp_path: Path) -> None:
    returns = _random_returns(4)
    days = DAYS[: len(returns)]
    signal = [returns[t + 1] if t + 1 < len(returns) else [math.nan] * 10 for t in range(120)]
    store = _store(tmp_path, {"signal": (signal, NAMES, days), "returns": (returns, NAMES, days)})

    lagged = _call(store, "analyze_signal", **FRAMES)
    same_close = _call(store, "analyze_signal", **FRAMES, lag=0)

    assert same_close["summary"]["mean_rank_ic"] == pytest.approx(1.0)
    assert abs(lagged["summary"]["mean_rank_ic"]) < 0.3
    assert any("lag is 0" in warning for warning in same_close["warnings"])
    assert not any("lag is 0" in warning for warning in lagged["warnings"])


def test_frames_align_on_shared_dates_and_instruments(tmp_path: Path) -> None:
    returns = _random_returns(5, names=11)
    signal = _random_returns(6, names=11)
    days = DAYS[:120]
    store = _store(
        tmp_path,
        {
            "signal": (signal[10:], [*NAMES, "ZZZ"], days[10:]),
            "returns": (returns, [*NAMES, "YYY"], days),
        },
    )

    output = _call(store, "analyze_signal", **FRAMES)

    assert output["observations"]["instruments"] == 10
    assert output["observations"]["dates"] == 110
    assert output["observations"]["first_date"] == days[10].isoformat()
    warnings = " ".join(output["warnings"])
    assert "left out 10 rows of 'returns'" in warnings
    assert "columns of 'signal' with no match in the other frame: ZZZ" in warnings
    assert "columns of 'returns' with no match in the other frame: YYY" in warnings


def test_thin_dates_and_stopped_returns_are_warned_about(tmp_path: Path) -> None:
    returns = _random_returns(7)
    for row in returns[100:]:
        row[9] = math.nan  # S9 stops: a delisting
    signal = _random_returns(8)
    for row in signal[:5]:
        row[2:] = [math.nan] * 8  # two names on the first dates
    days = DAYS[:120]
    store = _store(tmp_path, {"signal": (signal, NAMES, days), "returns": (returns, NAMES, days)})

    output = _call(store, "analyze_signal", **FRAMES)

    warnings = " ".join(output["warnings"])
    assert "5 dates have fewer than 3 instruments" in warnings
    assert "1 instruments (S9)" in warnings


@pytest.mark.parametrize(
    ("signal", "returns", "message"),
    [
        ({"dataset": "signal", "columns": ["nope"]}, {"dataset": "returns"}, "no column 'nope'"),
        ({"dataset": "other"}, {"dataset": "returns"}, "share no instrument columns"),
        ({"dataset": "signal"}, {"dataset": "undated"}, "no dates"),
    ],
)
def test_frames_that_do_not_line_up_are_refused(
    tmp_path: Path, signal: dict[str, Any], returns: dict[str, Any], message: str
) -> None:
    rows = _random_returns(9, days=20, names=2)
    store = _store(
        tmp_path,
        {
            "signal": (rows, ["A", "B"], DAYS[:20]),
            "returns": (rows, ["A", "B"], DAYS[:20]),
            "other": (rows, ["C", "D"], DAYS[:20]),
        },
    )
    undated = tmp_path / "undated.csv"
    undated.write_text("A,B\n" + "\n".join(f"{a},{b}" for a, b in rows) + "\n")
    store.put("undated", undated)
    policy = PermissionPolicy(granted=PermissionPolicy().granted | {Permission.READ_LOCAL_DATA})
    record = ToolExecutor(analytics_tools(store), policy).execute(
        ToolCall(tool="analyze_signal", arguments={"signal": signal, "returns": returns})
    )

    assert record.status == "error"
    assert message in (record.error or "")


def test_a_long_only_book_of_one_asset_earns_its_returns_less_one_cost(tmp_path: Path) -> None:
    returns = [[r[0]] for r in _random_returns(10, days=60, names=1)]
    days = DAYS[:60]
    signal = [[1.0] for _ in days]
    store = _store(tmp_path, {"signal": (signal, ["A"], days), "returns": (returns, ["A"], days)})

    output = _call(store, "run_backtest", **FRAMES, weighting="long_only", quantiles=1, cost_bps=20)

    # The signal of the first date is held from the third (one bar to trade, one to hold).
    held = [row[0] for row in returns[2:]]
    net = [held[0] - 0.5 * 20 / 10_000, *held[1:]]  # one-way turnover of the first buy is 0.5
    growth = math.prod(1 + value for value in net)
    summary = output["summary"]
    assert summary["first_date"] == days[2].isoformat()
    assert summary["bars"] == 58
    assert summary["annualized_return"] == pytest.approx(growth ** (252 / 58) - 1)
    assert summary["sharpe_ratio"] == pytest.approx(
        statistics.mean(net) / statistics.stdev(net) * math.sqrt(252)
    )
    assert summary["hit_rate"] == pytest.approx(sum(v > 0 for v in net) / len(net))
    assert summary["annual_turnover"] == pytest.approx(0.5 / 58 * 252)
    assert summary["cost_drag"] == pytest.approx(0.5 * 20 / 10_000 / 58 * 252)
    assert output["book"]["mean_gross_exposure"] == pytest.approx(1.0)
    assert "not a forecast" in output["conventions"]["simulation"]


def test_a_dollar_neutral_pair_earns_half_the_spread_and_pays_its_rebalancing(
    tmp_path: Path,
) -> None:
    rng = random.Random(11)
    days = DAYS[:80]
    returns = [[0.0, round(rng.gauss(0.001, 0.01), 6)] for _ in days]
    signal = [[0.0, 1.0] for _ in days]  # short A, long B, half the book each
    store = _store(
        tmp_path, {"signal": (signal, ["A", "B"], days), "returns": (returns, ["A", "B"], days)}
    )

    output = _call(store, "run_backtest", **FRAMES, cost_bps=10)

    b = [row[1] for row in returns[2:]]
    gross = [0.5 * value for value in b]
    # Rebalancing back to -0.5 / +0.5 after B moves r trades 0.25 |r| / (1 + 0.5 r), one-way.
    turnover = [0.5] + [0.25 * abs(r) / (1 + 0.5 * r) for r in b[:-1]]
    costs = output["costs"]
    assert costs["gross_return"] == pytest.approx(statistics.mean(gross) * 252)
    assert costs["annual_turnover"] == pytest.approx(statistics.mean(turnover) * 252)
    assert costs["cost_drag"] == pytest.approx(statistics.mean(turnover) * 10 / 10_000 * 252)
    assert output["book"]["mean_net_exposure"] == pytest.approx(0.0)
    assert output["book"]["mean_positions"] == pytest.approx(2.0)


def test_a_longer_lag_starts_the_backtest_later(tmp_path: Path) -> None:
    returns = _random_returns(12)
    days = DAYS[:120]
    store = _store(
        tmp_path,
        {"signal": (_random_returns(13), NAMES, days), "returns": (returns, NAMES, days)},
    )

    one = _call(store, "run_backtest", **FRAMES, cost_bps=5)
    three = _call(store, "run_backtest", **FRAMES, cost_bps=5, lag=3)
    zero = _call(store, "run_backtest", **FRAMES, cost_bps=5, lag=0)

    assert one["summary"]["first_date"] == days[2].isoformat()
    assert three["summary"]["first_date"] == days[4].isoformat()
    assert zero["summary"]["first_date"] == days[1].isoformat()
    assert any("lag is 0" in warning for warning in zero["warnings"])


def test_a_backtest_compares_with_a_benchmark_on_its_dates(tmp_path: Path) -> None:
    returns = _random_returns(14, names=1)
    days = DAYS[:120]
    store = _store(
        tmp_path,
        {
            "signal": ([[1.0] for _ in days], ["A"], days),
            "returns": ([[row[0]] for row in returns], ["A"], days),
            "bench": ([[row[0]] for row in returns[:100]], ["index"], days[:100]),
        },
    )

    output = _call(
        store,
        "run_backtest",
        **FRAMES,
        weighting="long_only",
        quantiles=1,
        cost_bps=0,
        benchmark={"dataset": "bench", "column": "index"},
    )

    assert output["benchmark"]["beta"] == pytest.approx(1.0)
    assert output["benchmark"]["correlation"] == pytest.approx(1.0)
    assert any("no return on 20" in warning for warning in output["warnings"])
    assert "dataset:bench" in output["data_versions"]


def test_a_signal_that_never_trades_is_refused(tmp_path: Path) -> None:
    days = DAYS[:20]
    store = _store(
        tmp_path,
        {
            "signal": ([[math.nan, math.nan] for _ in days], ["A", "B"], days),
            "returns": (_random_returns(15, days=20, names=2), ["A", "B"], days),
        },
    )
    policy = PermissionPolicy(granted=PermissionPolicy().granted | {Permission.READ_LOCAL_DATA})
    record = ToolExecutor(analytics_tools(store), policy).execute(
        ToolCall(tool="run_backtest", arguments={**FRAMES, "cost_bps": 5})
    )

    assert record.status == "error"
    assert "never led to a position" in (record.error or "")


def test_the_signal_tools_honour_the_contract(tmp_path: Path) -> None:
    days = DAYS[:60]
    store = _store(
        tmp_path,
        {
            "signal": (_random_returns(16, days=60), NAMES, days),
            "returns": (_random_returns(17, days=60), NAMES, days),
        },
    )
    tools = analytics_tools(store)

    check_tool(tools.get("analyze_signal"), FRAMES)
    check_tool(tools.get("run_backtest"), {**FRAMES, "cost_bps": 10})


def test_the_signal_tools_need_a_dataset_store() -> None:
    assert [spec.name for spec in analytics_tools().specs()] == [
        "analyze_performance",
        "analyze_risk",
    ]


def test_horizons_follow_the_dates_frequency_when_not_given(tmp_path: Path) -> None:
    months = [date(2015 + n // 12, n % 12 + 1, 28) for n in range(60)]
    returns = _random_returns(3, days=60)
    signal = _random_returns(4, days=60)
    store = _store(tmp_path, {"sig": (signal, NAMES, months), "ret": (returns, NAMES, months)})

    monthly = _call(store, "analyze_signal", signal={"dataset": "sig"}, returns={"dataset": "ret"})
    given = _call(
        store,
        "analyze_signal",
        signal={"dataset": "sig"},
        returns={"dataset": "ret"},
        horizons=[2],
    )

    assert [h["horizon"] for h in monthly["horizons"]] == [1, 3, 12]
    assert (
        monthly["conventions"]["horizons"] == "1, 3, 12 bars, from the dates' frequency (monthly)"
    )
    assert given["conventions"]["horizons"] == "2 bars, given"
    assert any("Only 10 instruments" in w for w in monthly["warnings"])


def test_a_signal_carried_forward_from_a_slower_frequency_is_warned_about(
    tmp_path: Path,
) -> None:
    returns = _random_returns(5)
    fresh = _random_returns(6)
    # A new value every 21 days, carried forward in between: a monthly score on daily dates.
    carried = [fresh[at - at % 21] for at in range(len(fresh))]
    days = DAYS[: len(returns)]
    store = _store(
        tmp_path,
        {"sig": (carried, NAMES, days), "new": (fresh, NAMES, days), "ret": (returns, NAMES, days)},
    )

    slow = _call(store, "analyze_signal", signal={"dataset": "sig"}, returns={"dataset": "ret"})
    daily = _call(store, "analyze_signal", signal={"dataset": "new"}, returns={"dataset": "ret"})

    warning = next(w for w in slow["warnings"] if "carried forward" in w)
    assert "every 24 bars" in warning and "horizons 1, 5, 21 " in warning  # 119 pairs, 5 changes
    assert not any("carried forward" in w for w in daily["warnings"])
