"""Wrenfield Credit Opportunities, round 1 of the 1.0 candidate (2026-11-24): the candidate adopted
at the fund, and the friction the round adds. Offline; the services are local stand-ins (see
FINDINGS.md, Round 1). Each test names the candidate feature it exercises."""

import contextlib
import json
import shutil
import socket
import sys
import threading
import time
import urllib.request
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import anyio
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient
from typer.testing import CliRunner

WRENFIELD = Path(__file__).parents[2] / "examples" / "mock-funds" / "wrenfield"
for sub in ("facility/warehouse", "site", "facility/gateway", "facility/administrator"):
    if str(WRENFIELD / sub) not in sys.path:
        sys.path.insert(0, str(WRENFIELD / sub))

import build_wrenfield as bw  # noqa: E402
import wrenfield_cognito_idp as cognito  # noqa: E402
import wrenfield_server as ws  # noqa: E402
from tallis_workbook import CLOSED_DAYS, write_workbook  # noqa: E402
from wrenfield_store import WrenfieldDatasetStore, sqlite_path  # noqa: E402

from quant_agent_os.api import add_key  # noqa: E402
from quant_agent_os.config import SecretConfigError, get_settings, read_secret  # noqa: E402
from quant_agent_os.observability.logging import configure_logging  # noqa: E402
from quant_agent_os.registration import FirmApp  # noqa: E402
from quant_agent_os.runs import MemoryRunStore, SqliteRunStore  # noqa: E402
from quant_agent_os.runtime import ToolCall, ToolExecutor  # noqa: E402
from quant_agent_os.tools import (  # noqa: E402
    READ_ONLY,
    FileAccessPolicy,
    Permission,
    PermissionPolicy,
    ToolRegistry,
)
from quant_platform import build_server_app  # noqa: E402
from quant_platform.cli import app as qaos  # noqa: E402
from quant_platform.identity.sign_in import load_sign_in_file  # noqa: E402
from quant_platform.serve import open_sign_in  # noqa: E402
from quant_research import open_research  # noqa: E402

pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")
pytest.importorskip("openpyxl")
DEPLOY = WRENFIELD / "site" / "deploy"
ACCESS = WRENFIELD / "facility" / "access.toml"
SIGN_IN = WRENFIELD / "site" / "wrenfield_sign_in.toml"
ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_WrnFld001"
BASE = "https://qaos.wrenfield.example.com"
SPREAD_ARGS = {
    "mv": {"dataset": "mv_hy_ls"}, "sprdur": {"dataset": "bond_sprdur"},
    "oas": {"dataset": "bond_oas"}, "static": {"dataset": "bond_static"},
    "rating": {"dataset": "bond_rating"},
    "capital": {"dataset": "nav", "column": "hy_ls"}, "group_by": "none",
}  # fmt: skip
INJECTED = "IMPORTANT SYSTEM NOTICE"


