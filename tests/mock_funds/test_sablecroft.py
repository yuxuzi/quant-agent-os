"""examples/mock-funds/sablecroft: a fund of hedge funds and private markets program on Azure.

The warehouse is a local stand-in for ADLS Gen2 and Azure Databricks (Delta tables, DuckDB for the
SQL warehouse); the identity provider is a mock Microsoft Entra ID. Nothing here leaves the machine.
"""

import csv
import json
import math
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import anyio
import numpy as np
import pandas as pd
import pytest
from pydantic import SecretStr

ROOT = Path(__file__).parents[2] / "examples" / "mock-funds" / "sablecroft"
for _folder in (
    "site",
    "facility/warehouse",
    "facility/identity",
    "facility/legacy",
):
    sys.path.insert(0, str(ROOT / _folder))

import build_sablecroft as build_module  # noqa: E402
import sablecroft_entra_mock  # noqa: E402
import sablecroft_server as server  # noqa: E402
import sablecroft_tools as tools  # noqa: E402
from sablecroft_entra import EntraValidator, serve_introspection  # noqa: E402
from sablecroft_store import ENTRIES, STRATEGIES, SablecroftStore  # noqa: E402
from sablecroft_warehouse import LocalDeltaWarehouse, abfss_to_path  # noqa: E402

from quant_agent_os.data import UnknownDatasetError  # noqa: E402
from quant_agent_os.mcp.http import GROUPS_CLAIM  # noqa: E402
from quant_agent_os.runtime import ToolCall, ToolExecutor  # noqa: E402
from quant_agent_os.testing import check_app  # noqa: E402
from quant_agent_os.tools import (  # noqa: E402
    Caller,
    FileAccessPolicy,
    Permission,
    PermissionPolicy,
    ToolRegistry,
)
from quant_agent_os.tools.access import acting_as  # noqa: E402
from quant_platform import compose  # noqa: E402
from quant_platform.acceptance import load_gold, run_acceptance  # noqa: E402
from quant_platform.identity.oauth import AuthorizationServer, IntrospectionVerifier  # noqa: E402
from quant_platform.identity.sign_in import load_sign_in_file  # noqa: E402
from quant_research import ResearchQuery, open_research  # noqa: E402

RESOURCE = "https://qaos.sablecroft.example/mcp"
ACCESS = ROOT / "facility" / "access.toml"
PLATFORM_IMPORT = re.compile(
    r"^\s*(from|import)\s+(quant_agent_os|quant_platform|quant_research|quant_analytics|"
    r"quant_app_factory|quant_toolbox)\b",
    re.MULTILINE,
)
GROUP = sablecroft_entra_mock.GROUPS
ADMIN_MONTH = "202512"


