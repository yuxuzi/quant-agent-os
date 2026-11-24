"""The pilot fund's day, in process: Kestrel Point Capital's own server behind its gateway.

The fund's site (examples/pilot-fund/site) serves the platform's toolsets with its warehouse, its
documents, and its legacy code, signed in through the mock gateway and scoped by access.toml. The
gateway and the risk-engine service run on loopback in threads; the platform's HTTP app is driven
through ASGI. Nothing leaves the machine. drill.sh tells the same story from a built wheel.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
import httpx2
import pytest

pytest.importorskip("empyrical")
pytest.importorskip("fastapi")
uvicorn = pytest.importorskip("uvicorn")

from build_warehouse import build  # noqa: E402
from idp import DEFAULT_CONFIG, RunningIdp, start  # noqa: E402
from mcp import Client  # noqa: E402
from mcp.client.session import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from quant_agent_os.api import add_key  # noqa: E402
from quant_agent_os.caller_assertion import CALLER_HEADER, sign_caller  # noqa: E402
from quant_agent_os.config import get_settings  # noqa: E402
from quant_agent_os.config.secrets import read_secret  # noqa: E402
from quant_agent_os.runs import SqliteRunStore  # noqa: E402
from quant_platform.cli import app as qaos  # noqa: E402
from quant_platform.identity import oauth  # noqa: E402
from quant_research import open_research  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "examples" / "pilot-fund"
SITE = PILOT / "site"
FACILITY = PILOT / "facility"
RESOURCE = "http://127.0.0.1:8000"
SECRET = "kpc-introspection-fixture"
# The risk service's caller secret, named by a file the test writes (decision 0030).
CALLER_SECRET_VALUE = "kpc-risk-caller-secret-fixture-0123456789"
# The tools that call the risk service, the only ones given a standing approval for network.
RISK_TOOLS = [
    "risk_desks",
    "risk_exposure",
    "risk_desk_var",
    "risk_stress",
    "risk_stress_all",
    "risk_factor_decomposition",
]


@dataclass
class Day:
    """The fund's server, its gateway, and what they need."""

    app: Any
    idp: RunningIdp
    home: Path
    answers: dict[str, Any]
    kestrel: Any  # the site's kestrel_server module
    output: str
    runs: list[SqliteRunStore]  # the run stores the server answers from, in the order opened


@pytest.fixture(scope="module")
def site_modules() -> Iterator[Any]:
    """Import the fund's server module, and forget it and the legacy package afterwards."""
    before = set(sys.modules)
    path = list(sys.path)
    sys.path.insert(0, str(SITE))
    import kestrel_server

    yield kestrel_server
    from kestrel_quant import db

    db.close()
    sys.path[:] = path
    for name in set(sys.modules) - before:
        if name.split(".")[0] in {"kestrel_server", "server", "facades", "stubs", "service"}:
            del sys.modules[name]
        if name.split(".")[0] in {"kestrel_quant", "warehouse_store"}:
            del sys.modules[name]


