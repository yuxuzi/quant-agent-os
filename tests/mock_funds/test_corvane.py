"""examples/mock-funds/corvane: a long-only equity manager's estate, tools, and sign-in, offline."""

import asyncio
import base64
import json
import math
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from quant_agent_os.data import UnknownDatasetError
from quant_agent_os.mcp.http import GROUPS_CLAIM
from quant_agent_os.runtime import ToolCall, ToolExecutor
from quant_agent_os.tools import Caller, FileAccessPolicy, PermissionPolicy, ToolRegistry
from quant_agent_os.tools.access import acting_as

ROOT = Path(__file__).parents[2] / "examples" / "mock-funds" / "corvane"
for sub in ("facility/warehouse", "facility/gateway", "site"):
    if str(ROOT / sub) not in sys.path:
        sys.path.insert(0, str(ROOT / sub))

import cognito_idp  # noqa: E402
import corvane_introspect  # noqa: E402
import corvane_server  # noqa: E402
import corvane_tools  # noqa: E402
from build_corvane import DEFAULT_SEED, FACTORS, build  # noqa: E402
from corvane_store import CorvaneDatasetStore, connect, pivot  # noqa: E402

from quant_agent_os.testing import check_app  # noqa: E402
from quant_platform.acceptance import load_gold, run_acceptance  # noqa: E402
from quant_platform.identity.sign_in import load_sign_in_file  # noqa: E402
from quant_research import open_research  # noqa: E402

pytest.importorskip("empyrical")
PLATFORM = re.compile(r"^\s*(from|import)\s+(quant_agent_os|quant_platform|quant_research)\b", re.M)
LCC, GQ = {"dataset": "weights_lcc"}, {"dataset": "weights_gq"}
US, GLOBAL = {"dataset": "bench_w_bm_us300"}, {"dataset": "bench_w_bm_global200"}
RET = {"dataset": "px_ret_usd"}
SECTOR = {"dataset": "ref_security", "column": "sector_code"}


