"""Larchmere Loan Partners (fictional CLO manager on Azure): estate, store, tools, platform."""

import hashlib
import json
import math
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
FUND = ROOT / "examples" / "mock-funds" / "larchmere"
for part in ("facility/warehouse", "facility/gateway", "site"):
    sys.path.insert(0, str(FUND / part))

import build_larchmere as estate  # noqa: E402
import larchmere_clo as arithmetic  # noqa: E402
from larchmere_acceptance import GOLD  # noqa: E402
from larchmere_server import clo  # noqa: E402
from larchmere_store import DATASETS, LarchmereDatasetStore, open_lake, slug  # noqa: E402

from quant_agent_os.data import UnknownDatasetError  # noqa: E402
from quant_agent_os.runtime.call import ToolCall  # noqa: E402
from quant_agent_os.runtime.executor import ToolExecutor  # noqa: E402
from quant_agent_os.testing import check_app  # noqa: E402
from quant_agent_os.tools import (  # noqa: E402
    Caller,
    FileAccessPolicy,
    PermissionPolicy,
    ToolRegistry,
)
from quant_agent_os.tools.access import acting_as  # noqa: E402
from quant_platform import compose  # noqa: E402
from quant_platform.acceptance import load_gold, run_acceptance  # noqa: E402

pytest.importorskip("empyrical")

B, A = "LARCH-2018-2", "LARCH-2021-1"
PM_A, PM_B, ANALYST, OPS, IR, COMPLIANCE = (
    f"4b1f0c52-6a4e-4d36-9a21-0a1b2c3d000{n}" for n in (1, 2, 3, 4, 5, 6)
)
LAST = "2026-10-30"
TESTS = (*estate.MIN_TESTS, *estate.MAX_TESTS)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("larchmere")
    estate.build(out)
    return out