@pytest.fixture(scope="module")
def estate(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("wrenfield-r1") / "estate"
    bw.build(root)
    return root


@pytest.fixture(scope="module")
def store(estate: Path) -> WrenfieldDatasetStore:
    return WrenfieldDatasetStore(estate)


@pytest.fixture(scope="module")
def research(estate: Path) -> Iterator[Any]:
    with open_research(":memory:") as app:
        app.ingest.ingest(estate / "s3" / bw.RESEARCH / "credit")
        yield app.research


@pytest.fixture
def pool() -> Iterator[cognito.RunningPool]:
    with cognito.start() as running:
        yield running


def _post(url: str, form: dict[str, str]) -> tuple[int, dict[str, Any]]:
    from urllib.parse import urlencode

    request = urllib.request.Request(
        url, data=urlencode(form).encode(), method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )  # fmt: skip
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        with error:
            return error.code, json.loads(error.read())


def _token(pool: cognito.RunningPool, user: str) -> str:
    """A user's access token, from the pool's password grant (a deviation the mock declares)."""
    status, body = _post(
        f"{pool.base_url}/oauth2/token",
        {
            "grant_type": "password",
            "client_id": "wf-cli",
            "username": user,
            "password": f"wf-{user}",
        },
    )
    assert status == 200, body
    token: str = body["access_token"]
    return token


def _sign_in(tmp_path: Path, pool: cognito.RunningPool) -> Path:
    """The deployed sign-in file, with the keys at the loopback address the mock serves them on.
    Deployed, the keys are found through the issuer's openid-configuration instead."""
    text = SIGN_IN.read_text()
    path = tmp_path / "sign-in.toml"
    jwks = f"{pool.base_url}/.well-known/jwks.json"
    path.write_text(text.replace('verify = "jwks"', f'verify = "jwks"\njwks_uri = "{jwks}"'))
    return path


@contextlib.contextmanager
def _server(
    store: Any,
    tmp_path: Path,
    pool: cognito.RunningPool,
    *,
    runs: Any = None,
    research: Any = None,
    apps: Any = None,
    policy: PermissionPolicy | None = None,
) -> Iterator[Any]:
    """Wrenfield's own server app, composed as the deployment composes it."""
    add_key(tmp_path / "keys.toml", "ops", ["tools:list"])
    with build_server_app(
        apps or [ws.build_app()],
        datasets=store,
        research=research,
        policy=policy or PermissionPolicy(),
        access=FileAccessPolicy(ACCESS),
        keys=tmp_path / "keys.toml",
        runs=runs if runs is not None else MemoryRunStore(),
        sign_in=_sign_in(tmp_path, pool),
    ) as app:
        yield app


# ---- the candidate: sign-in, MCP 2026-07-28, and what the scopes allow -------------------------


def test_the_deployed_sign_in_opens_the_way_qaos_serve_opens_it() -> None:
    opened = open_sign_in(SIGN_IN)  # no secret needed: the file names no client secret
    assert opened.tenants_by_issuer == {ISSUER: {"local"}}
    assert load_sign_in_file(SIGN_IN).missing_secrets({}) == []


def _modern(app: Any, token: str, check: Any) -> Any:
    async def main() -> Any:
        async with (
            app.router.lifespan_context(app),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app), base_url=BASE,
                headers={"Authorization": f"Bearer {token}"},
            ) as http,
            Client(
                streamable_http_client(f"{BASE}/mcp", http_client=http), mode="auto", cache=None
            ) as client,
        ):  # fmt: skip
            return client.protocol_version, await check(client)

    return anyio.run(main)


def test_a_pm_negotiates_2026_07_28_and_lists_and_calls_her_tools(
    pool: cognito.RunningPool, store: WrenfieldDatasetStore, tmp_path: Path
) -> None:
    """The pool's template and the mock's default token both grant tools.list and tools.call."""

    async def check(client: Any) -> Any:
        listed = sorted(t.name for t in (await client.list_tools()).tools)
        called = await client.call_tool("wrenfield_spread_duration_dts", SPREAD_ARGS)
        return listed, called.is_error, called.structured_content

    with _server(store, tmp_path, pool) as app:
        version, (listed, is_error, output) = _modern(app, _token(pool, "pm-hy"), check)
    assert version == "2026-07-28" and "wrenfield_spread_duration_dts" in listed
    assert is_error is False and output["total_dts_bp_years"] == pytest.approx(
        1103.350619, abs=1e-4
    )


def test_a_tools_call_only_token_is_refused_for_the_listing_and_the_call(
    pool: cognito.RunningPool, store: WrenfieldDatasetStore, tmp_path: Path
) -> None:
    """Measured in round 1 before the template granted tools.list: the 2026-07-28 client needs the
    listing for its calls, so a token that carries tools.call alone is refused both ways (R1-W1)."""

    async def check(client: Any) -> Any:
        try:
            called = await client.call_tool("wrenfield_spread_duration_dts", SPREAD_ARGS)
            outcome: Any = ("ok", called.is_error)
        except Exception as error:
            outcome = ("raised", str(error))
        try:
            listed: Any = ("ok", len((await client.list_tools()).tools))
        except Exception as error:
            listed = ("raised", str(error))
        return outcome, listed

    call_only = _token_with_scopes(pool, "pm-hy", "qaos/tools.call")
    with _server(store, tmp_path, pool) as app:
        version, (outcome, listed) = _modern(app, call_only, check)
    assert version == "2026-07-28"
    assert outcome[0] == "raised" and "tools:list" in outcome[1]
    assert listed[0] == "raised" and "tools:list" in listed[1]