@pytest.fixture(scope="module")
def risk_service(
    site_modules: Any, warehouse: Path, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[str]:
    """Serve the drafted, finished risk-engine service on a free loopback port.

    Its caller secret comes from a file, as the fund's deployment gives it, and the server reads
    the same file: nothing names the secret's value in the environment.
    """
    from kestrel_quant.config import CONFIG
    from service.app import app

    secret = tmp_path_factory.mktemp("risk-caller") / "caller.secret"
    secret.write_text(CALLER_SECRET_VALUE, "utf-8")
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("KPC_RISK_CALLER_SECRET", raising=False)
        patch.setenv("KPC_RISK_CALLER_SECRET_FILE", str(secret))
        CONFIG["risk"]["load_delay_s"] = 0  # the vendor download, which the drill keeps
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 30
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline, (
                "the risk service did not start"
            )
            time.sleep(0.05)
        yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    path = tmp_path_factory.mktemp("warehouse") / "kestrel.db"
    build(path)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("KPC_WAREHOUSE", str(path))
        from kestrel_quant.config import CONFIG  # read at import, so set it too

        patch.setitem(CONFIG, "warehouse", str(path))
        yield path


@pytest.fixture(scope="module")
def gateway() -> Iterator[RunningIdp]:
    with start(DEFAULT_CONFIG) as running:
        yield running


@pytest.fixture
def day(
    site_modules: Any,
    warehouse: Path,
    risk_service: str,
    gateway: RunningIdp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    request: pytest.FixtureRequest,
) -> Day:
    """Install the fund's settings in a fresh home, ingest its documents, and start its server
    up to listening, keeping the HTTP app it would serve."""
    home = tmp_path / "qaos-home"
    sign_in = tmp_path / "sign-in.toml"
    sign_in.write_text(
        (SITE / "sign-in.toml")
        .read_text("utf-8")
        .replace("http://127.0.0.1:9400", gateway.base_url),
        "utf-8",
    )
    environment = {
        "QUANT_AGENT_OS_ACCESS_FILE": str(FACILITY / "access.toml"),
        "QUANT_AGENT_OS_SIGN_IN_FILE": str(sign_in),
        "KESTREL_INTROSPECTION_SECRET": SECRET,
        "QUANT_AGENT_OS_GRANTED_PERMISSIONS": '["read_local_data", "network"]',
        "QUANT_AGENT_OS_PREAPPROVED_TOOLS": json.dumps({"network": RISK_TOOLS}),
        "QUANT_AGENT_OS_LOG_FORMAT": "json",
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    with open_research(get_settings().store) as research:
        research.ingest.ingest(FACILITY / "research")
    monkeypatch.setattr(site_modules.legacy, "SERVICE_URL", risk_service)
    served: list[Any] = []
    monkeypatch.setattr("quant_platform.serve.run_http", lambda app, **_: served.append(app))
    # serve_http closes its run store when serving stops; here serving "stops" at once, and the
    # tests then drive the app, so the store closes at the end of the test instead.
    stores: list[SqliteRunStore] = []

    class KeptOpen(SqliteRunStore):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            stores.append(self)

        def close(self) -> None:
            pass

    monkeypatch.setattr("quant_platform.serve.SqliteRunStore", KeptOpen)
    request.addfinalizer(lambda: [SqliteRunStore.close(store) for store in stores])
    # Likewise the corpus, which serve() opens for as long as it serves.
    opened = open_research(get_settings().store, read_only=True)
    request.addfinalizer(opened.close)
    monkeypatch.setattr(site_modules, "_research", lambda stack: opened.research)
    site_modules.serve()
    answers = json.loads((warehouse.parent / "known_answers.json").read_text())
    output = " ".join(capsys.readouterr().err.split())
    return Day(served[-1], gateway, home, answers, site_modules, output, stores)


def _token(day: Day, user: str) -> str:
    answer = httpx2.post(
        f"{day.idp.base_url}/token",
        data={"grant_type": "password", "username": user, "password": f"kpc-{user}"},
    )
    answer.raise_for_status()
    token: str = answer.json()["access_token"]
    return token


def _rest(day: Day, credential: str, calls: list[tuple[str, str, Any]]) -> list[httpx2.Response]:
    """Make each (method, path, json) request as ``credential``, in one server lifespan."""

    async def main() -> list[httpx2.Response]:
        async with (
            day.app.router.lifespan_context(day.app),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=day.app),
                base_url=RESOURCE,
                headers={"Authorization": f"Bearer {credential}"},
            ) as client,
        ):
            return [await client.request(m, p, json=body) for m, p, body in calls]

    return anyio.run(main)


def _call(day: Day, credential: str, tool: str, **arguments: Any) -> dict[str, Any]:
    (answer,) = _rest(
        day, credential, [("POST", f"/v1/tools/{tool}/call", {"arguments": arguments})]
    )
    body: dict[str, Any] = answer.json()
    body["http_status"] = answer.status_code
    return body


