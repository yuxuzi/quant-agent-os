"""examples/pilot-fund/facility: the mock fund's warehouse, legacy code, documents, and gateway."""

import base64
import hashlib
import json
import math
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import anyio
import numpy as np
import pandas as pd
import pytest
from build_warehouse import TABLES, build, rank_ic
from idp import DEFAULT_CONFIG, IdpConfig, RunningIdp, start

from quant_agent_os.tools import Caller, FileAccessPolicy
from quant_research.infrastructure.files import TextFileLoader

FACILITY = Path(__file__).parents[2] / "examples" / "pilot-fund" / "facility"
PLATFORM = re.compile(
    r"^\s*(from|import)\s+(quant_agent_os|quant_platform|quant_research|quant_analytics|"
    r"quant_app_factory|quant_toolbox)\b",
    re.MULTILINE,
)


def _contents_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with closing(sqlite3.connect(path)) as db:
        for table in TABLES:
            rows = db.execute(f"SELECT * FROM {table}").fetchall()
            digest.update(repr(sorted(rows, key=repr)).encode())
    return digest.hexdigest()


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("warehouse") / "kestrel.db"
    build(path)
    return path


@pytest.fixture(scope="module")
def answers(warehouse: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((warehouse.parent / "known_answers.json").read_text())
    return loaded


def _frame(path: Path, sql: str, *params: object) -> pd.DataFrame:
    with closing(sqlite3.connect(path)) as db:
        return pd.read_sql_query(sql, db, params=params or None)


# ---- the warehouse ---------------------------------------------------------------------------


def test_the_same_seed_builds_the_same_warehouse(tmp_path: Path, warehouse: Path) -> None:
    build(tmp_path / "again.db")
    build(tmp_path / "other.db", seed=1)
    assert _contents_hash(tmp_path / "again.db") == _contents_hash(warehouse)
    assert _contents_hash(tmp_path / "other.db") != _contents_hash(warehouse)


def test_the_warehouse_has_the_expected_tables_and_sizes(
    warehouse: Path, answers: dict[str, Any]
) -> None:
    assert warehouse.stat().st_size < 8_000_000
    with closing(sqlite3.connect(warehouse)) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == set(TABLES)
        classes = dict(db.execute("SELECT asset_class, COUNT(*) FROM ref_instruments GROUP BY 1"))
        desks = {row[0] for row in db.execute("SELECT DISTINCT desk FROM pos_eod")}
        factors = db.execute("SELECT COUNT(DISTINCT factor_id) FROM fm_loadings").fetchone()[0]
        model_dates = db.execute("SELECT COUNT(DISTINCT as_of_date) FROM fm_specific").fetchone()
    assert classes == {
        "equity": 20,
        "govt_bond_future": 4,
        "fx": 8,
        "credit_index": 2,
        "commodity_future": 6,
    }
    assert desks == {"eq_ls", "macro", "commod"}
    assert factors == 5
    assert model_dates[0] == 36
    assert answers["business_days"] == 783
    assert answers["row_counts"]["mkt_px_daily"] == 783 * 40
    assert answers["row_counts"]["pos_eod"] == 783 * 40


def test_prices_and_returns_agree(warehouse: Path) -> None:
    px = _frame(warehouse, "SELECT date, instrument_id, close, total_return FROM mkt_px_daily")
    close = px.pivot(index="date", columns="instrument_id", values="close")
    returns = px.pivot(index="date", columns="instrument_id", values="total_return").iloc[1:]
    implied = (close / close.shift(1) - 1).iloc[1:]
    assert np.allclose(implied.values, returns.values, atol=1e-5)
    assert (close > 0).all().all()


def test_the_planted_signals_hold(warehouse: Path, answers: dict[str, Any]) -> None:
    px = _frame(warehouse, "SELECT date, instrument_id, total_return FROM mkt_px_daily")
    returns = px.pivot(index="date", columns="instrument_id", values="total_return")
    signals = _frame(warehouse, "SELECT date, instrument_id, signal_name, value FROM sig_daily")
    found = {}
    for name in ("momentum", "carry", "value"):
        wide = signals[signals.signal_name == name].pivot(
            index="date", columns="instrument_id", values="value"
        )
        forward = returns[wide.columns].shift(-1)
        ic = rank_ic(wide.values[:-1], forward.values[:-1])
        found[name] = (ic.mean(), ic.mean() / (ic.std(ddof=1) / math.sqrt(len(ic))))
        assert found[name][0] == pytest.approx(answers["signals"][name]["mean_ic"], abs=1e-9)
    assert 0.015 < found["momentum"][0] < 0.045 and found["momentum"][1] > 3
    assert abs(found["carry"][0]) < 0.02 and abs(found["carry"][1]) < 2
    assert found["value"][0] > 0


def test_the_known_answers_at_the_platforms_default_are_what_analyze_signal_finds(
    warehouse: Path, answers: dict[str, Any]
) -> None:
    pytest.importorskip("empyrical")
    from quant_agent_os.data import MemoryDatasetStore
    from quant_agent_os.runtime import ToolCall, ToolExecutor
    from quant_agent_os.tools import Permission, PermissionPolicy
    from quant_analytics.signals import default_horizons
    from quant_analytics.tools import SignalRequest, analytics_tools

    defaults = SignalRequest.model_validate(
        {"signal": {"dataset": "s"}, "returns": {"dataset": "r"}}
    )
    planted = answers["signals"]["at_platform_default"]
    daily, _ = default_horizons(252)  # the warehouse's prices are daily
    assert defaults.horizons is None  # the horizons follow the dates' frequency
    assert (planted["lag"], planted["horizon"]) == (defaults.lag, daily[0])

    store = MemoryDatasetStore()
    px = _frame(warehouse, "SELECT date, instrument_id, total_return FROM mkt_px_daily")
    store.put("px_returns", px.pivot(index="date", columns="instrument_id", values="total_return"))
    signals = _frame(warehouse, "SELECT date, instrument_id, signal_name, value FROM sig_daily")
    policy = PermissionPolicy(granted=PermissionPolicy().granted | {Permission.READ_LOCAL_DATA})
    executor = ToolExecutor(analytics_tools(store), policy)
    for name in ("momentum", "carry", "value"):
        wide = signals[signals.signal_name == name].pivot(
            index="date", columns="instrument_id", values="value"
        )
        store.put(f"signals_{name}", wide)
        record = executor.execute(
            ToolCall(
                tool="analyze_signal",
                arguments={
                    "signal": {"dataset": f"signals_{name}"},
                    "returns": {"dataset": "px_returns"},
                },
            )
        )
        assert record.status == "ok", record.error
        summary, expected = record.output["summary"], planted[name]
        assert summary["dates"] == expected["days"]
        assert summary["mean_rank_ic"] == pytest.approx(expected["mean_rank_ic"], abs=1e-9)
        assert summary["rank_ic_ir"] == pytest.approx(expected["rank_ic_ir"], abs=1e-6)
        assert summary["rank_ic_t_stat"] == pytest.approx(expected["hac_t_stat"], abs=1e-4)
    # The planted momentum is still found a day later, as the drill's clients see it by default.
    assert planted["momentum"]["mean_rank_ic"] > 0.015 and planted["momentum"]["hac_t_stat"] > 3


def test_desk_pnl_follows_from_positions_and_prices(
    warehouse: Path, answers: dict[str, Any]
) -> None:
    px = _frame(warehouse, "SELECT date, instrument_id, close FROM mkt_px_daily")
    close = px.pivot(index="date", columns="instrument_id", values="close")
    for desk, truth in answers["desks"].items():
        pos = _frame(
            warehouse, "SELECT date, instrument_id, quantity FROM pos_eod WHERE desk = ?", desk
        ).pivot(index="date", columns="instrument_id", values="quantity")
        pnl = (pos.shift(1) * close[pos.columns].diff()).sum().sum()
        assert pnl == pytest.approx(truth["gross_pnl_usd"], rel=1e-6)
        fees = _frame(
            warehouse, "SELECT SUM(commission_usd) AS fees FROM trades WHERE desk = ?", desk
        ).fees[0]
        assert fees == pytest.approx(truth["commissions_usd"], abs=1.0)


# ---- the legacy package ----------------------------------------------------------------------


@pytest.fixture
def legacy(warehouse: Path, tmp_path: Path) -> Iterator[Any]:
    import kestrel_quant
    from kestrel_quant import db

    saved = dict(kestrel_quant.CONFIG)
    kestrel_quant.CONFIG.update(warehouse=str(warehouse), output_dir=str(tmp_path), verbose=False)
    db.close()
    try:
        yield kestrel_quant
    finally:
        db.close()
        kestrel_quant.CONFIG.clear()
        kestrel_quant.CONFIG.update(saved)


def test_the_simple_legacy_functions_give_sensible_numbers(
    legacy: Any, answers: dict[str, Any], tmp_path: Path
) -> None:
    from kestrel_quant import db, portfolio, reports, signals

    momentum = signals.momentum_signal()
    last = momentum.iloc[-1]
    assert last.notna().sum() == 40 and abs(last.mean()) < 1e-9
    carry = signals.carry_signal()
    assert carry.shape[1] == 20 and carry.abs().max().max() <= 3.0
    value = signals.value_zscore()
    assert value.dropna(axis=1, how="all").shape[1] == 20

    returns = db.load_returns()
    weights = portfolio.vol_target_weights(returns, momentum, window=63)
    assert np.isfinite(weights.values).all()
    book = portfolio.backtest(weights, returns, cost_bps=2)
    stats = reports.summary(book.iloc[130:])
    assert 0.03 < stats["ann_vol"] < 0.2
    table = reports.drawdown_table(book)
    assert (table.depth < 0).all() and (table.depth > -0.5).all()

    report = reports.desk_pnl_report("macro")
    truth = answers["desks"]["macro"]
    assert report.net_pnl.sum() == pytest.approx(
        truth["gross_pnl_usd"] - truth["commissions_usd"], rel=1e-6
    )
    assert (tmp_path / "pnl_macro.csv").exists()  # the side effect, as the fund wrote it


def test_the_risk_engine_loads_once_and_answers_with_sensible_numbers(legacy: Any) -> None:
    from kestrel_quant.risk_engine import RiskEngine, RiskEngineError

    engine = RiskEngine(load_delay_s=0)
    with pytest.raises(RiskEngineError, match="load"):
        engine.desk_var("macro")
    engine.load()
    assert engine.desks() == ["commod", "eq_ls", "macro"]
    for desk in engine.desks():
        gross = engine.exposure(desk)["gross"]
        figures = [engine.desk_var(desk, method=m)["var_usd"] for m in ("historical", "parametric")]
        figures.append(engine.desk_var(desk, method="factor")["var_usd"])
        for var in figures:
            assert math.isfinite(var) and 0 < var < 0.1 * gross
        assert max(figures) < 2 * min(figures)
        decomposition = engine.factor_decomposition(desk)
        shares = sum(decomposition["variance_share"].values()) + decomposition["specific_share"]
        assert shares == pytest.approx(1.0)
        for result in engine.stress_all(desk):
            assert math.isfinite(result["pnl_usd"]) and abs(result["pnl_usd"]) < gross
    assert engine.factor_decomposition("macro")["variance_share"]["usd"] > 0.5
    assert engine.desk_var("macro") is engine.desk_var("macro")  # cached
    with pytest.raises(RiskEngineError, match="unknown scenario"):
        engine.stress("macro", "alien_invasion")
    earlier = RiskEngine(as_of="2024-06-28", load_delay_s=0).load()
    assert earlier.as_of == "2024-06-28" and earlier.model_date <= "2024-06-28"


# ---- research documents ----------------------------------------------------------------------


def test_the_research_folder_holds_the_documents_and_the_injection() -> None:
    folder = FACILITY / "research"
    documents = sorted(folder.glob("*.md"))
    assert len(documents) >= 15
    loader = TextFileLoader()
    loaded = [loader.load(path, folder) for path in documents]
    assert all(source.metadata.get("title") for source in loaded)
    injected = [s for s in loaded if "ignore previous instructions" in s.body.lower()]
    assert [s.uri for s in injected] == ["note-data-vendor-update.md"]
    assert any(s.uri.startswith("minutes-risk-committee") for s in loaded)


# ---- the gateway -----------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def idp(clock: Clock) -> Iterator[RunningIdp]:
    with start(DEFAULT_CONFIG, clock=clock) as running:
        yield running


def _basic(client: str, secret: str) -> str:
    return "Basic " + base64.b64encode(f"{client}:{secret}".encode()).decode()


def _post(url: str, form: dict[str, str], auth: str | None = None) -> tuple[int, dict[str, Any]]:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if auth:
        headers["Authorization"] = auth
    request = urllib.request.Request(
        url, data=urllib.parse.urlencode(form).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        with error:
            return error.code, json.loads(error.read())


SERVER = _basic("qaos-server", "kpc-introspection-fixture")


def _token(idp: RunningIdp, user: str, **extra: str) -> str:
    status, body = _post(
        f"{idp.base_url}/token",
        {"grant_type": "password", "username": user, "password": f"kpc-{user}", **extra},
    )
    assert status == 200, body
    token: str = body["access_token"]
    return token


def _introspect(idp: RunningIdp, token: str, auth: str = SERVER) -> tuple[int, dict[str, Any]]:
    return _post(f"{idp.base_url}/introspect", {"token": token}, auth)


def test_the_gateway_issues_and_introspects_tokens(idp: RunningIdp, clock: Clock) -> None:
    status, claims = _introspect(idp, _token(idp, "pm-macro"))
    assert status == 200
    assert claims["active"] is True
    assert claims["iss"] == idp.base_url
    assert claims["aud"] == ["http://127.0.0.1:8000/mcp"]
    assert claims["sub"] == "pm-macro" and claims["client_id"] == "kpc-cli"
    assert claims["scope"] == "tools:list tools:call"
    assert claims["qaos_tenant"] == "kestrel" and claims["groups"] == ["desk-macro"]
    assert claims["iat"] == int(clock.now) and claims["exp"] == int(clock.now) + 3600

    with urllib.request.urlopen(
        f"{idp.base_url}/.well-known/oauth-authorization-server", timeout=5
    ) as response:
        metadata = json.loads(response.read())
    assert metadata["issuer"] == idp.base_url
    assert metadata["introspection_endpoint"] == f"{idp.base_url}/introspect"

    status, body = _post(
        f"{idp.base_url}/token",
        {"grant_type": "client_credentials"},
        _basic("kpc-batch", "kpc-batch-fixture"),
    )
    assert status == 200
    assert _introspect(idp, body["access_token"])[1]["groups"] == ["operators"]


def test_the_gateway_refuses_bad_credentials(idp: RunningIdp) -> None:
    token = _token(idp, "researcher")
    assert _introspect(idp, token, _basic("qaos-server", "wrong"))[0] == 401
    assert _introspect(idp, token, "Bearer " + token)[0] == 401
    assert _introspect(idp, token, _basic("kpc-batch", "kpc-batch-fixture"))[0] == 403
    status, body = _post(
        f"{idp.base_url}/token",
        {"grant_type": "password", "username": "researcher", "password": "guess"},
    )
    assert (status, body["error"]) == (400, "invalid_grant")
    status, body = _post(
        f"{idp.base_url}/token",
        {"grant_type": "password", "username": "former-analyst", "password": "kpc-former-analyst"},
    )
    assert (status, body["error"]) == (400, "invalid_grant")
    forged = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    assert _introspect(idp, forged)[1] == {"active": False}


def test_expired_revoked_and_disabled_tokens_are_inactive(idp: RunningIdp, clock: Clock) -> None:
    short = _token(idp, "pm-equities", expires_in="60")
    revoked = _token(idp, "pm-equities")
    disabled = _token(idp, "pm-commodities")
    clock.now += 61
    assert _introspect(idp, short)[1] == {"active": False}
    assert _introspect(idp, revoked)[1]["active"] is True
    assert _post(f"{idp.base_url}/revoke", {"token": revoked}, SERVER)[0] == 200
    assert _introspect(idp, revoked)[1] == {"active": False}
    idp.provider.disable_user("pm-commodities")
    assert _introspect(idp, disabled)[1] == {"active": False}
    clock.now += 3600
    assert _introspect(idp, _token(idp, "risk-manager"))[1]["active"] is True


def test_the_claim_names_are_configurable(clock: Clock) -> None:
    data = json.loads(DEFAULT_CONFIG.read_text())
    data |= {"tenant_claim": "kpc_tenant", "groups_claim": "roles", "issuer": "https://login.kpc"}
    with start(IdpConfig.from_dict(data), clock=clock) as idp:
        claims = _introspect(idp, _token(idp, "researcher"))[1]
    assert claims["kpc_tenant"] == "kestrel" and claims["roles"] == ["research"]
    assert claims["iss"] == "https://login.kpc" and "groups" not in claims


def test_the_platform_verifier_accepts_the_gateways_tokens() -> None:
    pytest.importorskip("mcp")
    with start(DEFAULT_CONFIG) as idp:  # the real clock, as the verifier uses
        _check_platform_verifier(idp)


def _check_platform_verifier(idp: RunningIdp) -> None:
    from pydantic import SecretStr

    from quant_platform.identity.oauth import AuthorizationServer, IntrospectionVerifier

    verifier = IntrospectionVerifier(
        [
            AuthorizationServer(
                issuer=idp.base_url,
                introspection_url=f"{idp.base_url}/introspect",
                client_id="qaos-server",
                client_secret=SecretStr("kpc-introspection-fixture"),
            )
        ],
        resource="http://127.0.0.1:8000/mcp",
        tenants_by_issuer={idp.base_url: {"kestrel"}},
    )
    access = anyio.run(verifier.verify_token, _token(idp, "risk-manager"))
    assert access is not None
    assert access.subject == "risk-manager"
    assert access.scopes == ["tools:list", "tools:call"]
    assert access.claims is not None and access.claims["tenant"] == "kestrel"
    assert anyio.run(verifier.verify_token, "not-a-token") is None


# ---- entitlements and boundaries -------------------------------------------------------------


def test_desk_entitlements_follow_the_desks() -> None:
    policy = FileAccessPolicy(FACILITY / "access.toml")

    def sees(group: str, dataset: str) -> bool:
        return policy.allows(
            Caller("someone", "kestrel", frozenset({group})), "dataset.read", dataset
        )

    assert sees("desk-eq-ls", "positions_eq_ls") and sees("desk-eq-ls", "px_returns")
    assert not sees("desk-eq-ls", "positions_macro")
    assert not sees("desk-macro", "desk_returns_commod")
    assert all(sees("risk", f"positions_{desk}") for desk in ("eq_ls", "macro", "commod"))
    assert sees("research", "signals_momentum") and sees("research", "fm_loadings")
    assert not any(sees("research", f"positions_{d}") for d in ("eq_ls", "macro", "commod"))
    assert not sees("operators", "px_returns")


def test_the_facility_imports_nothing_of_the_platform() -> None:
    sources = list(FACILITY.rglob("*.py"))
    assert len(sources) >= 8
    assert [str(p) for p in sources if PLATFORM.search(p.read_text())] == []