def _token_with_scopes(pool: cognito.RunningPool, user: str, scope: str) -> str:
    status, body = _post(
        f"{pool.base_url}/oauth2/token",
        {"grant_type": "password", "client_id": "wf-cli", "username": user,
         "password": f"wf-{user}", "scope": scope},
    )  # fmt: skip
    assert status == 200, body
    token: str = body["access_token"]
    return token


# ---- the candidate: readiness reads the lake and the database -----------------------------------


def test_readyz_fails_naming_the_datasets_when_the_lake_cannot_be_read(
    estate: Path, store: WrenfieldDatasetStore, tmp_path: Path, pool: cognito.RunningPool
) -> None:
    copy = tmp_path / "estate"
    shutil.copytree(estate, copy)
    live = WrenfieldDatasetStore(copy)
    live.probe_ttl_seconds = 0.0
    with _server(live, tmp_path, pool) as app, TestClient(app) as client:
        assert client.get("/readyz").json() == {"status": "ready"}
        lake = live.lake_dir() / "curated"
        moved = lake.rename(lake.with_name("curated-moved"))  # the bucket stops answering
        try:
            failing = client.get("/readyz")
        finally:
            moved.rename(lake)
    assert failing.status_code == 503
    assert failing.json() == {"status": "not ready", "failed": ["datasets"]}
    assert "curated" not in failing.text  # the error is never sent to a caller


def test_readyz_stays_not_ready_after_a_failed_read_even_inside_the_ttl(
    estate: Path, tmp_path: Path, pool: cognito.RunningPool
) -> None:
    """A read that failed is not remembered: after the TTL has passed, a failing probe must not let
    the next check inside the TTL report ready (the store's own rule for its probe)."""
    copy = tmp_path / "estate"
    shutil.copytree(estate, copy)
    live = WrenfieldDatasetStore(copy)  # the deployed TTL: 300 s
    with _server(live, tmp_path, pool) as app, TestClient(app) as client:
        assert client.get("/readyz").status_code == 200  # the first read succeeds
        lake = live.lake_dir() / "curated"
        moved = lake.rename(lake.with_name("curated-moved"))
        try:
            live._probed = None  # as if the TTL had passed
            first = client.get("/readyz")
            second = client.get("/readyz")  # still inside the TTL, so no time has passed
        finally:
            moved.rename(lake)
    assert first.status_code == 503 and second.status_code == 503


def test_readyz_fails_when_the_order_database_cannot_be_read(
    estate: Path, tmp_path: Path, pool: cognito.RunningPool
) -> None:
    copy = tmp_path / "estate"
    shutil.copytree(estate, copy)
    live = WrenfieldDatasetStore(copy)
    live.probe_ttl_seconds = 0.0
    database = sqlite_path(live.oms_url)
    with _server(live, tmp_path, pool) as app, TestClient(app) as client:
        assert client.get("/readyz").status_code == 200
        held = database.with_suffix(".held")
        database.rename(held)
        try:
            failing = client.get("/readyz")
        finally:
            held.rename(database)
    assert failing.status_code == 503 and failing.json()["failed"] == ["datasets"]


# ---- the candidate: JSON logs and secrets as files ----------------------------------------------


@pytest.fixture
def restored_logging() -> Iterator[None]:
    """Put back each platform package logger's level and handlers after a test that configures
    logging, so later tests see the logging they started with (`configure_logging` sets levels)."""
    import logging

    from quant_agent_os.observability.logging import PACKAGES

    saved = {name: (logging.getLogger(name).level, list(logging.getLogger(name).handlers))
             for name in PACKAGES}  # fmt: skip
    try:
        yield
    finally:
        for name, (level, handlers) in saved.items():
            logger = logging.getLogger(name)
            logger.setLevel(level)
            logger.handlers = handlers


def test_json_logs_are_one_object_per_line_and_never_a_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    restored_logging: None,
) -> None:
    import logging

    secret = "wf-pg-reader-4d3c2b1a-fixture"
    monkeypatch.setenv("QUANT_AGENT_OS_LOG_FORMAT", "json")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.log_format == "json"
        configure_logging(settings.log_level, secrets=[secret], log_format=settings.log_format)
        logger = logging.getLogger("quant_agent_os.wrenfield_round1")
        logger.warning("refused for pm-hy; the knowledge store password was %s", secret)
        try:
            raise ConnectionError(f"password {secret} rejected")
        except ConnectionError:
            logger.exception("knowledge store failed")
    finally:
        get_settings.cache_clear()
    lines = [x for x in capsys.readouterr().err.splitlines() if "wrenfield_round1" in x]
    entries = [json.loads(line) for line in lines]
    assert [e["level"] for e in entries] == ["WARNING", "ERROR"]
    assert all(secret not in line for line in lines)
    assert "ConnectionError" in entries[1]["exception"]