def _get(day: Day, path: str, credential: str | None = None) -> httpx2.Response:
    """Make one GET as ``credential`` (or with none), in one server lifespan."""
    headers = {"Authorization": f"Bearer {credential}"} if credential else {}

    async def main() -> httpx2.Response:
        async with (
            day.app.router.lifespan_context(day.app),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=day.app), base_url=RESOURCE, headers=headers
            ) as client,
        ):
            return await client.get(path)

    return anyio.run(main)


def _over_mcp(
    day: Day,
    credential: str,
    check: Callable[[Any], Awaitable[Any]],
    *,
    newest: bool,
) -> tuple[str, Any]:
    """Run ``check`` on an MCP session as ``credential``; return the revision and its answer.

    ``newest`` negotiates the newest revision the client can (2026-07-28); otherwise the client
    sends the 2025-11-25 ``initialize`` handshake, as the older clients do.
    """

    async def main() -> tuple[str, Any]:
        async with (
            day.app.router.lifespan_context(day.app),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=day.app),
                base_url=RESOURCE,
                headers={"Authorization": f"Bearer {credential}"},
            ) as http,
        ):
            if newest:
                async with Client(
                    streamable_http_client(f"{RESOURCE}/mcp", http_client=http),
                    mode="auto",
                    cache=None,
                ) as client:
                    return client.protocol_version, await check(client)
            async with (
                streamable_http_client(f"{RESOURCE}/mcp", http_client=http) as (read, write),
                ClientSession(read, write) as session,
            ):
                initialized = await session.initialize()
                return initialized.protocol_version, await check(session)

    return anyio.run(main)