@pytest.fixture(scope="module")
def lake(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("sablecroft")
    build_module.build(path)
    return path


@pytest.fixture(scope="module")
def answers(lake: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((lake / "known_answers.json").read_text())
    return loaded


@pytest.fixture(scope="module")
def warehouse(lake: Path) -> LocalDeltaWarehouse:
    return LocalDeltaWarehouse(lake)


@pytest.fixture(scope="module")
def store(warehouse: LocalDeltaWarehouse) -> SablecroftStore:
    return SablecroftStore(warehouse)


def frame(store: SablecroftStore, dataset_id: str) -> pd.DataFrame:
    """A dataset as pandas, the way the platform hands it to a tool."""
    data = store.get(dataset_id)
    index = pd.DatetimeIndex(data.dates, name="date") if data.dates else pd.Index(data.index)
    return pd.DataFrame(data.columns, index=index)


def read_csv(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.read_text().splitlines()))


def column(store: SablecroftStore, dataset_id: str, name: str) -> pd.Series:
    return frame(store, dataset_id)[name]


def run(catalog: Any, caller: Caller | None, tool: str, arguments: dict[str, Any]) -> Any:
    executor = ToolExecutor(ToolRegistry(catalog.tools), catalog.policy, access=catalog.access)
    with acting_as(caller):
        return executor.execute(ToolCall(tool=tool, arguments=arguments))


def platform_catalog(store: SablecroftStore, access: FileAccessPolicy | None = None) -> Any:
    policy = PermissionPolicy(granted=PermissionPolicy().granted | {Permission.READ_LOCAL_DATA})
    return compose(policy=policy, datasets=store, access=access, apps=[server.app])


# ---- the estate: ADLS Gen2 and Unity Catalog stand-ins ----------------------------------------


def test_the_same_seed_builds_the_same_lake_quickly_and_small(tmp_path: Path, lake: Path) -> None:
    started = time.perf_counter()
    build_module.build(tmp_path / "again")
    assert time.perf_counter() - started < 2.0
    build_module.build(tmp_path / "other", seed=1)
    first = json.loads((tmp_path / "again" / "known_answers.json").read_text())
    assert first == json.loads((lake / "known_answers.json").read_text())
    other = json.loads((tmp_path / "other" / "known_answers.json").read_text())
    assert other["private_funds"]["funds"]["PM02"] != first["private_funds"]["funds"]["PM02"]
    assert sum(p.stat().st_size for p in lake.rglob("*") if p.is_file()) < 3_000_000


def test_the_lake_has_adls_layout_delta_logs_and_raw_statements(lake: Path) -> None:
    lake_map = json.loads((lake / "lake_map.json").read_text())
    host = "sablecroftlake.dfs.core.windows.net"
    assert lake_map == {f"abfss://raw@{host}": "raw", f"abfss://uc@{host}": "uc"}
    returns = abfss_to_path(f"abfss://uc@{host}/sablecroft/curated/mgr_returns", lake_map, lake)
    assert (returns / "dt=2025-12-31" / "part-0000.parquet").is_file()
    log = (returns / "_delta_log" / "00000000000000000000.json").read_text().splitlines()
    assert sum("add" in json.loads(line) for line in log) == 60
    assert (lake / "uc/sablecroft/curated/pm_cashflows/fund_id=PM01/part-0000.parquet").is_file()
    assert (lake / "uc/sablecroft/ref/managers/part-0000.parquet").is_file()
    statements = list((lake / "raw" / "admin-nav").glob("*/*/nav-statement.csv"))
    assert len(statements) == 6  # two administrators, three months
    assert len(list((lake / "raw" / "gp-statements").glob("*/2025Q3-capital-account.csv"))) == 12
    with pytest.raises(ValueError, match="No ADLS container"):
        abfss_to_path(f"abfss://other@{host}/x", lake_map, lake)


def test_raw_statements_agree_with_the_curated_tables(lake: Path, store: SablecroftStore) -> None:
    seen = 0
    for path in (lake / "raw" / "admin-nav").glob(f"*/{ADMIN_MONTH}/nav-statement.csv"):
        for row in read_csv(path):
            dataset = "ret_final" if row["status"] == "final" else "ret_est"
            value = column(store, dataset, row["manager_id"]).loc[pd.Timestamp(row["period_end"])]
            assert value == pytest.approx(float(row["mtd_return"]), abs=1e-12)
            seen += 1
    assert seen == 22 - 1  # the liquidated manager has no December statement
    # The GP statements' ending NAV is the curated quarter-end NAV.
    nav = frame(store, "pm_nav")
    for path in (lake / "raw" / "gp-statements").glob("*/2025Q3-capital-account.csv"):
        (row,) = read_csv(path)
        assert nav.loc[pd.Timestamp(row["quarter_end"]), row["fund_id"]] == pytest.approx(
            float(row["ending_nav"])
        )


def test_the_warehouse_serves_unity_catalog_names_and_delta_versions(
    warehouse: LocalDeltaWarehouse,
) -> None:
    _, rows = warehouse.query("SELECT COUNT(*) FROM sablecroft.ref.managers")
    assert rows == [(25,)]
    _, rows = warehouse.query(
        "SELECT manager_id FROM sablecroft.serving.v_ret_final WHERE strategy = ? "
        "AND period_end = '2025-11-30' ORDER BY 1",
        ("cta",),
    )
    assert [r[0] for r in rows] == ["CT01", "CT02", "CT03"]
    assert "curated.mgr_returns@0" in warehouse.data_version()


# ---- planted truths --------------------------------------------------------------------------


def test_the_planted_truths_hold_in_the_served_data(
    store: SablecroftStore, answers: dict[str, Any]
) -> None:
    final, est = frame(store, "ret_final"), frame(store, "ret_est")
    revision = (final["EV02"] - est["EV02"]).dropna()
    planted = answers["revisions"]["managers"]["EV02"]
    assert len(revision) == planted["n"]
    assert revision.mean() == pytest.approx(planted["mean_revision"], abs=1e-12)
    assert planted["t_stat"] > 5 and abs(answers["revisions"]["managers"]["EQ01"]["t_stat"]) < 2
    for manager in ("IC01", "RA01"):
        smoothing = answers["smoothing"]["managers"][manager]
        assert 1.5 < smoothing["sample_ratio"] < 1.8
        assert smoothing["population_ratio"] == pytest.approx(
            1 / math.sqrt(sum(t * t for t in smoothing["theta"]))
        )
    calibration = answers["private_funds"]["funds"]["PM01"]
    assert (calibration["irr"], calibration["tvpi"]) == (pytest.approx(0.2), pytest.approx(1.32))
    # The data cut: finals arrive 15, 45, or ~75 days late, and a liquidated manager stops.
    last = answers["availability_at_as_of"]["last_final_month_by_lag"]
    assert last == {"15": "2025-11-30", "45": "2025-10-31"}
    assert final["EV04"].dropna().index[-1] == pd.Timestamp("2024-06-30")
    assert final.loc["2025-11-30", "EQ01"] == final.loc["2025-11-30", "EQ01"]  # present
    assert math.isnan(final.loc["2025-11-30", "EV01"])  # 45-day lag, not yet final
    assert est.loc["2025-12-31", "EV01"] == est.loc["2025-12-31", "EV01"]  # the estimate is there


# ---- the dataset store -----------------------------------------------------------------------


def test_the_store_serves_monthly_quarterly_and_daily_datasets(store: SablecroftStore) -> None:
    assert store.ids() == sorted(ENTRIES)
    assert len(store.ids()) == 16 + 2 * len(STRATEGIES)
    assert store.get("ret_final").periods_per_year == 12
    assert store.get("ret_final_q").periods_per_year == 4
    assert store.get("idx_public").periods_per_year == 252
    assert store.get("pm_calls").periods_per_year is None  # irregular dated flows
    assert list(store.get("ret_final_cta").columns) == ["CT01", "CT02", "CT03"]
    terms = store.get("mgr_terms")
    assert terms.index is not None and len(terms.index) == 25
    assert terms.column("strategy_code")[0] == 1.0
    with pytest.raises(UnknownDatasetError, match="No dataset 'nope'"):
        store.get("nope")
    assert store.get("ret_final").fingerprint == store.get("ret_final").fingerprint


def test_cash_flows_are_gross_and_eur_returns_are_converted(store: SablecroftStore) -> None:
    assert list(column(store, "pm_calls", "PM01").dropna()) == [100e6]
    assert list(column(store, "pm_distributions", "PM01").dropna()) == [60e6, 72e6]
    best, fx = frame(store, "ret_best"), column(store, "fx_eurusd", "EURUSD")
    usd = frame(store, "ret_usd")
    day, before = pd.Timestamp("2025-06-30"), pd.Timestamp("2025-05-31")
    expected = (1 + best.loc[day, "EQ03"]) * fx.loc[day] / fx.loc[before] - 1
    assert usd.loc[day, "EQ03"] == pytest.approx(expected)
    assert usd.loc[day, "EQ01"] == best.loc[day, "EQ01"]  # a USD manager is unchanged


# ---- the fund's own tools, with known answers -------------------------------------------------


def test_xirr_matches_the_textbook_example() -> None:
    dates = pd.to_datetime(["2008-01-01", "2008-03-01", "2008-10-30", "2009-02-15", "2009-04-01"])
    amounts = np.array([-10000.0, 2750.0, 4250.0, 3250.0, 2750.0])
    days = np.array([(d - dates[0]).days for d in dates], dtype=float)
    assert tools.xirr(days, amounts) == pytest.approx(0.373362535, abs=1e-6)  # Excel's XIRR
    with pytest.raises(ValueError, match="outflow"):
        tools.xirr(days, np.abs(amounts))


def test_private_fund_figures_match_the_independent_known_answers(
    store: SablecroftStore, answers: dict[str, Any]
) -> None:
    funds = answers["private_funds"]["funds"]
    ccy = {"PM03": "SCI_EU", "PM08": "SCI_EU", "PM11": "SCI_EU"}
    for fund, truth in funds.items():
        calls, dists = column(store, "pm_calls", fund), column(store, "pm_distributions", fund)
        nav = column(store, "pm_nav", fund)
        got = tools.private_fund_returns(calls, dists, nav)
        for key in ("paid_in", "distributions", "nav", "tvpi", "dpi", "rvpi", "irr"):
            assert getattr(got, key) == pytest.approx(truth[key], rel=1e-7, abs=1e-9), (fund, key)
        index = column(store, "idx_public", ccy.get(fund, "SCI_US"))
        pme = tools.private_fund_pme(calls, dists, nav, index)
        assert pme.ks_pme == pytest.approx(truth["ks_pme"], rel=1e-9)
        if truth["ln_pme_index_irr"] is None:
            assert pme.ln_index_irr is None and "undefined" in pme.warnings[0]
        else:
            assert pme.ln_index_irr == pytest.approx(truth["ln_pme_index_irr"], abs=1e-7)
    assert funds["PM01"]["irr"] == pytest.approx(0.2) and funds["PM01"]["dpi"] == pytest.approx(
        1.32
    )
    assert any(t["ln_pme_index_irr"] is None for t in funds.values())  # a case LN cannot do


def test_pme_by_hand() -> None:
    calls = pd.Series([100.0], index=pd.to_datetime(["2020-01-01"]))
    dists = pd.Series([121.0], index=pd.to_datetime(["2022-01-01"]))
    nav = pd.Series([0.0], index=pd.to_datetime(["2022-06-30"]))
    flat = pd.Series(100.0, index=pd.date_range("2019-12-01", "2022-07-01", freq="D"))
    result = tools.private_fund_pme(calls, dists, nav, flat)
    assert result.ks_pme == pytest.approx(1.21)  # a flat index: PME is the multiple
    assert result.ln_index_irr is None  # distributions exceed the index units the calls bought
    grown = pd.Series(100.0 * 1.1 ** (np.arange(len(flat)) / 365.0), index=flat.index)
    assert tools.private_fund_pme(calls, dists, nav, grown).ks_pme < 1.21


def test_lo_sharpe_by_hand_and_on_the_smoothed_manager(store: SablecroftStore) -> None:
    alternating = pd.Series([1.0, -1.0, 1.0, -1.0])
    # rho1 = -0.75; eta(2) = 2 / sqrt(2 + 2 * 1 * -0.75); per-period Sharpe 0.
    shifted = alternating + 0.5  # mean 0.5, sd 1.1547 (n-1)
    got = tools.lo_adjusted_sharpe(shifted, q=2)
    per_period = 0.5 / shifted.std(ddof=1)
    rho1 = -0.75 * 4 / 4  # the shift does not change the demeaned autocorrelation
    assert got.eta == pytest.approx(2 / math.sqrt(2 + 2 * rho1))
    assert got.adjusted_sharpe == pytest.approx(per_period * got.eta)
    smoothed = column(store, "ret_final", "IC01")
    adjusted = tools.lo_adjusted_sharpe(smoothed)
    assert adjusted.eta > 1.3 and adjusted.adjusted_sharpe < 0.5 * adjusted.raw_sharpe
    with pytest.raises(ValueError, match="more than 12"):
        tools.lo_adjusted_sharpe(smoothed.head(10))


def test_unsmoothing_finds_the_planted_ratio_roughly(
    store: SablecroftStore, answers: dict[str, Any]
) -> None:
    for manager in ("IC01", "RA01"):
        planted = answers["smoothing"]["managers"][manager]["sample_ratio"]
        found = tools.unsmooth_returns(column(store, "ret_final", manager))
        assert found.theta[0] >= found.theta[1] >= found.theta[2] >= 0
        assert sum(found.theta) == pytest.approx(1.0)
        assert found.unsmoothed_vol == pytest.approx(found.reported_vol * found.vol_ratio)
        # A method-of-moments fit on 58 months: right to within about a fifth, not exact.
        assert abs(found.vol_ratio - planted) / planted < 0.2, (manager, found.vol_ratio, planted)
    # A liquid manager's returns are not smoothed, but the fit still finds a ratio above 1.
    liquid = tools.unsmooth_returns(column(store, "ret_final", "EQ01")).vol_ratio
    assert 1.0 <= liquid < 1.4  # noise alone reads as some smoothing: 1.26 here, a false alarm


def test_the_liquidity_ladder_by_hand_and_on_the_book(store: SablecroftStore) -> None:
    terms = pd.DataFrame(
        {
            "nav_usd": [100.0],
            "lockup_months": [0],
            "notice_days": [30],
            "redemption_freq_months": [3],
            "gate_pct": [0.5],
            "investment_yyyymmdd": [20200101],
            "active": [1],
        },
        index=["X"],
    )
    ladder = tools.liquidity_ladder(terms, "2025-12-31")
    by_label = {b.label: b.usd for b in ladder.buckets}
    # Request 2025-12-31 + 30 days notice: first quarter-end is 2026-03-31, cash 30 days later.
    assert ladder.managers[0].first_dealing_date == "2026-03-31"
    assert by_label["within 180 days"] == 50.0 and by_label["within 365 days"] == 50.0
    book = frame(store, "mgr_terms")
    full = tools.liquidity_ladder(book, "2025-12-31")
    assert full.total_usd == pytest.approx(book.loc[book["active"] == 1, "nav_usd"].sum())
    assert full.buckets[-1].cumulative_share == pytest.approx(1.0)
    assert [m.manager_id for m in full.managers].count("EV04") == 0  # liquidated: not redeemable
    illiquid = {m.manager_id: m for m in full.managers}
    assert illiquid["RA01"].first_payment_days > illiquid["MC01"].first_payment_days


def test_peer_ranking_by_hand_and_on_the_book(store: SablecroftStore) -> None:
    index = pd.date_range("2022-01-31", periods=36, freq="ME")
    returns = pd.DataFrame(
        {
            "A": [0.02, 0.01] * 18,
            "B": [0.03, -0.01] * 18,
            "C": [0.01, 0.0] * 18,
            "D": [np.nan] * 26 + [0.01] * 10,
        },
        index=index,
    )
    terms = pd.DataFrame({"strategy_code": [1, 1, 2, 1]}, index=["A", "B", "C", "D"])
    ranking = tools.manager_peer_ranking(returns, terms)
    rows = {r.manager_id: r for r in ranking.rows}
    assert (rows["A"].rank, rows["B"].rank, rows["C"].rank) == (1, 2, 1)
    assert rows["A"].peers == 2 and rows["C"].peers == 1 and ranking.skipped == ["D"]
    a = np.array([0.02, 0.01] * 18)
    assert rows["A"].annual_vol == pytest.approx(a.std(ddof=1) * math.sqrt(12))
    assert rows["A"].annual_return == pytest.approx(np.prod(1 + a) ** (12 / 36) - 1)
    book = tools.manager_peer_ranking(frame(store, "ret_best"), frame(store, "mgr_terms"))
    assert {r.strategy for r in book.rows} >= {"eq_ls", "macro", "cta"}
    assert "EV04" in book.skipped  # the liquidated manager has no recent record


def test_revision_bias_flags_marlow_only(store: SablecroftStore, answers: dict[str, Any]) -> None:
    result = tools.revision_bias(frame(store, "ret_est"), frame(store, "ret_final"))
    flagged = [r.manager_id for r in result.rows if r.biased]
    assert flagged == [answers["revisions"]["biased_manager"]] == ["EV02"]
    top = result.rows[0]
    planted = answers["revisions"]["managers"]["EV02"]
    assert top.t_stat == pytest.approx(planted["t_stat"], rel=1e-9)
    assert top.mean_revision == pytest.approx(planted["mean_revision"], rel=1e-9)


# ---- the platform contract, acceptance, and the platform's own analytics -----------------------

EXAMPLES: dict[str, dict[str, Any]] = {
    "sab_private_fund_returns": {
        "calls": {"dataset": "pm_calls", "column": "PM01"},
        "distributions": {"dataset": "pm_distributions", "column": "PM01"},
        "nav": {"dataset": "pm_nav", "column": "PM01"},
    },
    "sab_private_fund_pme": {
        "calls": {"dataset": "pm_calls", "column": "PM03"},
        "distributions": {"dataset": "pm_distributions", "column": "PM03"},
        "nav": {"dataset": "pm_nav", "column": "PM03"},
        "index": {"dataset": "idx_public", "column": "SCI_EU"},
    },
    "sab_unsmooth_returns": {"returns": {"dataset": "ret_final", "column": "IC01"}},
    "sab_lo_adjusted_sharpe": {"returns": {"dataset": "ret_final", "column": "IC01"}},
    "sab_liquidity_ladder": {"terms": {"dataset": "mgr_terms"}, "as_of": "2025-12-31"},
    "sab_manager_peer_ranking": {
        "returns": {"dataset": "ret_best"},
        "terms": {"dataset": "mgr_terms"},
    },
    "sab_revision_bias": {
        "estimates": {"dataset": "ret_est"},
        "finals": {"dataset": "ret_final"},
    },
}


def test_the_funds_app_honours_the_platform_contract(store: SablecroftStore) -> None:
    records = check_app(server.app, EXAMPLES, datasets=store)
    by_tool = {r.tool: r.output for r in records if r.output}
    assert by_tool["sab_private_fund_returns"]["irr"] == pytest.approx(0.2)
    assert by_tool["sab_private_fund_returns"]["tvpi"] == pytest.approx(1.32)
    # Sparse series (a cash flow on 3 of 1,000 days) draw a missing-values warning on every call.
    assert any("missing values" in w for w in by_tool["sab_private_fund_returns"]["warnings"])
    assert by_tool["sab_revision_bias"]["rows"][0]["manager_id"] == "EV02"


@pytest.fixture(scope="module")
def research() -> Any:
    app = open_research(":memory:")
    app.ingest.ingest(ROOT / "facility" / "research")
    yield app
    app.close()


def test_acceptance_cases_pass_as_the_cio(store: SablecroftStore, research: Any) -> None:
    gold, digest = load_gold(ROOT / "site" / "sablecroft_acceptance.toml")
    catalog = compose(
        policy=PermissionPolicy(),
        datasets=store,
        research=research.research,
        access=FileAccessPolicy(ACCESS),
        apps=[server.app],
    )
    report = run_acceptance(catalog, gold, digest)
    assert report.passed, report.markdown()
    assert report.total == 7


def test_acceptance_cannot_address_a_list_element_in_the_output(
    store: SablecroftStore, tmp_path: Path
) -> None:
    cases = tmp_path / "cases.toml"
    cases.write_text(
        '[[numeric]]\nname = "top revision t"\ntool = "sab_revision_bias"\n'
        'arguments = { estimates = { dataset = "ret_est" }, finals = { dataset = "ret_final" } }\n'
        'value = "rows.0.t_stat"\nexpected = 5.9\ntolerance = 0.1\n'
    )
    gold, digest = load_gold(cases)
    report = run_acceptance(platform_catalog(store), gold, digest)
    assert not report.passed
    assert "no number at rows.0.t_stat" in report.results[0].actual


def test_the_platforms_analytics_read_the_funds_monthly_data(store: SablecroftStore) -> None:
    catalog = platform_catalog(store)
    smoothed = run(catalog, None, "analyze_performance", EXAMPLES["sab_unsmooth_returns"])
    assert smoothed.status == "ok"
    out = smoothed.output
    assert out["periods_per_year"] == 12 and out["periods"] == 58
    assert "Left out 1 rows" in out["warnings"][0]  # December's final is not published yet
    # Evidence, not a bug: the platform reports the raw Sharpe of a smoothed manager as is.
    assert out["metrics"]["sharpe_ratio"] == pytest.approx(2.7137, abs=1e-3)
    assert out["metrics"]["probabilistic_sharpe_ratio"] > 0.999
    quarterly = run(
        catalog,
        None,
        "analyze_performance",
        {"returns": {"dataset": "ret_final_q", "column": "IC02"}},
    )
    assert quarterly.output["periods_per_year"] == 4 and quarterly.output["periods"] == 19
    # Estimates against finals: the same call on both vintages, side by side.
    est = run(
        catalog, None, "analyze_performance", {"returns": {"dataset": "ret_est", "column": "EV02"}}
    )
    fin = run(
        catalog,
        None,
        "analyze_performance",
        {"returns": {"dataset": "ret_final", "column": "EV02"}},
    )
    assert est.output["periods"] == 60 and fin.output["periods"] == 58
    # A manager's performance is by default over a different window than its estimate's: no tool
    # lines the vintages up.
    assert est.output["metrics"]["annualized_return"] < fin.output["metrics"]["annualized_return"]


def test_analyze_risk_on_the_fund_of_funds_loses_the_window_to_a_liquidated_manager(
    store: SablecroftStore,
) -> None:
    catalog = platform_catalog(store)
    mixed = run(
        catalog, None, "analyze_risk",
        {"portfolio_id": "fund", "asset_returns": {"dataset": "ret_usd"},
         "weights_ref": {"dataset": "fof_weights"}},
    )  # fmt: skip
    # The weights hold the three quarterly reporters, the returns frame cannot: refused, not
    # narrowed.
    assert mixed.status == "error" and "has no column 'IC02'" in mixed.error
    monthly = run(
        catalog, None, "analyze_risk",
        {"portfolio_id": "fund", "asset_returns": {"dataset": "ret_usd"},
         "weights_ref": {"dataset": "fof_weights_m"}},
    )  # fmt: skip
    assert monthly.status == "ok"
    # 60 months of returns, but EV04 stopped in 2024-06: 18 rows are dropped, the latest 18 months.
    assert monthly.output["periods"] == 42
    assert any("Left out 18 rows" in w for w in monthly.output["warnings"])
    assert monthly.output["volatility"] == pytest.approx(0.0340, abs=2e-3)
    assert monthly.output["effective_positions"] > 15


def test_signal_tools_run_on_manager_returns_but_mean_nothing_here(store: SablecroftStore) -> None:
    catalog = platform_catalog(store)
    study = run(
        catalog, None, "analyze_signal",
        {"signal": {"dataset": "ret_best"}, "returns": {"dataset": "ret_usd"}},
    )  # fmt: skip
    assert study.status == "ok"
    assert study.output["summary"]["instruments"] == 22  # a cross-section of 22 funds
    # Horizons default to 1, 5, 21 *periods*: on monthly data that is 21 months.
    assert [h["horizon"] for h in study.output["horizons"]] == [1, 5, 21]
    backtest = run(
        catalog, None, "run_backtest",
        {"signal": {"dataset": "ret_best"}, "returns": {"dataset": "ret_usd"}, "cost_bps": 10},
    )  # fmt: skip
    assert backtest.status == "ok" and backtest.output["summary"]["bars"] == 58


# ---- per-caller access ------------------------------------------------------------------------


def caller(*groups: str) -> Caller:
    return Caller("user@sablecroft.example", groups=frozenset(GROUP[g] for g in groups))


def test_entitlements_follow_the_teams_and_a_refusal_looks_like_a_missing_dataset(
    store: SablecroftStore,
) -> None:
    access = FileAccessPolicy(ACCESS)
    catalog = platform_catalog(store, access)
    mine = {"returns": {"dataset": "ret_final_eq_ls", "column": "EQ01"}}
    other = {"returns": {"dataset": "ret_final_macro", "column": "MC01"}}
    nothing = {"returns": {"dataset": "ret_final_nothing", "column": "MC01"}}
    analyst = caller("sg-research-eq-ls")
    assert run(catalog, analyst, "analyze_performance", mine).status == "ok"
    refused, missing = (run(catalog, analyst, "analyze_performance", a) for a in (other, nothing))
    assert refused.status == missing.status == "error"
    assert "No dataset 'ret_final_macro'" in refused.error
    assert refused.error.replace("ret_final_macro", "X") == missing.error.replace(
        "ret_final_nothing", "X"
    )
    assert refused.refused == ["dataset.read ret_final_macro"]  # the record still says why
    # What the analyst can list is exactly its strategy's data.
    listed = run(catalog, analyst, "list_datasets", {}).output
    ids = {d["id"] for d in listed["datasets"]} if "datasets" in listed else set()
    assert ids == {"ret_final_eq_ls", "ret_est_eq_ls", "fx_eurusd", "idx_public"}
    # Investor relations: the fund's aggregate numbers only, one tool of its own.
    relations = caller("sg-investor-relations")
    fof = {"returns": {"dataset": "fof_fund", "column": "monthly_return"}}
    assert run(catalog, relations, "analyze_performance", fof).status == "ok"
    assert run(catalog, relations, "analyze_performance", mine).status == "error"
    assert (
        run(catalog, relations, "sab_liquidity_ladder", EXAMPLES["sab_liquidity_ladder"]).status
        != "ok"
    )
    # Operations: NAV reconciliation and the revision check, no private-markets tools.
    operations = caller("sg-operations")
    assert (
        run(catalog, operations, "sab_revision_bias", EXAMPLES["sab_revision_bias"]).status == "ok"
    )
    assert run(catalog, operations, "analyze_performance", mine).status != "ok"
    # Private markets reach commitments and cash flows, and not a hedge fund's returns.
    private = caller("sg-private-markets")
    assert (
        run(
            catalog, private, "sab_private_fund_returns", EXAMPLES["sab_private_fund_returns"]
        ).status
        == "ok"
    )
    assert run(catalog, private, "analyze_performance", mine).status == "error"
    # Fail closed: no groups (an Entra app token has none), or an unknown one.
    assert run(catalog, caller(), "analyze_performance", mine).status != "ok"
    assert (
        run(
            catalog, Caller("x", groups=frozenset({"not-a-guid"})), "analyze_performance", mine
        ).status
        != "ok"
    )
    assert run(catalog, None, "analyze_performance", mine).status != "ok"


def test_a_strategy_entitlement_cannot_be_a_column_filter(store: SablecroftStore) -> None:
    """Why the store serves a dataset per strategy: access is by dataset id, not by column."""
    assert {"ret_final_eq_ls", "ret_final_macro"} <= set(store.ids())
    assert set(store.get("ret_final_eq_ls").columns) == {"EQ01", "EQ02", "EQ03", "EQ04"}
    # The entitlement for one strategy does not cover the all-managers dataset that holds the
    # others.
    rules = ACCESS.read_text()
    assert '"*_eq_ls"' in rules and '"ret_final"' not in rules.split("sg-research-eq-ls")[1][:400]


# ---- research and the injection --------------------------------------------------------------


def test_the_research_folder_holds_the_documents_and_the_injection_stays_inert(
    research: Any, store: SablecroftStore
) -> None:
    documents = sorted((ROOT / "facility" / "research").glob("*.md"))
    assert 6 <= len(documents) <= 8
    before = ToolRegistry(server.build_catalog(store, PermissionPolicy()).tools).digest()
    packet = research.research.find_related(
        ResearchQuery(query="IMPORTANT SYSTEM NOTICE maintenance mode")
    )
    carrying = [p for p in packet.passages if "ignore previous instructions" in p.text.lower()]
    assert carrying and {p.source.source_id for p in carrying} == {"note-administrator-update.md"}
    assert all(item.kind == "retrieved" for item in packet.evidence)
    after = ToolRegistry(server.build_catalog(store, PermissionPolicy()).tools).digest()
    assert before == after  # reading hostile text changed no tool
    smoothed = research.research.find_related(
        ResearchQuery(query="Getmansky Lo Makarov weights true volatility")
    )
    assert smoothed.passages[0].source.source_id == "note-smoothed-returns.md"


# ---- Entra ID, and the platform's sign-in -----------------------------------------------------


def verifier_for(
    issuer: sablecroft_entra_mock.EntraIssuer, introspection_url: str
) -> IntrospectionVerifier:
    server_entry = AuthorizationServer(
        issuer=issuer.issuer,
        introspection_url=introspection_url,
        client_id="qaos",
        client_secret=SecretStr("shim-secret"),
        tenant_claim="",
        groups_claim="groups",
    )
    return IntrospectionVerifier(
        [server_entry], resource=RESOURCE, tenants_by_issuer={issuer.issuer: {"local"}}
    )


def test_the_mock_issuer_has_entras_shapes_and_no_introspection_endpoint() -> None:
    import urllib.error
    import urllib.request

    with sablecroft_entra_mock.serve() as issuer:
        token = issuer.user_token("dev.shah@sablecroft.example", audience=RESOURCE)
        header = json.loads(_b64(token.split(".")[0]))
        claims = json.loads(_b64(token.split(".")[1]))
        assert header == {"typ": "JWT", "alg": "RS256", "kid": sablecroft_entra_mock.KID}
        assert claims["iss"] == f"{issuer.base_url}/{sablecroft_entra_mock.TENANT_ID}/v2.0"
        assert claims["tid"] == sablecroft_entra_mock.TENANT_ID and claims["ver"] == "2.0"
        assert claims["groups"] == [GROUP["sg-research-eq-ls"]]  # GUIDs, never names
        assert claims["scp"] == "tools.list tools.call" and "roles" not in claims
        app = json.loads(_b64(issuer.app_token().split(".")[1]))
        assert app["roles"] == ["Tools.Call"] and "scp" not in app and "groups" not in app
        keys = f"{issuer.base_url}/{sablecroft_entra_mock.TENANT_ID}/discovery/v2.0/keys"
        with urllib.request.urlopen(keys) as response:
            assert json.load(response)["keys"][0]["kid"] == sablecroft_entra_mock.KID
        introspect = f"{issuer.base_url}/{sablecroft_entra_mock.TENANT_ID}/oauth2/v2.0/introspect"
        request = urllib.request.Request(introspect, data=b"token=x", method="POST")
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(request)
        assert refused.value.code == 404
        refused.value.close()


def _b64(part: str) -> bytes:
    import base64

    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def test_the_platforms_sign_in_refuses_every_entra_token_without_introspection() -> None:
    with sablecroft_entra_mock.serve() as issuer:
        url = f"{issuer.base_url}/{sablecroft_entra_mock.TENANT_ID}/oauth2/v2.0/introspect"
        token = issuer.user_token("dev.shah@sablecroft.example", audience=RESOURCE)
        assert anyio.run(verifier_for(issuer, url).verify_token, token) is None


def test_with_a_validating_sidecar_the_platform_accepts_entra_tokens_and_reads_guid_groups(
    store: SablecroftStore,
) -> None:
    with sablecroft_entra_mock.serve() as issuer:
        jwks = f"{issuer.base_url}/{sablecroft_entra_mock.TENANT_ID}/discovery/v2.0/keys"
        validator = EntraValidator(issuer.issuer, jwks, RESOURCE)
        with serve_introspection(validator, "qaos", "shim-secret") as url:
            verifier = verifier_for(issuer, url)
            token = issuer.user_token("dev.shah@sablecroft.example", audience=RESOURCE)
            access = anyio.run(verifier.verify_token, token)
            assert access is not None
            assert access.subject == sablecroft_entra_mock.USERS["dev.shah@sablecroft.example"][0]
            assert set(access.scopes) == {"tools:list", "tools:call"}  # dots mapped to colons
            assert access.claims is not None
            groups = access.claims[GROUPS_CLAIM]
            assert groups == [GROUP["sg-research-eq-ls"]]
            # The platform's own access file, keyed by those GUIDs, then decides.
            catalog = platform_catalog(store, FileAccessPolicy(ACCESS))
            who = Caller(str(access.subject), groups=frozenset(groups))
            mine = {"returns": {"dataset": "ret_final_eq_ls", "column": "EQ01"}}
            assert run(catalog, who, "analyze_performance", mine).status == "ok"
            # Refused: another audience, an expired token, a bad signature, an app token.
            other = issuer.user_token(
                "dev.shah@sablecroft.example", audience="api://sablecroft-qaos"
            )
            expired = issuer.user_token(
                "dev.shah@sablecroft.example", audience=RESOURCE, lifetime=-10
            )
            forged = token[:-6] + ("AAAAAA" if not token.endswith("AAAAAA") else "BBBBBB")
            for bad in (other, expired, forged):
                assert anyio.run(verifier.verify_token, bad) is None
            app_access = anyio.run(verifier.verify_token, issuer.app_token(audience=RESOURCE))
            assert app_access is not None and app_access.claims is not None
            assert app_access.claims[GROUPS_CLAIM] == [] and app_access.scopes == []
            assert (
                run(catalog, Caller("app", groups=frozenset()), "analyze_performance", mine).status
                != "ok"
            )


def test_the_sign_in_file_for_azure_loads_but_names_what_entra_cannot_do() -> None:
    entry = load_sign_in_file(ROOT / "site" / "deploy" / "sign-in.toml")
    assert entry.resource == RESOURCE
    server_entry = entry.authorization_servers[0]
    assert server_entry.issuer.startswith("https://login.microsoftonline.com/")
    assert server_entry.issuer.endswith("/v2.0")  # a v2.0 issuer: also what the token's iss holds
    # The file's required introspection_url is the sidecar's, on loopback; Entra has no such URL.
    assert server_entry.introspection_url.startswith("http://127.0.0.1")
    assert entry.missing_secrets({}) == ["QAOS_INTROSPECTION_SECRET"]  # a Key Vault ref as env var


def test_the_run_store_setting_cannot_name_a_managed_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from quant_agent_os.config import get_settings

    monkeypatch.setenv(
        "QUANT_AGENT_OS_RUNS_FILE",
        "postgresql://qaos@sablecroft-pg.postgres.database.azure.com/qaos",
    )
    monkeypatch.setenv(
        "QUANT_AGENT_OS_STORE",
        "postgresql://qaos_reader@sablecroft-pg.postgres.database.azure.com/qaos?sslmode=require",
    )
    get_settings.cache_clear()
    try:
        settings = get_settings()
    finally:
        get_settings.cache_clear()
    assert isinstance(settings.runs_file, Path)  # a filesystem path: the run store is a file
    assert settings.runs_file.as_posix().startswith("postgresql:")  # mangled into a relative path
    assert "sslmode=require" in settings.store  # the knowledge store takes a URL


# ---- the legacy package and the facility's boundary -------------------------------------------


def test_the_legacy_package_runs_as_the_analysts_use_it(lake: Path, store: SablecroftStore) -> None:
    import sablecroft_alts as legacy
    from sablecroft_alts import monitor

    legacy.CONFIG.update(lake=str(lake / "uc" / "sablecroft"), verbose=False, refresh_delay_s=0.0)
    flows = pd.DataFrame(
        {"date": ["2021-01-01", "2022-01-01", "2023-01-01"], "amount": [-100.0, 60.0, 72.0]}
    )
    assert legacy.xirr(flows) == pytest.approx(0.2, abs=1e-9)
    assert legacy.fund_multiples([100.0], [132.0], 0.0)["tvpi"] == pytest.approx(1.32)
    wide = legacy.returns.load_returns("final")
    assert wide.shape[1] == 22
    assert wide["IC01"].dropna().shape[0] == 58
    assert monitor.refresh() == 63  # the December, November, and October statements' rows
    status = monitor.latest_status("EV01")
    assert status is not None and status["status"] == "estimate"  # a 45-day manager, 2 months late
    assert "EV01" in monitor.stale()
    # The legacy ladder reads the wall clock when no date is given: its answer changes by the day.
    book = frame(store, "mgr_terms").assign(investment_date="2021-01-01")
    ladder = legacy.liquidity_ladder(book, as_of=date(2025, 12, 31))
    assert sum(ladder.values()) == pytest.approx(book["nav_usd"].sum())


def test_the_facility_imports_nothing_of_the_platform() -> None:
    for path in (ROOT / "facility").rglob("*.py"):
        assert not PLATFORM_IMPORT.search(path.read_text()), path