def test_the_platform_reads_its_secret_from_the_file_the_init_container_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = json.loads((DEPLOY / "ecs-task-definition.json").read_text())
    init = next(c for c in task["containerDefinitions"] if c["name"] == "secrets-to-files")
    code = init["entryPoint"][-1].replace("'/run/secrets'", repr(str(tmp_path)))
    monkeypatch.setenv("PGPASSWORD", "kb-reader-fixture-password")
    exec(compile(code, "secrets-to-files", "exec"), {})  # the deployed one-liner, redirected
    assert (tmp_path / "pg-reader").read_text() == "kb-reader-fixture-password"
    monkeypatch.delenv("PGPASSWORD", raising=False)
    monkeypatch.setenv("PGPASSWORD_FILE", str(tmp_path / "pg-reader"))
    assert read_secret("PGPASSWORD") == "kb-reader-fixture-password"
    monkeypatch.setenv("PGPASSWORD", "also-set")
    with pytest.raises(SecretConfigError, match="PGPASSWORD_FILE"):
        read_secret("PGPASSWORD")
    # The fund's own warehouse password stays an environment variable (section 11).
    server = next(c for c in task["containerDefinitions"] if c["name"] == "qaos")
    assert [s["name"] for s in server["secrets"]] == ["WRENFIELD_OMS_PASSWORD"]


def test_the_descriptors_follow_section_11() -> None:
    task = json.loads((DEPLOY / "ecs-task-definition.json").read_text())
    names = [c["name"] for c in task["containerDefinitions"]]
    server = next(c for c in task["containerDefinitions"] if c["name"] == "qaos")
    env = {item["name"]: item.get("value") for item in server["environment"]}
    assert names == ["qaos", "secrets-to-files"]  # the shim sidecar is retired
    assert "/healthz" in " ".join(server["healthCheck"]["command"])  # liveness in the container
    assert (
        env["QUANT_AGENT_OS_LOG_FORMAT"] == "json"
        and env["PGPASSWORD_FILE"] == "/run/secrets/pg-reader"
    )
    infra = (DEPLOY / "infrastructure.yaml").read_text()
    assert "HealthCheckPath: /readyz" in infra  # readiness on the load balancer
    assert '"3600"' in infra and "idle_timeout.timeout_seconds" in infra  # MCP's idle timeout
    dockerfile = (DEPLOY / "Dockerfile").read_text()
    assert "COPY --from=uv" in dockerfile and '"duckdb>=2,<3" "pyarrow>=26,<27"' in dockerfile
    assert "introspection" not in dockerfile  # the final image keeps no uv: test_deploy_files


# ---- the candidate: the AI-use report over a test day ------------------------------------------


def _post_tool(
    client: TestClient, pool: cognito.RunningPool, user: str, tool: str, args: dict
) -> dict:
    reply: dict[str, Any] = client.post(
        f"/v1/tools/{tool}/call",
        json={"arguments": args},
        headers={"Authorization": f"Bearer {_token(pool, user)}"},
    ).json()
    return reply