def _usage(day: Day, principal: str | None = None) -> dict[str, Any]:
    """Run ``qaos runs report`` over the period around this test, as JSON, against the test's store.

    The period runs from yesterday to the day after tomorrow, so a run near midnight UTC is counted.
    """
    today = dt.datetime.now(dt.UTC).date()
    result = CliRunner().invoke(
        qaos,
        [
            "runs",
            "report",
            "--from",
            str(today - dt.timedelta(days=1)),
            "--to",
            str(today + dt.timedelta(days=2)),
            "--runs",
            str(get_settings().runs_file),
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    report: dict[str, Any] = json.loads(result.stdout)
    if principal is not None:
        rows = [row for row in report["by_principal"] if row["principal"] == principal]
        assert len(rows) <= 1
        return rows[0] if rows else {}
    return report


def _latest_weights(warehouse: Path, desk: str) -> dict[str, float]:
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(warehouse)) as db:
        rows = db.execute(
            "SELECT instrument_id, weight FROM pos_eod WHERE desk = ? AND date = "
            "(SELECT MAX(date) FROM pos_eod)",
            (desk,),
        ).fetchall()
    return {name: weight for name, weight in rows}


# Sign-in and entitlements (B2, B3)


def test_each_user_reaches_only_their_groups_tools_and_datasets(day: Day) -> None:
    assert "users signed in by" in day.output and day.idp.base_url in day.output

    def tools(user: str) -> set[str]:
        (listed,) = _rest(day, _token(day, user), [("GET", "/v1/tools", None)])
        assert listed.status_code == 200, listed.text
        return {item["name"] for item in listed.json()}

    pm, research, risk, operator = (
        tools(user) for user in ("pm-equities", "researcher", "risk-manager", "operator")
    )
    assert {"analyze_signal", "run_backtest", "kpc_desk_pnl", "find_related_signals"} <= pm
    assert not {name for name in pm if name.startswith("risk_")}
    assert "kpc_desk_pnl" not in research and "kpc_momentum_signal" in research
    assert {"risk_desk_var", "risk_stress_all", "kpc_desk_pnl"} <= risk
    assert operator == {"server_info", "list_datasets"}


def test_a_pm_sees_their_own_desk_and_a_refused_desk_looks_missing(day: Day) -> None:
    pm = _token(day, "pm-equities")
    listed = _call(day, pm, "list_datasets")
    ids = {item["id"] for item in listed["output"]["datasets"]}
    assert "positions_eq_ls" in ids and "px_returns" in ids
    assert not {"positions_macro", "weights_commod", "desk_returns_macro"} & ids

    own = _call(
        day,
        pm,
        "analyze_performance",
        returns={"dataset": "desk_returns_eq_ls", "column": "gross_return"},
    )
    other = _call(
        day,
        pm,
        "analyze_performance",
        returns={"dataset": "desk_returns_macro", "column": "gross_return"},
    )
    missing = _call(
        day,
        pm,
        "analyze_performance",
        returns={"dataset": "desk_returns_nowhere", "column": "gross_return"},
    )
    assert own["status"] == "ok"
    assert other["status"] == missing["status"] != "ok"
    assert other["error"].replace("desk_returns_macro", "X") == missing["error"].replace(
        "desk_returns_nowhere", "X"
    )

    pnl = _call(day, pm, "kpc_desk_pnl", desk="eq_ls")
    refused = _call(day, pm, "kpc_desk_pnl", desk="macro")
    unknown = _call(day, pm, "kpc_desk_pnl", desk="credit")
    assert pnl["status"] == "ok" and pnl["output"]["days"] == 783
    assert refused["status"] == unknown["status"] != "ok"
    assert refused["error"] == unknown["error"].replace("credit", "macro")
    assert pnl["output"]["gross_pnl"] == pytest.approx(
        day.answers["desks"]["eq_ls"]["gross_pnl_usd"], rel=1e-9
    )


def test_research_sees_signals_but_no_positions(day: Day) -> None:
    researcher = _token(day, "researcher")
    refused = _call(
        day,
        researcher,
        "analyze_performance",
        returns={"dataset": "desk_returns_eq_ls", "column": "gross_return"},
    )
    denied = _call(day, researcher, "kpc_desk_pnl", desk="eq_ls")
    assert refused["status"] != "ok" and "desk_returns_eq_ls" not in refused["error"].split(";")[-1]
    assert denied["status"] == "denied"


def test_an_api_key_still_works_beside_sign_in(day: Day) -> None:
    key = add_key(
        get_settings().api_keys_file,
        "batch-research",
        ["tools:list", "tools:call"],
        groups=["research"],
    )
    found = _call(day, key, "list_datasets")
    assert found["status"] == "ok"
    assert not any(item["id"].startswith("positions_") for item in found["output"]["datasets"])


# Baseline analysis (B5)


def test_analyze_signal_recovers_the_planted_momentum_ic(day: Day) -> None:
    researcher = _token(day, "researcher")
    planted = day.answers["signals"]["momentum"]
    found = _call(
        day,
        researcher,
        "analyze_signal",
        signal={"dataset": "signals_momentum"},
        returns={"dataset": "px_returns"},
        lag=0,  # the planted convention: a signal on day t against the return of day t + 1
    )
    assert found["status"] == "ok", found
    summary = found["output"]["summary"]
    assert summary["mean_rank_ic"] == pytest.approx(planted["mean_ic"], abs=1e-9)
    assert summary["rank_ic_ir"] == pytest.approx(planted["ic_ir"], abs=1e-6)
    carry = _call(
        day,
        researcher,
        "analyze_signal",
        signal={"dataset": "signals_carry"},
        returns={"dataset": "px_returns"},
        lag=0,
    )
    assert carry["output"]["summary"]["mean_rank_ic"] == pytest.approx(
        day.answers["signals"]["carry"]["mean_ic"], abs=1e-9
    )


def test_a_backtest_and_the_risk_of_a_desk_with_stress_and_the_vendor_model(
    day: Day, warehouse: Path
) -> None:
    risk = _token(day, "risk-manager")
    backtest = _call(
        day,
        risk,
        "run_backtest",
        signal={"dataset": "signals_momentum"},
        returns={"dataset": "px_returns"},
        cost_bps=5,
    )
    assert backtest["status"] == "ok", backtest
    assert backtest["output"]["summary"]["last_date"] == "2025-12-31"

    weights = _latest_weights(warehouse, "macro")
    found = _call(
        day,
        risk,
        "analyze_risk",
        portfolio_id="macro",
        weights=weights,
        asset_returns={"dataset": "px_returns"},
        factor_model={
            "loadings": "fm_loadings",
            "factor_covariance": "fm_factor_cov",
            "specific_variance": {"dataset": "fm_specific", "column": "specific_var_annual"},
            "periods_per_year": 1,
        },
    )
    assert found["status"] == "ok", found
    output = found["output"]
    assert output["factor_model"] is not None
    assert output["historical_stress"] or output["historical_stress_note"]


# Legacy code served (B6)


def test_the_legacy_facades_and_the_risk_engine_service_answer(day: Day) -> None:
    risk = _token(day, "risk-manager")
    momentum = _call(day, risk, "kpc_momentum_signal", prices={"dataset": "px_close"})
    var = _call(day, risk, "risk_desk_var", desk="macro", method="factor")
    stress = _call(day, risk, "risk_stress_all", desk="eq_ls")
    assert momentum["status"] == "ok" and len(momentum["output"]["result"]) == 40
    assert var["status"] == "ok", var
    assert var["output"]["var_usd"] > 0 and var["output"]["as_of"] == "2025-12-31"
    assert stress["status"] == "ok" and len(stress["output"]["result"]) == 5
    pm = _token(day, "pm-macro")
    assert _call(day, pm, "risk_desk_var", desk="macro")["status"] == "denied"


# Research over the fund's documents (B4)


def test_research_is_cited_and_the_injected_note_stays_inert(day: Day) -> None:
    researcher = _token(day, "researcher")
    found = _call(
        day, researcher, "find_related_signals", query="vendor factor model file format change", k=5
    )
    assert found["status"] == "ok", (found["error"], found["hint"])
    sources = [p["source"]["source_id"] for p in found["output"]["passages"]]
    assert "note-data-vendor-update.md" in sources
    assert all(p["source"]["source_id"].endswith(".md") for p in found["output"]["passages"])
    assert "untrusted" in json.dumps(found["output"]).lower()

    # Nothing ran but the call made: the note's instructions reached no tool.
    with SqliteRunStore(get_settings().runs_file) as runs:
        recent = runs.list_runs(5)
    assert [run.principal for run in recent[:1]] == ["researcher"]
    assert len(recent) == 1


# MCP and the audit trail (B7)


def test_mcp_with_a_token_and_the_run_store_names_each_user(day: Day) -> None:
    pm = _token(day, "pm-macro")

    async def main() -> Any:
        async with (
            day.app.router.lifespan_context(day.app),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=day.app),
                base_url=RESOURCE,
                headers={"Authorization": f"Bearer {pm}"},
            ) as client,
            streamable_http_client(f"{RESOURCE}/mcp", http_client=client) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            return await session.call_tool("kpc_desk_pnl", {"desk": "macro"})

    called = anyio.run(main)
    assert called.is_error is False
    _call(day, _token(day, "pm-equities"), "kpc_desk_pnl", desk="macro")
    with SqliteRunStore(get_settings().runs_file) as runs:
        recorded = {(run.actor, run.principal) for run in runs.list_runs(10)}
    assert recorded == {("mcp-http", "pm-macro"), ("rest", "pm-equities")}