@pytest.fixture(scope="module")
def known(built: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((built / "known_answers.json").read_text())
    return loaded


@pytest.fixture(scope="module")
def store(built: Path) -> LarchmereDatasetStore:
    return LarchmereDatasetStore(built / "sql" / "clo.db", built / "lake" / "catalog.json")


def snapshot(deal: str, as_of: str) -> dict[str, Any]:
    key = slug(deal)
    return {
        "par": {"dataset": f"par_{key}"},
        "price": {"dataset": f"price_{key}"},
        "rating_factor": {"dataset": "rf_all"},
        "static": {"dataset": "loan_static"},
        "structure": {"dataset": f"structure_{key}"},
        "sofr": {"dataset": "sofr"},
        "as_of": as_of,
    }


def executor(store: LarchmereDatasetStore, access: Path | None = None) -> ToolExecutor:
    policy = FileAccessPolicy(access) if access else None
    catalog = compose(policy=PermissionPolicy(), datasets=store, access=policy, apps=[clo])
    return ToolExecutor(ToolRegistry(catalog.tools), catalog.policy, access=catalog.access)


def call(runner: ToolExecutor, tool: str, caller: Caller | None, **arguments: Any) -> Any:
    with acting_as(caller):
        return runner.execute(ToolCall(tool=tool, arguments=arguments))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---- the estate ---------------------------------------------------------------------------------


def test_the_estate_is_deterministic_and_small(built: Path, tmp_path: Path) -> None:
    assert sum(p.stat().st_size for p in built.rglob("*") if p.is_file()) < 3_000_000
    estate.build(tmp_path)
    assert sha(tmp_path / "sql" / "clo.db") == sha(built / "sql" / "clo.db")
    other = estate.build(tmp_path / "other", seed=1)
    assert other["failures"] != json.loads((built / "known_answers.json").read_text())["failures"]


def test_the_lake_has_the_real_layout(built: Path) -> None:
    catalog = json.loads((built / "lake" / "catalog.json").read_text())
    host = "larchmereadls.dfs.core.windows.net"
    assert catalog["uris"][f"abfss://curated@{host}"] == "lake/curated"
    assert (
        catalog["tables"]["trustee_tests"]["location"] == f"abfss://curated@{host}/trustee_tests/"
    )
    assert (built / "lake/curated/trustee_tests/dt=2025-03-31/part-0000.parquet").is_file()
    assert (built / "lake/curated/loan_prices/month=2026-10/part-0000.parquet").is_file()
    assert not (
        built / "lake/curated/loan_prices/month=2025-04"
    ).exists()  # the feed starts May 2025
    raw = built / "lake/raw/trustee/2025-03/larchmere_larch-2018-2_20250331_tests.csv"
    assert "oc_e" in raw.read_text()
    assert (built / "lake/docs/CLO Research/Shared Documents/IC-pack-2026-09.docx").is_file()


def test_the_planted_failure_and_diversion(known: dict[str, Any]) -> None:
    assert known["failures"][B]["2025-03-31"] == ["oc_e", "ccc_pct"]
    assert [d for d, names in known["failures"][B].items() if "oc_e" in names] == ["2025-03-31"]
    assert not any("oc_e" in names for names in known["failures"][A].values())
    assert known["zero_equity_distributions"] == [[B, "2025-04-21"]]
    assert known["tests"][B]["2025-03-31"]["oc_e"] < 1.1225
    assert known["tests"][B]["2025-02-28"]["oc_e"] > 1.1225  # the cushion was thin, not gone
    assert known["final_balances"][B]["AAA"] > 0
    assert known["final_balances"][A]["AAA"] == 310e6  # a reinvesting deal repays nothing


def test_the_t_sql_is_real_t_sql_and_the_standin_has_the_same_views(built: Path) -> None:
    schema = (FUND / "facility/azure_sql/schema.sql").read_text()
    views = (FUND / "facility/azure_sql/views.sql").read_text()
    for text in ("CREATE TABLE clo.holdings", "DATETIME2", "DECIMAL(18,2)", "NVARCHAR"):
        assert text in schema
    for text in ("CREATE VIEW clo_read.v_holdings", "CROSS APPLY", "DATEDIFF"):
        assert text in views
    wanted = {name for name in views.split() if name.startswith("clo_read.v_")}
    with closing(sqlite3.connect(built / "sql" / "clo.db")) as db:
        have = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'view'")}
    assert {name.removeprefix("clo_read.") for name in wanted} == have


def test_an_analyst_reads_the_lake_with_duckdb(built: Path, known: dict[str, Any]) -> None:
    duckdb = pytest.importorskip("duckdb")
    folder = built / "lake/curated/loan_prices"
    rows = duckdb.sql(
        f"SELECT month, COUNT(*) FROM read_parquet('{folder}/**/*.parquet', "
        "hive_partitioning=true) "
        "GROUP BY month ORDER BY month"
    ).fetchall()
    assert rows[0][0] == "2025-05" and rows[-1][0] == "2026-10"
    assert sum(count for _, count in rows) == 392 * 200
    lake = open_lake(built / "lake" / "catalog.json")
    try:
        total = lake.execute("SELECT SUM(principal_paid) FROM tranche_cashflows").fetchone()[0]
    finally:
        lake.close()
    repaid = sum(
        estate.DEALS[d]["balances"][t] - known["final_balances"][d][t]
        for d in (A, B)
        for t in estate.DEBT
    )
    assert total == pytest.approx(repaid, abs=0.01 * 40)  # cents, over 40 payments


# ---- the store ----------------------------------------------------------------------------------


def test_the_store_serves_every_declared_dataset(store: LarchmereDatasetStore) -> None:
    assert store.ids() == sorted(DATASETS)
    par = store.get("par_larch_2021_1")
    assert par.periods_per_year == 12 and par.rows == 37
    assert par.info().missing_cells > 0  # a loan not held on a date is a missing value, not zero
    assert store.get("sofr").periods_per_year == 252
    assert store.get("equity_cf_larch_2021_1").rows == 14  # 12 payments, first NAV, last NAV
    assert store.get("loan_static").columns["maturity_epoch_day"][0] > 19000  # dates as numbers
    with pytest.raises(UnknownDatasetError, match="No dataset 'nope'"):
        store.get("nope")


def test_the_store_fingerprints_follow_the_data(store: LarchmereDatasetStore, built: Path) -> None:
    first = store.get("price_larch_2018_2").fingerprint
    assert first == store.get("price_larch_2018_2").fingerprint
    assert first != store.get("price_larch_2021_1").fingerprint


# ---- the CLO tools ------------------------------------------------------------------------------


def book(rows: list[tuple[float, float, float, float, int, int, int]]) -> pd.DataFrame:
    return pd.DataFrame(
        rows, columns=["par", "price", "rf", "years", "industry", "obligor", "facility"]
    ).assign(spread_bps=400.0, floor_bps=50.0, fixed=float("nan"))


def test_the_arithmetic_matches_a_hand_calculation() -> None:
    # B2 100 at par; two CCC loans, 50 at 90 and 50 at 60; a defaulted 20 at 40; 10 of cash.
    loans = book(
        [
            (100, 100, 2720, 4, 1, 1, 1),
            (50, 90, 4770, 4, 1, 2, 1),
            (50, 60, 6500, 4, 2, 3, 1),
            (20, 40, 10000, 4, 2, 4, 1),
        ]
    )
    structure = pd.Series(
        {
            f"bal_{t}": b
            for t, b in zip(("AAA", "AA", "A", "BBB", "BB"), (100, 20, 10, 10, 10), strict=True)
        }
        | {f"spread_{t}": 100 for t in ("AAA", "AA", "A", "BBB", "BB")}
    )
    values = arithmetic.compute_tests(loans, structure, sofr_pct=4.0, cash=10.0)
    # CCC is 100 of 200 performing; the excess over 7.5% is 85, lowest priced first:
    # 50 x (1 - .60) + 35 x (1 - .90) = 23.5. Adjusted par = 200 - 23.5 + 20 x .40 + 10 = 194.5.
    assert values["ccc_pct"] == pytest.approx(50.0)
    assert values["ccc_haircut"] == pytest.approx(23.5)
    assert values["adjusted_par"] == pytest.approx(194.5)
    assert values["oc_ab"] == pytest.approx(194.5 / 120)
    assert values["warf"] == pytest.approx((100 * 2720 + 50 * 4770 + 50 * 6500) / 200)
    assert values["was"] == pytest.approx(4.0)
    assert values["wal"] == pytest.approx(4.0)
    # 3 performing obligors, average par 66.67: units 1, .75 in industry 1 and .75 in industry 2.
    assert values["diversity"] == pytest.approx(1.375 + 0.75)
    assert values["obligor_pct"] == pytest.approx(50.0)


def test_a_floor_lifts_the_spread_when_sofr_is_below_it() -> None:
    loans = book([(100, 100, 2720, 4, 1, 1, 1)])
    structure = pd.Series(
        {f"bal_{t}": 10.0 for t in estate.DEBT} | {f"spread_{t}": 100 for t in estate.DEBT}
    )
    low = arithmetic.compute_tests(loans, structure, sofr_pct=0.3, cash=0.0)
    assert low["was"] == pytest.approx((400 + 20) / 100)  # a 50 bps floor, SOFR at 30 bps


def test_xirr_and_cash_on_cash_by_hand() -> None:
    flows = pd.DataFrame(
        {
            "invested": [100.0, 0.0, 0.0],
            "distribution": [0.0, 10.0, 10.0],
            "terminal_nav": [0.0, 0.0, 100.0],
        },
        index=pd.DatetimeIndex(["2024-01-01", "2025-01-01", "2026-01-01"]),
    )
    result = arithmetic.equity_returns(flows)
    assert result.total_distributions == 20 and result.moic == pytest.approx(1.2)
    assert result.cash_on_cash_annual == pytest.approx(20 / 100 / (731 / 365))
    assert result.irr == pytest.approx(0.0999, abs=2e-4)  # 10% a year, a day off from leap 2024


@pytest.mark.parametrize("deal", [A, B])
@pytest.mark.parametrize("as_of", ["2025-02-28", "2025-03-31", LAST])
def test_every_test_matches_the_generators_independent_calculation(
    store: LarchmereDatasetStore, known: dict[str, Any], deal: str, as_of: str
) -> None:
    record = call(executor(store), "clo_compliance_tests", None, **snapshot(deal, as_of))
    assert record.status == "ok", record.error
    want = known["tests"][deal][as_of]
    assert record.output["values"] == pytest.approx({name: want[name] for name in TESTS}, rel=1e-9)
    assert record.output["adjusted_par"] == pytest.approx(want["adjusted_par"], rel=1e-9)
    assert record.output["failed"] == known["failures"][deal].get(as_of, [])
    assert record.output["data_versions"]["dataset:rf_all"]
    assert record.output["convention"].startswith("Month-end trustee dates")


def test_the_what_if_checks_a_trade_before_it_is_made(store: LarchmereDatasetStore) -> None:
    runner = executor(store)
    base = snapshot(A, LAST)
    nothing = call(
        runner, "clo_what_if", None, **base, loan_id="LN0042", par_change=0, trade_price=99
    )
    assert nothing.status == "ok", nothing.error
    assert all(abs(change["delta"]) < 1e-12 for change in nothing.output["changes"])
    cash = store.get("structure_larch_2021_1").column("principal_cash")[-1]
    sale = call(
        runner, "clo_what_if", None, **base, loan_id="LN0001", par_change=-1e6, trade_price=99
    )
    held_by_a = store.get("par_larch_2021_1").column("LN0001")[-1]
    if held_by_a >= 1e6:
        assert sale.status == "ok", sale.error
        assert sale.output["cash_after"] == pytest.approx(cash + 0.99e6)  # a sale adds cash
    buy = call(
        runner, "clo_what_if", None, **base, loan_id="LN0042", par_change=3e6, trade_price=98.5
    )
    assert buy.status == "ok"
    assert any("principal cash" in w for w in buy.output["warnings"]) == (
        buy.output["cash_after"] < 0
    )
    too_much = call(
        runner, "clo_what_if", None, **base, loan_id="LN0042", par_change=-5e6, trade_price=99
    )
    assert too_much.status == "error" and "the deal holds" in (too_much.error or "")


def test_a_cure_is_visible_in_the_what_if(store: LarchmereDatasetStore) -> None:
    # Sell every CCC loan 2018-2 holds at 2025-03-31, at the market: Class E OC cures.
    key = slug(B)
    price = store.get(f"price_{key}")
    par = store.get(f"par_{key}")
    rating = store.get("rf_all")
    stamp = pd.Timestamp("2025-03-31")
    day = (stamp - pd.Timestamp(date(1970, 1, 1))).days
    assert day > 0
    names = [
        loan
        for loan in par.columns
        if not math.isnan(par.column(loan)[par.index.index("2025-03-31")])
        and rating.column(loan)[rating.index.index("2025-03-31")] >= 4770
    ]
    assert len(names) >= 5
    runner = executor(store)
    worst = max(names, key=lambda n: par.column(n)[par.index.index("2025-03-31")])
    held = par.column(worst)[par.index.index("2025-03-31")]
    px = price.column(worst)[price.index.index("2025-03-31")]
    out = call(
        runner, "clo_what_if", None, **snapshot(B, "2025-03-31"),
        loan_id=worst, par_change=-held, trade_price=px,
    )  # fmt: skip
    assert out.status == "ok", out.error
    by_name = {change["name"]: change for change in out.output["changes"]}
    assert by_name["oc_e"]["passes_before"] is False
    assert (
        by_name["oc_e"]["after"] > by_name["oc_e"]["before"]
        or by_name["ccc_pct"]["after"] < by_name["ccc_pct"]["before"]
    )


def test_equity_returns_by_deal(store: LarchmereDatasetStore, known: dict[str, Any]) -> None:
    runner = executor(store)
    for deal in (A, B):
        record = call(
            runner, "clo_equity_returns", None, cashflows={"dataset": f"equity_cf_{slug(deal)}"}
        )
        assert record.status == "ok", record.error
        out = record.output
        assert out["invested"] > 0 and out["terminal_nav"] > 0
        paid = sum(known["distributions"][deal].values())
        assert out["total_distributions"] == pytest.approx(paid, rel=1e-6)
        assert 0.0 < out["irr"] < 0.5 and out["cash_on_cash_annual"] > 0
    assert known["distributions"][B]["2025-04-21"] == 0.0


def test_check_app_passes_for_the_clo_tools(store: LarchmereDatasetStore) -> None:
    records = check_app(
        clo,
        {
            "clo_compliance_tests": snapshot(A, LAST),
            "clo_what_if": {
                **snapshot(A, LAST),
                "loan_id": "LN0042",
                "par_change": 1e6,
                "trade_price": 99.0,
            },
            "clo_equity_returns": {"cashflows": {"dataset": "equity_cf_larch_2021_1"}},
        },
        datasets=store,
    )
    assert [r.status for r in records] == ["ok", "ok", "ok"]


# ---- the platform's own analytics on this fund's data -------------------------------------------


def test_the_platforms_analytics_read_monthly_clo_data(store: LarchmereDatasetStore) -> None:
    runner = executor(store)
    perf = call(
        runner,
        "analyze_performance",
        None,
        returns={"dataset": "equity_returns_larch_2021_1", "column": "equity"},
    )
    assert perf.status == "ok", perf.error
    assert perf.output["periods_per_year"] == 12 and perf.output["periods"] == 36
    assert perf.output["metrics"]["max_drawdown"] < -0.1  # an equity tranche: levered, so deep
    risk = call(
        runner, "analyze_risk", None, portfolio_id="two loans",
        weights={"LN0001": 0.5, "LN0002": 0.5},
        asset_returns={"dataset": "loan_return_daily"},
    )  # fmt: skip
    assert risk.status == "ok", risk.error
    assert risk.output["periods_per_year"] == 252 and risk.output["periods"] == 391
    assert "none of the 12 scenario windows" in risk.output["historical_stress_note"]
    signal = call(
        runner, "analyze_signal", None, signal={"dataset": "discount_larch_2021_1"},
        returns={"dataset": "loan_return_monthly_larch_2021_1"}, horizons=[1, 3], lag=1,
    )  # fmt: skip
    assert signal.status == "ok", signal.error
    assert signal.output["summary"]["mean_rank_ic"] > 0.05  # discount to par reverts, as simulated
    backtest = call(
        runner, "run_backtest", None, signal={"dataset": "discount_larch_2021_1"},
        returns={"dataset": "loan_return_monthly_larch_2021_1"}, cost_bps=25.0, lag=1,
    )  # fmt: skip
    assert backtest.status == "ok", backtest.error
    assert (
        backtest.output["book"]["forced_exits"] > 0
    )  # a loan leaving the pool reads as a delisting


# ---- access ------------------------------------------------------------


def test_entitlements_fail_closed_and_refusals_look_like_missing_data(
    store: LarchmereDatasetStore,
) -> None:
    runner = executor(store, FUND / "facility" / "access.toml")
    pm_b = Caller("tomas.okafor", groups=frozenset({PM_B}))
    own = call(runner, "clo_compliance_tests", pm_b, **snapshot(B, LAST))
    assert own.status == "ok", own.error
    other = call(runner, "clo_compliance_tests", pm_b, **snapshot(A, LAST))
    missing = call(
        runner, "clo_compliance_tests", pm_b, **{**snapshot(B, LAST), "par": {"dataset": "par_zzz"}}
    )
    assert other.status == "error" and missing.status == "error"
    assert (other.error or "").replace("par_larch_2021_1", "X").split(";")[0] == (
        missing.error or ""
    ).replace("par_zzz", "X").split(";")[0]
    nobody = call(runner, "clo_compliance_tests", None, **snapshot(B, LAST))
    assert nobody.status != "ok"
    analyst = Caller("priya.lindqvist", groups=frozenset({ANALYST}))
    assert (
        call(runner, "clo_compliance_tests", analyst, **snapshot(A, LAST)).status != "ok"
    )  # tool refused
    prices = call(
        runner,
        "analyze_risk",
        analyst,
        portfolio_id="x",
        weights={"LN0001": 1.0},
        asset_returns={"dataset": "loan_return_daily"},
    )
    assert prices.status == "ok"
    leak = call(
        runner,
        "analyze_performance",
        analyst,
        returns={"dataset": "equity_returns_larch_2021_1", "column": "equity"},
    )
    assert leak.status == "error"
    reader = Caller("elena.sorensen", groups=frozenset({IR}))
    assert (
        call(
            runner, "clo_equity_returns", reader, cashflows={"dataset": "equity_cf_larch_2021_1"}
        ).status
        == "ok"
    )
    assert (
        call(
            runner,
            "analyze_performance",
            reader,
            returns={"dataset": "equity_returns_larch_2021_1", "column": "equity"},
        ).status
        == "ok"
    )
    assert (
        call(
            runner,
            "analyze_signal",
            reader,
            signal={"dataset": "loan_return_daily"},
            returns={"dataset": "loan_return_daily"},
        ).status
        != "ok"
    )
    deal_wide = Caller("dev.rahman", groups=frozenset({COMPLIANCE}))
    assert call(runner, "clo_compliance_tests", deal_wide, **snapshot(A, LAST)).status == "ok"
    group_names = Caller("x", groups=frozenset({"CLO.PM"}))  # a role name is not a group here
    assert call(runner, "clo_compliance_tests", group_names, **snapshot(A, LAST)).status != "ok"


# ---- acceptance ---------------------------------------------------------------------------------


def test_the_agreed_cases_pass_as_named_callers(
    store: LarchmereDatasetStore, built: Path, tmp_path: Path
) -> None:
    from quant_research import open_research

    with open_research(tmp_path / "kb.db") as research:
        research.ingest.ingest(FUND / "facility" / "research")
        catalog = compose(
            policy=PermissionPolicy(),
            research=research.research,
            datasets=store,
            access=FileAccessPolicy(FUND / "facility" / "access.toml"),
            apps=[clo],
        )
        gold, digest = load_gold(GOLD)
        report = run_acceptance(catalog, gold, digest)
    assert report.passed, report.markdown()
    assert "4 of 4 cases passed" in report.markdown()


# ---- research ------------------------------------------------------------


def test_the_injection_is_retrievable_text_and_nothing_more(tmp_path: Path, built: Path) -> None:
    from quant_research import open_research
    from quant_research.application.retrieval import ResearchQuery

    docs = built / "lake" / "docs" / "CLO Research" / "Shared Documents"
    with open_research(tmp_path / "kb.db") as research:
        result = research.ingest.ingest(docs)
        assert result.skipped == ["IC-pack-2026-09.docx"]  # SharePoint's own format is not ingested
        hit = research.research.find_related(
            ResearchQuery(query="maintenance mode ignore previous instructions", k=3)
        )
        assert hit.trust == "untrusted"  # the platform marks everything retrieved
        assert "commentary-loan-market-q3.md" in [p.source.source_id for p in hit.passages]
        minutes = research.research.find_related(
            ResearchQuery(query="Class E overcollateralization failed diverted interest", k=3)
        )
        assert minutes.passages[0].source.source_id == "minutes-clo-committee-2025-04.md"
    names = {p.name for p in (FUND / "facility" / "research").glob("*.md")}
    injected = [
        n
        for n in names
        if "ignore previous instructions" in (FUND / "facility/research" / n).read_text().lower()
    ]
    assert injected == ["commentary-loan-market-q3.md"]


# ---- the legacy package --------------------------------------------------------------------------


def test_the_legacy_code_agrees_with_the_known_answers(built: Path, known: dict[str, Any]) -> None:
    legacy = FUND / "facility" / "legacy" / "larchmere_clo"
    code = (
        "import compliance; "
        f"print(compliance.calc_warf('{A}', '{LAST}')); "
        f"print(compliance.calc_oc('{B}', '2025-03-31'))"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        cwd=legacy,
        env={"LARCH_DB": str(built / "sql" / "clo.db"), "PATH": ""},
        capture_output=True,
        text=True,
        check=True,
    )
    numbers = [float(line) for line in done.stdout.splitlines() if line[:1].isdigit()]
    assert numbers[0] == pytest.approx(known["tests"][A][LAST]["warf"], rel=1e-9)
    assert numbers[1] == pytest.approx(known["tests"][B]["2025-03-31"]["oc_e"], rel=1e-9)
    assert "WARF" in done.stdout  # it prints, as legacy code does