@pytest.fixture(scope="module")
def estate(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("corvane") / "estate"
    build(path)
    return path


@pytest.fixture(scope="module")
def known(estate: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((estate / "known_answers.json").read_text())
    return loaded


@pytest.fixture(scope="module")
def store(estate: Path) -> CorvaneDatasetStore:
    return CorvaneDatasetStore(connect({"CORVANE_SQL_URL": f"estate://{estate}"}))


@pytest.fixture(scope="module")
def research_service(estate: Path) -> Iterator[Any]:
    with open_research(":memory:") as app:
        app.ingest.ingest(estate / "s3" / "corvane-research" / "research")
        yield app


def _run(catalog: Any, tool: str, caller: Caller | None = None, **arguments: Any) -> Any:
    executor = ToolExecutor(ToolRegistry(catalog.tools), catalog.policy)
    if caller is None:
        return executor.execute(ToolCall(tool=tool, arguments=arguments))
    with acting_as(caller):
        return executor.execute(ToolCall(tool=tool, arguments=arguments))


@pytest.fixture(scope="module")
def catalog(store: CorvaneDatasetStore) -> Any:
    return corvane_server.build_catalog(store, PermissionPolicy())


# ---- the estate ------------------------------------------------------------------------------


def test_the_estate_is_deterministic_and_small(tmp_path: Path, estate: Path) -> None:
    build(tmp_path / "again")
    build(tmp_path / "other", seed=1)
    same = (estate / "known_answers.json").read_text()
    assert (tmp_path / "again" / "known_answers.json").read_text() == same
    assert (tmp_path / "other" / "known_answers.json").read_text() != same
    size = sum(p.stat().st_size for p in estate.rglob("*") if p.is_file())
    assert size < 3_000_000


def test_the_lake_has_real_key_layouts_and_a_catalog(estate: Path) -> None:
    mapping = json.loads((estate / "s3_map.json").read_text())
    assert mapping["s3://corvane-curated"] == "s3/corvane-curated"
    curated = estate / mapping["s3://corvane-curated"] / "curated"
    assert (curated / "prices_daily" / "dt=2026-05-15" / "part-0000.parquet").is_file()
    assert (curated / "factor_scores" / "dt=2026-01-05" / "part-0000.parquet").is_file()
    raw = estate / mapping["s3://corvane-raw"] / "raw"
    assert len(list((raw / "vendor-benchmark").glob("constituents_*.csv"))) == 6
    first = (raw / "vendor-benchmark" / "constituents_20260102.csv").read_text().splitlines()[1]
    assert float(first.split(",")[2]) > 0.0001  # weights land in percent, and curate to fractions
    catalog = json.loads((estate / "catalog" / "glue_catalog.json").read_text())
    tables = {t["Name"]: t for t in catalog["TableList"]}
    assert set(tables) == {"prices_daily", "fx_daily", "factor_scores"}
    assert tables["prices_daily"]["PartitionKeys"] == [{"Name": "dt", "Type": "string"}]


def test_planted_truths_are_consistent(known: dict[str, Any]) -> None:
    assert known["seed"] == DEFAULT_SEED
    assert known["largest_selection_sector"] == "Industrials"
    assert known["rank_ic"]["quality"] > 0.03
    assert max(abs(known["rank_ic"][f]) for f in FACTORS if f != "quality") < 0.015
    assert [b["date"] for b in known["breaches"]["SMA_FND_B"]] == [
        "2026-03-12",
        "2026-03-13",
        "2026-03-16",
        "2026-03-17",
    ]
    assert known["breaches"]["SMA_PEN_A"] == [] and known["breaches"]["LCC"] == []
    assert known["largest_linked_effect"]["sector"] == "Industrials"


# ---- the store ---------------------------------------------------------------------------------


def test_the_store_serves_wide_datasets_from_snowflake_sql_and_the_lake(
    store: CorvaneDatasetStore,
) -> None:
    ids = store.ids()
    assert {"px_ret_usd", "weights_lcc", "weights_sma_fnd_b", "bench_w_bm_global200"} <= set(ids)
    assert {f"scores_{f}" for f in FACTORS} <= set(ids)
    returns = store.get("px_ret_usd")
    assert returns.periods_per_year == 252 and "CASH_USD" in returns.columns
    local, usd = store.get("px_ret_local"), store.get("px_ret_usd")
    # JP was closed on 2026-02-11: no local return that day, and the dollar return is currency only.
    assert local.index is not None and usd.index is not None
    day = "2026-02-11"
    assert math.isnan(local.column("JP0001")[local.index.index(day)])
    fx = store.get("fx_ret")
    assert fx.index is not None
    assert usd.column("JP0001")[usd.index.index(day)] == pytest.approx(
        fx.column("JPY")[fx.index.index(day)]
    )
    close = store.get("px_close_local")
    assert close.info().missing_cells > 0  # holidays, listings, and the delisting are NaN
    with pytest.raises(UnknownDatasetError, match="No dataset 'nope'"):
        store.get("nope")


def test_the_serving_views_reconcile_with_the_vendor_benchmark(store: CorvaneDatasetStore) -> None:
    weights, returns = store.get("bench_w_bm_us300"), store.get("px_ret_usd")
    bench = store.get("bench_ret").column("BM_US300")
    worst = 0.0
    assert weights.index and returns.index
    for j in range(1, len(weights.index)):
        i = returns.index.index(weights.index[j])
        total = sum(
            weights.columns[c][j - 1]
            * (returns.columns[c][i] if returns.columns[c][i] == returns.columns[c][i] else 0.0)
            for c in weights.columns
            if c in returns.columns
        )
        worst = max(worst, abs(total - bench[store.get("bench_ret").index.index(weights.index[j])]))  # type: ignore[union-attr]
    assert worst < 1e-12


def test_a_split_is_value_neutral_in_the_holdings(
    store: CorvaneDatasetStore, known: dict[str, Any]
) -> None:
    close = store.get("px_close_local").column(known["plan"]["split"])
    assert store.get("px_close_local").index is not None
    index = store.get("px_close_local").index
    assert index is not None
    before, after = close[index.index("2026-02-17")], close[index.index("2026-02-18")]
    assert after < 0.7 * before  # the raw close halved
    weights = store.get("weights_lcc").column(known["plan"]["split"])
    wi = store.get("weights_lcc").index
    assert wi is not None
    assert abs(weights[wi.index("2026-02-18")] / weights[wi.index("2026-02-17")] - 1) < 0.15


def test_pivot_keeps_missing_cells_missing_and_fills_weights() -> None:
    rows = [("2026-01-02", "A", 1.0), ("2026-01-05", "B", 2.0)]
    assert pivot("date", rows, None).splitlines() == [
        "date,A,B",
        "2026-01-02,1.0,NaN",
        "2026-01-05,NaN,2.0",
    ]
    assert pivot("date", rows, 0.0).splitlines()[1] == "2026-01-02,1.0,0.0"


# ---- the fund's tools, against the planted truths ----------------------------------------------


def test_the_fund_tools_give_the_planted_answers(
    store: CorvaneDatasetStore, known: dict[str, Any]
) -> None:
    perf = {"dataset": "perf_lcc", "column": "gross"}
    examples: dict[str, dict[str, Any]] = {
        "corvane_active_share": {"portfolio": LCC, "benchmark": US, "as_of": "2026-05-15"},
        "corvane_tracking_error": {
            "portfolio_returns": perf,
            "benchmark_returns": {"dataset": "bench_ret", "column": "BM_US300"},
            "start": "2026-01-03",
            "end": "2026-05-15",
        },
        "corvane_tracking_error_ex_ante": {
            "portfolio": LCC, "benchmark": US, "returns": RET, "as_of": "2026-05-15",
        },
        "corvane_attribution_single": {
            "portfolio": LCC, "benchmark": US, "returns": RET, "sector_code": SECTOR,
            "start": "2026-05-01", "end": "2026-06-01",
        },
        "corvane_attribution_linked": {
            "portfolio": LCC, "benchmark": US, "returns": RET, "sector_code": SECTOR,
            "start": "2026-01-02", "end": "2026-06-01",
        },
        "corvane_currency_effect": {
            "portfolio": GQ, "benchmark": GLOBAL, "returns_usd": RET,
            "returns_local": {"dataset": "px_ret_local"}, "fx_returns": {"dataset": "fx_ret"},
            "currency_code": {"dataset": "ref_security", "column": "currency_code"},
            "start": "2026-05-01", "end": "2026-06-01",
        },
        "corvane_days_to_liquidate": {
            "portfolio": LCC, "adv_usd": {"dataset": "adv_usd"}, "as_of": "2026-05-15",
            "aum_usd": 1.5e9,
        },
        "corvane_mandate_check": {
            "portfolio": {"dataset": "weights_sma_fnd_b"},
            "excluded": {"dataset": "mandate_flags_sma_fnd_b", "column": "excluded"},
            "start": "2026-01-02", "end": "2026-06-05",
        },
    }  # fmt: skip
    records = {
        r.tool: r.output
        for r in check_app(corvane_server.build_app(), examples, datasets=store)
        if r.output is not None
    }
    assert len(records) == 8
    o = records["corvane_active_share"]
    assert o["active_share"] == pytest.approx(known["active_share"]["LCC"], abs=1e-12)
    assert o["convention"].startswith("0.5 * sum")
    assert o["data_versions"]["dataset:weights_lcc"] == store.get("weights_lcc").fingerprint
    assert records["corvane_tracking_error"]["tracking_error"] == pytest.approx(
        known["tracking_error_ex_post"]["LCC"], abs=1e-12
    )
    assert records["corvane_tracking_error_ex_ante"]["tracking_error"] == pytest.approx(
        known["tracking_error_ex_ante_60d"]["LCC"], abs=1e-12
    )
    may = known["attribution_lcc_may"]
    single = records["corvane_attribution_single"]
    assert single["portfolio_return"] == pytest.approx(may["portfolio_return"], abs=1e-12)
    industrials = next(s for s in single["sectors"] if s["sector"] == "Industrials")
    assert industrials["allocation"] == pytest.approx(
        may["effects"]["Industrials"]["allocation"], abs=1e-12
    )
    assert single["allocation"] + single["selection"] + single["interaction"] == pytest.approx(
        single["active_return"], abs=1e-12
    )
    linked = records["corvane_attribution_linked"]
    expected = known["attribution_lcc_linked_monthly"]
    assert len(linked["periods"]) == 5
    assert linked["portfolio_return"] == pytest.approx(expected["portfolio_return"], abs=1e-12)
    total = linked["allocation"] + linked["selection"] + linked["interaction"]
    assert total == pytest.approx(
        linked["portfolio_return"] - linked["benchmark_return"], abs=1e-12
    )
    best = max(linked["sectors"], key=lambda s: s["selection"])
    assert best["sector"] == known["largest_selection_sector"] == "Industrials"
    currency = records["corvane_currency_effect"]
    assert currency["currency_allocation"] == pytest.approx(
        known["currency_gq_may"]["currency_allocation"], abs=1e-12
    )
    assert currency["active_return_usd"] == pytest.approx(
        currency["active_return_local"] + currency["currency_allocation"] + currency["residual"]
    )
    liquidity = records["corvane_days_to_liquidate"]
    assert liquidity["max_days"] == pytest.approx(
        known["days_to_liquidate_lcc"]["max_days"], rel=1e-9
    )
    assert records["corvane_mandate_check"]["breach_days"] == [
        b["date"] for b in known["breaches"]["SMA_FND_B"]
    ]


def test_brinson_fachler_on_a_two_sector_example() -> None:
    import pandas as pd

    days = pd.to_datetime(["2026-01-02", "2026-01-05"])
    wp = pd.DataFrame({"A": [0.6, 0.6], "B": [0.4, 0.4]}, index=days)
    wb = pd.DataFrame({"A": [0.5, 0.5], "B": [0.5, 0.5]}, index=days)
    returns = pd.DataFrame({"A": [0.0, 0.10], "B": [0.0, 0.02]}, index=days)
    code = pd.Series({"A": 5, "B": 6})
    out = corvane_tools.attribution_single(wp, wb, returns, code, "2026-01-02", "2026-01-05")
    # Rp = 0.6*0.10 + 0.4*0.02 = 0.068; Rb = 0.06; A: alloc 0.1*(0.10-0.06)=0.004, sel 0, int 0.
    assert out.portfolio_return == pytest.approx(0.068)
    assert out.benchmark_return == pytest.approx(0.06)
    energy = next(s for s in out.sectors if s.sector == "Energy")
    assert energy.allocation == pytest.approx(0.004) and energy.selection == pytest.approx(0.0)
    financials = next(s for s in out.sectors if s.sector == "Financials")
    assert financials.allocation == pytest.approx(-0.1 * (0.02 - 0.06))
    assert out.active_return == pytest.approx(out.allocation + out.selection + out.interaction)


def test_a_tool_refuses_a_date_that_is_not_a_row(catalog: Any) -> None:
    record = _run(catalog, "corvane_active_share", portfolio=LCC, benchmark=US, as_of="2026-05-16")
    assert record.status == "error"
    assert "is not a row of portfolio" in record.error and "2026-05-15" in record.error


def test_the_mandate_cap_check_finds_the_caps_breaches(catalog: Any, known: dict[str, Any]) -> None:
    record = _run(
        catalog, "corvane_mandate_check",
        portfolio={"dataset": "weights_sma_swf_c"},
        excluded={"dataset": "mandate_flags_sma_swf_c", "column": "excluded"},
        start="2026-01-02", end="2026-06-05", max_position=0.05,
    )  # fmt: skip
    expected = known["breaches"]["SMA_SWF_C"]
    got = [(b["date"], b["security_id"], b["kind"]) for b in record.output["breaches"]]
    assert got == [(b["date"], b["security_id"], b["kind"]) for b in expected]


# ---- the platform's own tools on this fund's data ----------------------------------------------


def test_the_platforms_analytics_on_the_funds_data(catalog: Any, known: dict[str, Any]) -> None:
    perf = _run(
        catalog, "analyze_performance",
        returns={"dataset": "perf_lcc", "column": "gross"},
        benchmark={"dataset": "bench_ret", "column": "BM_US300"},
    )  # fmt: skip
    metrics = perf.output["metrics"]
    for name in ("information_ratio", "tracking_error", "beta", "up_capture", "down_capture"):
        assert name in metrics
    assert perf.output["periods"] == 109 and metrics["beta"] == pytest.approx(1.0, abs=0.02)

    signal = _run(
        catalog, "analyze_signal", signal={"dataset": "scores_quality"}, returns=RET, lag=0
    )
    ic = signal.output["summary"]["mean_rank_ic"]
    assert ic == pytest.approx(known["rank_ic"]["quality"], abs=5e-4)
    assert any("treated as delisted and earns -30%" in w for w in signal.output["warnings"])
    assert any("CASH_USD" in w for w in signal.output["warnings"])
    weak = _run(catalog, "analyze_signal", signal={"dataset": "scores_value"}, returns=RET, lag=0)
    assert abs(weak.output["summary"]["mean_rank_ic"]) < 0.02

    risk = _run(
        catalog, "analyze_risk", portfolio_id="lcc",
        weights_ref={"dataset": "weights_lcc", "as_of": "2026-05-15"}, asset_returns=RET,
    )  # fmt: skip
    assert (
        risk.status == "ok" and risk.output["periods"] == 109
    )  # the late names are left out instead
    assert any("Left out 2 assets" in w and "US0301" in w for w in risk.output["warnings"])
    assert any("US0301" in w for w in risk.output["warnings"])

    backtest = _run(
        catalog, "run_backtest", signal={"dataset": "scores_quality"}, returns=RET, cost_bps=10,
        weighting="long_only", rebalance_every=21, lag=1,
        benchmark={"dataset": "bench_ret", "column": "BM_US300"},
    )  # fmt: skip
    assert backtest.output["costs"]["cost_drag"] > 0
    assert backtest.output["book"]["weighting"] == "long_only"
    assert "up_capture" in backtest.output["benchmark"]


# ---- access and acceptance ---------------------------------------------------------------------


def test_per_caller_access_refusals_look_like_missing_datasets(store: CorvaneDatasetStore) -> None:
    access = FileAccessPolicy(ROOT / "facility" / "access.toml")
    catalog = corvane_server.build_catalog(store, PermissionPolicy(), access=access)
    service = Caller("claire", "default", frozenset({"client-service-aldergate"}))
    own = _run(
        catalog, "analyze_performance", service,
        returns={"dataset": "perf_sma_pen_a", "column": "gross"},
        benchmark={"dataset": "bench_ret", "column": "BM_US300"},
    )  # fmt: skip
    other = _run(
        catalog, "analyze_performance", service,
        returns={"dataset": "perf_sma_fnd_b", "column": "gross"},
    )  # fmt: skip
    holdings = _run(
        catalog, "corvane_active_share", service, portfolio=LCC, benchmark=US, as_of="2026-05-15"
    )
    assert own.status == "ok"
    assert other.status == "error" and "No dataset 'perf_sma_fnd_b'" in other.error
    assert holdings.status in ("denied", "error")
    listing = _run(catalog, "list_datasets", service)
    assert {d["id"] for d in listing.output["datasets"]} == {"perf_sma_pen_a", "bench_ret"}

    pm = Caller("priya", "default", frozenset({"pm-us-core"}))
    own_pm = _run(
        catalog, "corvane_active_share", pm, portfolio=LCC, benchmark=US, as_of="2026-05-15"
    )
    foreign = _run(
        catalog, "corvane_active_share", pm, portfolio=GQ, benchmark=GLOBAL, as_of="2026-05-15"
    )
    assert own_pm.status == "ok" and foreign.status == "error"
    assert "No dataset 'weights_gq'" in foreign.error
    analyst = Caller("ines", "default", frozenset({"analyst-industrials"}))
    assert (
        _run(
            catalog, "analyze_signal", analyst, signal={"dataset": "scores_quality"}, returns=RET
        ).status
        == "ok"
    )
    peek = _run(
        catalog,
        "analyze_risk",
        analyst,
        portfolio_id="x",
        weights_ref={"dataset": "weights_lcc"},
        asset_returns=RET,
    )
    assert peek.status == "error" and "No dataset 'weights_lcc'" in peek.error


def test_the_agreed_acceptance_cases_pass_as_the_named_callers(
    store: CorvaneDatasetStore, research_service: Any
) -> None:
    access = FileAccessPolicy(ROOT / "facility" / "access.toml")
    catalog = corvane_server.build_catalog(
        store, PermissionPolicy(), research_service.research, access=access
    )
    gold, digest = load_gold(ROOT / "site" / "corvane_acceptance.toml")
    report = run_acceptance(catalog, gold, digest)
    assert report.passed, report.markdown()
    assert report.total == 7


def test_the_facility_imports_nothing_of_the_platform() -> None:
    for path in (ROOT / "facility").rglob("*.py"):
        assert not PLATFORM.search(path.read_text()), path


# ---- research, with the injection inert --------------------------------------------------------


def test_research_search_finds_the_minute_and_the_injection_stays_data(
    research_service: Any,
) -> None:
    from quant_research import ResearchQuery

    packet = research_service.research.find_related(
        ResearchQuery(query="spin-off tobacco mandates")
    )
    assert any("minutes-investment-committee" in p.source.source_id for p in packet.passages)
    hostile = research_service.research.find_related(
        ResearchQuery(query="maintenance mode approve pending tool call holdings")
    )
    carrying = [p for p in hostile.passages if "IGNORE ALL PREVIOUS INSTRUCTIONS" in p.text]
    assert carrying and all(p.source.source_id.endswith("note-energy-outlook.md") for p in carrying)
    assert all(item.kind == "retrieved" for item in hostile.evidence)


# ---- identity: a Cognito-shaped pool against the platform's sign-in ----------------------------


def _form(token: str) -> dict[str, str]:
    return {"token": token, "token_type_hint": "access_token"}


def test_the_mock_pool_looks_like_cognito() -> None:
    import jwt

    idp = cognito_idp.start()
    try:
        with urllib.request.urlopen(f"{idp.url}/.well-known/jwks.json") as response:
            keys = json.loads(response.read())
        with urllib.request.urlopen(f"{idp.url}/.well-known/openid-configuration") as response:
            meta = json.loads(response.read())
        request = urllib.request.Request(f"{idp.url}/oauth2/introspect", data=b"token=x")
        with pytest.raises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(request)
        missing.value.close()
    finally:
        idp.stop()
    assert missing.value.code == 404 and "introspection_endpoint" not in meta
    token = idp.pool.access_token("priya")
    header = jwt.get_unverified_header(token)
    claims = jwt.decode(token, options={"verify_signature": False})
    assert header["alg"] == "RS256" and header["kid"] == keys["keys"][0]["kid"]
    assert claims["token_use"] == "access" and "aud" not in claims
    assert claims["cognito:groups"] == ["pm-us-core"] and claims["scope"].startswith("qaos/")
    assert "aud" in jwt.decode(idp.pool.id_token("priya"), options={"verify_signature": False})
    assert "cognito:groups" not in jwt.decode(
        idp.pool.machine_token(), options={"verify_signature": False}
    )


def test_the_platforms_sign_in_cannot_use_cognito_directly_but_can_through_the_shim() -> None:
    from quant_platform.identity.oauth import AuthorizationServer, IntrospectionVerifier

    pool = cognito_idp.CognitoPool()
    resource = "https://qaos.corvane.example.com/mcp"
    secret = ("qaos-resource-server", "s3cret")
    "Basic " + base64.b64encode(b"qaos-resource-server:s3cret").decode()

    def shim(request: httpx.Request) -> httpx.Response:
        form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
        status, body = corvane_introspect.introspect(
            {"token": form["token"]}, request.headers.get("authorization"),
            issuer=pool.issuer, resource=resource, client_ids=frozenset({pool.client_id}),
            fetch_jwks=pool.jwks, basic=secret,
        )  # fmt: skip
        return httpx.Response(status, json=body)

    def server(url: str) -> AuthorizationServer:
        return AuthorizationServer.model_validate(
            {
                "issuer": pool.issuer, "introspection_url": url, "client_id": secret[0],
                "client_secret": secret[1], "tenant_claim": "", "groups_claim": "cognito:groups",
            }
        )  # fmt: skip

    def verify(url: str, handler: Any, token: str) -> Any:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        verifier = IntrospectionVerifier(
            [server(url)],
            resource=resource,
            tenants_by_issuer={pool.issuer: {"corvane"}},
            http=client,
        )
        return asyncio.run(verifier.verify_token(token))

    def cognito_404(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    token = pool.access_token("priya")
    assert verify(f"{pool.issuer}/oauth2/introspect", cognito_404, token) is None  # no endpoint
    access = verify("https://introspect.corvane.example.com/oauth2/introspect", shim, token)
    assert access is not None
    assert access.claims[GROUPS_CLAIM] == ["pm-us-core"]
    assert set(access.scopes) == {"tools:list", "tools:call"}
    assert access.claims["iss"] == pool.issuer
    # An ID token, a forged signature, and an expired token are all inactive.
    assert verify("https://i.example.com/x", shim, pool.id_token("priya")) is None
    other = cognito_idp.CognitoPool()
    assert verify("https://i.example.com/x", shim, other.access_token("priya")) is None
    late = cognito_idp.CognitoPool(clock=lambda: 1_000.0)
    assert verify("https://i.example.com/x", shim, late.access_token("priya")) is None
    # The machine token has no groups: it signs in as a caller with none, so per-caller access
    # refuses all of it.
    machine = verify("https://i.example.com/x", shim, pool.machine_token())
    assert machine is not None and machine.claims[GROUPS_CLAIM] == []


def test_the_sign_in_file_names_cognito_claims_and_a_secret_reference() -> None:
    sign_in = load_sign_in_file(ROOT / "site" / "deploy" / "sign-in.toml")
    entry = sign_in.authorization_servers[0]
    assert entry.groups_claim == "cognito:groups" and entry.tenant_claim == ""
    assert sign_in.missing_secrets({}) == ["CORVANE_INTROSPECTION_SECRET"]
    resolved = sign_in.resolve({"CORVANE_INTROSPECTION_SECRET": "x"})
    assert resolved.issuers == [cognito_idp.ISSUER]
    groups = {
        g.strip()
        for g in re.findall(
            r"\[groups\.([\w-]+)\]", (ROOT / "facility" / "access.toml").read_text()
        )
    }
    assert {u for gs in cognito_idp.USERS.values() for u in gs} <= groups


# ---- deployment descriptors (never run) --------------------------------------------------------


def test_the_deployment_descriptors_agree_with_the_code() -> None:
    deploy = ROOT / "site" / "deploy"
    task = json.loads((deploy / "ecs-task-definition.json").read_text())
    service = json.loads((deploy / "ecs-service.json").read_text())
    json.loads((deploy / "iam-task-role-policy.json").read_text())
    container = task["containerDefinitions"][0]
    code = (ROOT / "site" / "corvane_store.py").read_text() + (deploy / "sign-in.toml").read_text()
    for secret in container["secrets"]:
        name = secret["name"]
        assert name in code or name == "PGPASSWORD", name
    config = Path(__file__).parents[2] / "src" / "quant_agent_os" / "config" / "settings.py"
    for item in container["environment"]:
        if item["name"].startswith("QUANT_AGENT_OS_"):
            assert item["name"].removeprefix("QUANT_AGENT_OS_").lower() in config.read_text(), item
    assert (
        service["loadBalancers"][0]["containerPort"]
        == container["portMappings"][0]["containerPort"]
    )
    assert "/healthz" in json.dumps(container["healthCheck"]) + json.dumps(service["_targetGroup"])


# ---- the legacy package, scanned ---------------------------------------------------------------


def test_the_legacy_scan_classifies_the_stateful_code_as_services() -> None:
    from quant_platform.cli import app

    result = CliRunner().invoke(
        app, ["legacy", "scan", str(ROOT / "facility" / "legacy" / "corvane_quant"), "--json"]
    )
    assert result.exit_code == 0, result.output
    found = json.loads(result.output)
    kinds = {item["name"]: item["kind"] for item in found["entries"]}
    assert kinds.get("covariance") == "service" and kinds.get("run") == "service"
    assert (
        kinds.get("load_prices") == "facade"
    )  # it keeps state in config.PRICES, which the scan misses