def test_the_agreed_cases_pass(day: Day, capsys: pytest.CaptureFixture[str]) -> None:
    code = day.kestrel.acceptance(SITE / "acceptance.toml")
    report = capsys.readouterr().out
    assert code == 0, report
    assert report.startswith("# Acceptance: PASS")


# MCP 2026-07-28 (decision 0034): a client that negotiates the newest revision gets the same answers


def test_a_2026_07_28_client_gets_the_fund_tools_and_the_same_refusal(day: Day) -> None:
    pm = _token(day, "pm-equities")

    async def listed(session: Any) -> list[str]:
        return sorted(tool.name for tool in (await session.list_tools()).tools)

    async def refused(session: Any) -> tuple[bool | None, str]:
        called = await session.call_tool("kpc_desk_pnl", {"desk": "macro"})
        return called.is_error, called.content[0].text

    newest_version, newest_tools = _over_mcp(day, pm, listed, newest=True)
    older_version, older_tools = _over_mcp(day, pm, listed, newest=False)
    assert (newest_version, older_version) == ("2026-07-28", "2025-11-25")
    assert newest_tools == older_tools
    assert "kpc_desk_pnl" in newest_tools
    assert not [name for name in newest_tools if name.startswith("risk_")]

    _, newest_refusal = _over_mcp(day, pm, refused, newest=True)
    _, older_refusal = _over_mcp(day, pm, refused, newest=False)
    assert newest_refusal == older_refusal
    assert newest_refusal[0] is True and "No desk 'macro'" in newest_refusal[1]