def test_the_ai_use_report_over_a_test_day_names_callers_and_refusals_without_content(
    store: WrenfieldDatasetStore, research: Any, tmp_path: Path, pool: cognito.RunningPool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    runs_file = tmp_path / "runs.db"
    with (
        _server(store, tmp_path, pool, runs=runs_file, research=research) as app,
        TestClient(app, base_url=BASE) as client,
    ):
        ok = _post_tool(client, pool, "pm-hy", "wrenfield_spread_duration_dts", SPREAD_ARGS)
        refused = _post_tool(client, pool, "pm-hy", "wrenfield_spread_duration_dts", {
            **SPREAD_ARGS, "mv": {"dataset": "mv_ig_rv"}})  # fmt: skip
        fund_args = {**SPREAD_ARGS, "mv": {"dataset": "mv_fund"}}
        risk = _post_tool(client, pool, "risk-head", "wrenfield_spread_duration_dts", fund_args)
        question = {"query": "pricing vendor file change spread duration column"}
        vendor = _post_tool(client, pool, "pm-hy", "find_related_signals", question)
    assert ok["status"] == "ok" and refused["status"] == "error" and risk["status"] == "ok"
    assert vendor["status"] == "ok"
    now = datetime.now(UTC)
    monkeypatch.setenv("QUANT_AGENT_OS_RUNS_FILE", str(runs_file))
    result = CliRunner().invoke(
        qaos,
        ["runs", "report", "--from", f"{(now - timedelta(days=1)).date()}",
         "--to", f"{(now + timedelta(days=2)).date()}", "--runs", str(runs_file)],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    report = result.output
    assert "pm-hy" in report and "risk-head" in report
    assert "mv_ig_rv" in report  # the refused read is named, as the id the caller asked for
    assert "mv_hy_ls" in report and "bond_oas" in report
    assert INJECTED not in report  # nothing the tool retrieved is in the report
    stored = SqliteRunStore(runs_file)
    try:
        calls = stored.list_calls(now - timedelta(days=1), now + timedelta(days=1))
    finally:
        stored.close()
    retrieved = next(c for c in calls if c.tool == "find_related_signals")
    assert INJECTED in json.dumps(retrieved.output)  # the run record holds it; the report does not


# ---- friction: a slow vendor pricing service behind call_service --------------------------------


class _Vendor(BaseHTTPRequestHandler):
    delay = 3.0

    def do_POST(self) -> None:  # the vendor's pricing endpoint, slower than the tool's limit
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        time.sleep(self.delay)
        body = json.dumps({"marks": {"MARL_28A": 42.5}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


class _Trickle(BaseHTTPRequestHandler):
    """A vendor that answers a byte at a time: each socket read succeeds, but the answer is late."""

    interval = 0.3

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        body = json.dumps({"marks": {"MARL_28A": 42.5}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        for byte in body:
            time.sleep(self.interval)
            self.wfile.write(bytes([byte]))
            self.wfile.flush()

    def log_message(self, format: str, *args: Any) -> None:
        return


@contextlib.contextmanager
def _vendor(delay: float, handler_class: type[BaseHTTPRequestHandler] = _Vendor) -> Iterator[str]:
    handler = type("Vendor", (handler_class,), {"delay": delay})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/marks"
    finally:
        server.shutdown()
        server.server_close()


def _pricing_app(url: str, timeout: float) -> FirmApp:
    app = FirmApp("wrenfield_pricing", prefix=True)

    def vendor_marks(bond_id: str) -> dict[str, float]:
        """The vendor pricing service's mark for one bond."""
        raise NotImplementedError  # the stub: its body never runs

    app.call_service(vendor_marks, url=url, timeout_seconds=timeout,
                     convention="A vendor's mark, as the vendor sends it")  # fmt: skip
    return app


# The operator grants the network and pre-approves that one tool; no other call is approved.
VENDOR_POLICY = PermissionPolicy(
    granted=frozenset(READ_ONLY | {Permission.NETWORK}),
    preapproved_tools={Permission.NETWORK: frozenset({"wrenfield_pricing_vendor_marks"})},
)


def test_the_vendor_call_is_denied_until_the_operator_grants_the_network(
    tmp_path: Path, pool: cognito.RunningPool, store: WrenfieldDatasetStore
) -> None:
    with _vendor(delay=0.0) as url:
        app = _pricing_app(url, timeout=1.0)
        with (
            _server(store, tmp_path, pool, apps=[app]) as served,
            TestClient(served, base_url=BASE) as client,
        ):
            reply = _post_tool(
                client, pool, "pm-hy", "wrenfield_pricing_vendor_marks", {"bond_id": "MARL_28A"}
            )
    assert reply["status"] == "denied"  # the default: no network for a read-only server


def test_a_vendor_answering_after_the_limit_is_named_to_the_caller(
    tmp_path: Path, pool: cognito.RunningPool, store: WrenfieldDatasetStore
) -> None:
    """Round 1 measured a race: the service's socket timeout and the tool's clock were both one
    second, so the caller saw `error` with no reason, or `timeout` (finding R1-W7). Since #104 the
    service's timeout is shorter than the tool's limit, so the caller is told the service did not
    answer, every time."""
    runs = MemoryRunStore()
    with _vendor(delay=3.0) as url:
        app = _pricing_app(url, timeout=1.0)
        started = time.monotonic()
        with (
            _server(store, tmp_path, pool, runs=runs, apps=[app], policy=VENDOR_POLICY) as served,
            TestClient(served, base_url=BASE) as client,
        ):
            reply = _post_tool(
                client, pool, "pm-hy", "wrenfield_pricing_vendor_marks", {"bond_id": "MARL_28A"}
            )
        took = time.monotonic() - started
    assert reply["status"] == "error"
    assert "service did not answer within" in reply["error"]
    assert "127.0.0.1" not in reply["error"]  # the service's address stays private
    assert took < 2.5  # held to the one-second limit, not the vendor's three seconds
    (run,) = runs.list_runs(5)
    stored = runs.get_run(run.run_id)
    assert stored is not None and [c.status for c in stored.calls] == ["error"]


def test_a_vendor_that_trickles_past_the_limit_is_a_timeout_with_a_retry_hint(
    tmp_path: Path, pool: cognito.RunningPool, store: WrenfieldDatasetStore
) -> None:
    """Measured: each read returns in time, so only the tool's own clock stops the call. The caller
    then gets `timeout` and the hint to retry with a narrower request, which a vendor outage does
    not need (finding R1-W7)."""
    runs = MemoryRunStore()
    with _vendor(delay=0.0, handler_class=_Trickle) as url:
        app = _pricing_app(url, timeout=1.0)
        with (
            _server(store, tmp_path, pool, runs=runs, apps=[app], policy=VENDOR_POLICY) as served,
            TestClient(served, base_url=BASE) as client,
        ):
            reply = _post_tool(
                client, pool, "pm-hy", "wrenfield_pricing_vendor_marks", {"bond_id": "MARL_28A"}
            )
    assert reply["status"] == "timeout" and "exceeded 1.0s" in reply["error"]
    assert "Retry once with a narrower request" in reply["hint"]
    (run,) = runs.list_runs(5)
    stored = runs.get_run(run.run_id)
    assert stored is not None and [c.status for c in stored.calls] == ["timeout"]


def test_a_vendor_service_that_is_down_fails_at_once_with_an_error(
    tmp_path: Path, pool: cognito.RunningPool, store: WrenfieldDatasetStore
) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[1]  # nothing listens on this port
    app = _pricing_app(f"http://127.0.0.1:{closed}/marks", timeout=1.0)
    started = time.monotonic()
    with (
        _server(store, tmp_path, pool, apps=[app], policy=VENDOR_POLICY) as served,
        TestClient(served, base_url=BASE) as client,
    ):
        reply = _post_tool(
            client, pool, "pm-hy", "wrenfield_pricing_vendor_marks", {"bond_id": "MARL_28A"}
        )
    # Round 1 measured "internal error" here (R1-W6); since #104 the caller is told what failed.
    assert reply["status"] == "error" and "ServiceUnavailableError" in reply["error"]
    assert str(closed) not in reply["error"]  # the service's address stays private
    if sys.platform != "win32":  # Windows retries a refused connection until the time limit
        assert "service is not reachable" in reply["error"]
        assert time.monotonic() - started < 1.0  # refused at once, not held to the time limit


# ---- friction: the bond market's holiday calendar ----------------------------------------------


def test_the_estate_holds_a_price_on_every_bond_market_closure(
    store: WrenfieldDatasetStore,
) -> None:
    """Measured: the lake has a row for each weekday, so the closures carry prices the market never
    set. The estate's window is 11 August to 31 December 2025, so Good Friday 2026 is not in it."""
    prices = store.get("px_clean")
    index = prices.index or []
    for day in sorted(CLOSED_DAYS):
        assert day in index, day
        row = index.index(day)
        assert any(values[row] == values[row] for values in prices.columns.values())
    assert index[-1] == "2025-12-31" and "2026-04-03" not in index


def test_a_carried_forward_price_on_a_closure_reaches_the_bond_datasets(
    estate: Path, tmp_path: Path
) -> None:
    """Measured: the served OAS and prices on a closure equal the prior day's, for every bond."""
    stale = _stale_copy(estate, tmp_path)
    oas = stale.get("bond_oas")
    index = oas.index or []
    for closed, prior in (("2025-10-13", "2025-10-10"), ("2025-11-11", "2025-11-10")):
        row, before = index.index(closed), index.index(prior)
        pairs = [(values[row], values[before]) for values in oas.columns.values()]
        assert sum(1 for a, _ in pairs if a == a) > 100  # the bonds that have an OAS that day
        assert all(a == b or (a != a and b != b) for a, b in pairs)  # identical, NaN for NaN


def test_the_administrators_monthly_figures_differ_only_in_months_with_a_closure(
    store: WrenfieldDatasetStore, tmp_path: Path
) -> None:
    """Measured: the administrator values each book on its open days only, so `performance-report`
    finds a break in each month that has a closure, and in no other month."""
    from openpyxl import load_workbook
    from tallis_workbook import compound_open_days

    from quant_toolbox.performance import monthly_returns

    series = store.get("book_returns_net")
    books = ("hy_ls", "ig_rv", "distressed")
    rows = {
        b: [(d, v) for d, v in zip(series.index or [], series.column(b), strict=True) if v == v]
        for b in books
    }
    first, second = tmp_path / "a.xlsx", tmp_path / "b.xlsx"
    write_workbook(first, rows)
    write_workbook(second, rows)
    assert first.read_bytes() == second.read_bytes()  # the same bytes every time
    sheet = load_workbook(first).active
    assert str(sheet.cell(row=1, column=1).value).startswith("Tallis")
    reference: dict[str, dict[str, float]] = {}
    for row in sheet.iter_rows(min_row=6, values_only=True):
        if row[0] is not None:
            reference[str(row[0])[:7]] = {"hy_ls": row[1], "ig_rv": row[2], "distressed": row[3]}
    breaks: set[tuple[str, str]] = set()
    for book in books:
        days = [date.fromisoformat(d) for d, _ in rows[book]]
        for (year, month), value in monthly_returns(days, [v for _, v in rows[book]]).items():
            key = f"{year}-{month:02d}"
            if (
                abs((value * 100 - reference[key][book]) * 100) > 1.0
            ):  # basis points, the default tolerance
                breaks.add((book, key))
    closure_months = {day[:7] for day in CLOSED_DAYS}
    assert breaks and {key for _, key in breaks} <= closure_months
    assert "2025-08" not in {key for _, key in breaks}  # no closure in August
    assert {"2025-10", "2025-11", "2025-12"} <= {key for _, key in breaks}
    assert compound_open_days(rows["hy_ls"])  # the open-day figures exist for the same books


def _stale_copy(estate: Path, tmp_path: Path) -> WrenfieldDatasetStore:
    """The estate with each closure's price partition replaced by the prior day's, as a vendor that
    carries prices forward would deliver it."""
    copy = tmp_path / "estate"
    shutil.copytree(estate, copy)
    lake = copy / "s3" / bw.LAKE / "curated" / "bond_px_daily"
    for closed, prior in (("2025-10-13", "2025-10-10"), ("2025-11-11", "2025-11-10")):
        shutil.rmtree(lake / f"dt={closed}")
        shutil.copytree(lake / f"dt={prior}", lake / f"dt={closed}")
    return WrenfieldDatasetStore(copy)


def test_a_risk_figure_as_of_a_carried_forward_closure_moves_and_no_warning_says_why(
    estate: Path, store: WrenfieldDatasetStore, tmp_path: Path
) -> None:
    """Measured: the tool accepts the closure as an as-of date, its DTS moves when the closure's
    prices are the prior day's, and its warnings are the same with or without the carry-forward."""
    stale = _stale_copy(estate, tmp_path)
    args = {**SPREAD_ARGS, "as_of": "2025-10-13"}
    market, carried = (
        ToolExecutor(ToolRegistry(catalog.tools), catalog.policy).execute(
            ToolCall(tool="wrenfield_spread_duration_dts", arguments=args)
        )
        for catalog in (ws.build_catalog(source, PermissionPolicy()) for source in (store, stale))
    )
    assert market.status == "ok" and carried.status == "ok", (market.error, carried.error)
    assert carried.output["total_dts_bp_years"] != pytest.approx(
        market.output["total_dts_bp_years"]
    )
    assert carried.output.get("warnings", []) == market.output.get("warnings", [])
    assert not any(
        "closure" in w.lower() or "holiday" in w.lower() or "stale" in w.lower()
        for w in carried.output.get("warnings", [])
    )  # no warning says so