# The AI-use report (qaos runs report), for the fund's compliance file


def test_the_ai_use_report_names_each_user_their_refusals_and_approvals(day: Day) -> None:
    pm_equities = _token(day, "pm-equities")
    researcher = _token(day, "researcher")
    risk = _token(day, "risk-manager")
    _call(day, pm_equities, "list_datasets")
    _call(day, pm_equities, "kpc_desk_pnl", desk="eq_ls")
    _call(day, pm_equities, "kpc_desk_pnl", desk="macro")  # a refused desk
    _call(
        day,
        pm_equities,
        "analyze_performance",
        returns={"dataset": "desk_returns_macro", "column": "gross_return"},
    )  # a refused dataset read
    _call(
        day,
        researcher,
        "analyze_performance",
        returns={"dataset": "desk_returns_eq_ls", "column": "gross_return"},
    )
    _call(day, researcher, "kpc_desk_pnl", desk="eq_ls")  # denied outright
    _call(day, risk, "risk_desk_var", desk="macro", method="factor")  # approved in standing
    _call(day, _token(day, "pm-macro"), "kpc_desk_pnl", desk="macro")
    _call(day, _token(day, "operator"), "list_datasets")

    report = _usage(day)
    rows = {row["principal"]: row for row in report["by_principal"]}
    assert set(rows) == {"pm-equities", "pm-macro", "researcher", "risk-manager", "operator"}
    assert report["header"]["calls"] == 9
    assert "desk_returns_macro" in rows["pm-equities"]["datasets_refused"]
    assert "desk_returns_eq_ls" in rows["researcher"]["datasets_refused"]
    assert rows["risk-manager"]["approved_standing"] == 1
    assert rows["risk-manager"]["approved_per_call"] == 0
    refused_reads = {row["dataset"]: row["refused_reads"] for row in report["by_dataset"]}
    assert refused_reads["desk_returns_macro"] == 1
    assert refused_reads["desk_returns_eq_ls"] == 1
    assert any("does not say whether" in note for note in report["notes"])
    # The desk refusals are not counted as refusals: the report's status columns show them as
    # errors, or as denied, which is what the run store recorded for each call.
    assert rows["pm-equities"]["errors"] + rows["pm-equities"]["refused"] >= 2


# Readiness, JSON logs, and the risk service's caller secret, as the fund's deployment sets them


def test_readiness_answers_ready_then_names_the_run_store_once_it_is_closed(
    day: Day, capsys: pytest.CaptureFixture[str]
) -> None:
    ready = _get(day, "/readyz")
    assert ready.status_code == 200 and ready.json() == {"status": "ready"}
    capsys.readouterr()
    SqliteRunStore.close(day.runs[-1])  # the store the server answers from
    down = _get(day, "/readyz")
    assert down.status_code == 503
    assert down.json() == {"status": "not ready", "failed": ["runs"]}
    logged = [
        json.loads(line) for line in capsys.readouterr().err.splitlines() if line.startswith("{")
    ]
    warning = next(item for item in logged if item["message"] == "Readiness check failed: runs")
    assert warning["level"] == "WARNING"
    assert {"time", "level", "logger", "message"} <= warning.keys()


def test_the_risk_service_checks_every_caller_with_the_secret_its_file_holds(
    day: Day, risk_service: str
) -> None:
    assert "KPC_RISK_CALLER_SECRET" not in os.environ  # the secret is named by its file alone
    assert read_secret("KPC_RISK_CALLER_SECRET") == CALLER_SECRET_VALUE
    url = f"{risk_service}/risk_desks"

    def assertion(
        principal: str, groups: set[str], *, tool: str = "risk_desks", key: str = ""
    ) -> str:
        return sign_caller(
            key or CALLER_SECRET_VALUE,
            principal=principal,
            tenant="local",
            groups=groups,
            tool=tool,
        )

    assert httpx2.post(url, json={}).status_code == 401  # no assertion: the platform did not call
    pm = assertion("pm-macro", {"desk-macro"})
    assert httpx2.post(url, json={}, headers={CALLER_HEADER: pm}).status_code == 403
    forged = assertion("risk-manager", {"risk"}, key="x" * 40)
    assert httpx2.post(url, json={}, headers={CALLER_HEADER: forged}).status_code == 401
    other_tool = assertion("risk-manager", {"risk"}, tool="risk_desk_var")
    assert httpx2.post(url, json={}, headers={CALLER_HEADER: other_tool}).status_code == 401
    risk = httpx2.post(url, json={}, headers={CALLER_HEADER: assertion("risk-manager", {"risk"})})
    assert risk.status_code == 200 and risk.json()


# The performance view (decision 0036), on a Kestrel book


def test_analyze_performance_links_a_view_and_its_result_carries_what_the_page_reads(
    day: Day,
) -> None:
    pm = _token(day, "pm-equities")
    arguments = {"returns": {"dataset": "desk_returns_eq_ls", "column": "gross_return"}}

    async def check(session: Any) -> Any:
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        called = await session.call_tool("analyze_performance", arguments)
        view = await session.read_resource("ui://quant-agent-os/performance.html")
        item = view.contents[0]
        return tools["analyze_performance"].meta, called, item.mime_type, item.text

    _, (meta, called, mime, page) = _over_mcp(day, pm, check, newest=True)
    assert meta["ui"]["resourceUri"] == "ui://quant-agent-os/performance.html"
    assert mime == "text/html;profile=mcp-app" and "ui/initialize" in page
    assert called.is_error is False
    data = called.structured_content
    assert data["series_id"].startswith("desk_returns_eq_ls")  # the page's heading shows this id
    for key in ("periods", "periods_per_year", "conventions", "warnings", "evidence"):
        assert key in data, key
    assert {"annualized_return", "annualized_volatility", "sharpe_ratio"} <= set(data["metrics"])
    # A client without MCP Apps gets the same result; it draws no page.
    _, older = _over_mcp(day, pm, check, newest=False)
    assert older[1].structured_content == data


# Round 1 friction: a PM who leaves mid-day, whom the identity provider disables


def test_a_pm_who_leaves_mid_day_keeps_a_cached_token_for_a_minute_and_a_new_one_is_refused(
    day: Day,
) -> None:
    # No other test signs this user in, so disabling them leaves the shared provider usable.
    leaver = "pm-commodities"
    cached = _token(day, leaver)
    assert _call(day, cached, "list_datasets")["status"] == "ok"  # the server now holds the answer
    unused = _token(day, leaver)  # issued before the disable; the server has never seen it
    day.idp.provider.disable_user(leaver)  # the drill's provider needs a restart to do this
    assert _call(day, cached, "list_datasets")["status"] == "ok"  # still accepted, from the cache
    assert _call(day, unused, "list_datasets")["http_status"] == 401  # refused at once
    issued = httpx2.post(
        f"{day.idp.base_url}/token",
        data={"grant_type": "password", "username": leaver, "password": f"kpc-{leaver}"},
    )
    assert issued.status_code == 400
    assert _get(day, "/v1/tools", credential=unused).status_code == 401
    assert oauth.CACHE_SECONDS == 60.0  # the most a disabled user's cached token can last
    # The report shows the calls that got through, under the leaver's name. The two refused
    # attempts, with no credential or an unused token, reached no tool and left no row.
    assert _get(day, "/v1/tools").status_code == 401
    row = _usage(day, leaver)
    assert row["calls"] == 2 and row["ok"] == 2
