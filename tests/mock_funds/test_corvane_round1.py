"""Corvane Equity Partners, round 1 of the 1.0 candidate (2026-11-24): the candidate adopted at the
fund, and the friction the round adds. Offline; the services are local stand-ins (see FINDINGS.md,
Round 1). Each test names the candidate feature it exercises."""

import contextlib
import json
import sys
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient
from typer.testing import CliRunner

ROOT = Path(__file__).parents[2] / "examples" / "mock-funds" / "corvane"
for sub in ("facility/warehouse", "facility/gateway", "facility/administrator", "site"):
    if str(ROOT / sub) not in sys.path:
        sys.path.insert(0, str(ROOT / sub))

import cognito_idp  # noqa: E402
import corvane_server  # noqa: E402
from build_corvane import build  # noqa: E402
from corvane_store import CorvaneDatasetStore, connect  # noqa: E402
from halvorsen_workbook import (  # noqa: E402  # noqa: E402
    SPIN_OFF_ADJUSTMENT_BP,
    SPIN_OFF_MONTH,
    write_workbook,
)

from quant_agent_os.api import add_key  # noqa: E402
from quant_agent_os.config import SecretConfigError, get_settings, read_secret  # noqa: E402
from quant_agent_os.observability.logging import configure_logging  # noqa: E402
from quant_agent_os.runs import MemoryRunStore, SqliteRunStore  # noqa: E402
from quant_agent_os.tools import FileAccessPolicy, PermissionPolicy  # noqa: E402
from quant_platform import build_server_app  # noqa: E402
from quant_platform.cli import app as qaos  # noqa: E402
from quant_research import open_research  # noqa: E402

pytest.importorskip("empyrical")
pytest.importorskip("openpyxl")
DEPLOY = ROOT / "site" / "deploy"
ACCESS = ROOT / "facility" / "access.toml"
LCC, GQ = {"dataset": "weights_lcc"}, {"dataset": "weights_gq"}
US, GLOBAL = {"dataset": "bench_w_bm_us300"}, {"dataset": "bench_w_bm_global200"}
RET = {"dataset": "px_ret_usd"}
SECTOR = {"dataset": "ref_security", "column": "sector_code"}
SPIN_OFF = "US0301"  # the tobacco spin-off: held by the two no-tobacco accounts from March 2026


@pytest.fixture(scope="module")
def estate(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("corvane-r1") / "estate"
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
def research(estate: Path) -> Iterator[Any]:
    with open_research(":memory:") as app:
        app.ingest.ingest(estate / "s3" / "corvane-research" / "research")
        yield app.research


@pytest.fixture
def idp() -> Iterator[cognito_idp.RunningIdp]:
    running = cognito_idp.start()
    try:
        yield running
    finally:
        running.stop()


def _sign_in(tmp_path: Path, idp: cognito_idp.RunningIdp) -> Path:
    """The deployed sign-in file, with the keys at the loopback address the mock serves them on.
    Deployed, the keys are found through the issuer's openid-configuration instead."""
    text = (DEPLOY / "sign-in.toml").read_text()
    path = tmp_path / "sign-in.toml"
    jwks = f"{idp.url}/.well-known/jwks.json"
    path.write_text(text.replace('verify = "jwks"', f'verify = "jwks"\njwks_uri = "{jwks}"'))
    return path


@contextlib.contextmanager
def _server(
    store: Any,
    tmp_path: Path,
    idp: cognito_idp.RunningIdp,
    *,
    runs: Any = None,
    research: Any = None,
) -> Iterator[Any]:
    """Corvane's own server app as the deployment composes it, with its sign-in and access."""
    add_key(tmp_path / "keys.toml", "ops", ["tools:list"])
    with build_server_app(
        [corvane_server.build_app()],
        datasets=store,
        research=research,
        policy=PermissionPolicy(),
        access=FileAccessPolicy(ACCESS),
        keys=tmp_path / "keys.toml",
        runs=runs if runs is not None else MemoryRunStore(),
        sign_in=_sign_in(tmp_path, idp),
    ) as app:
        yield app


# ---- the candidate: MCP 2026-07-28 for a signed-in user ----------------------------------------


def _modern(app: Any, user_token: str, check: Any) -> Any:
    base = "https://qaos.corvane.example.com"

    async def main() -> Any:
        async with (
            app.router.lifespan_context(app),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app), base_url=base,
                headers={"Authorization": f"Bearer {user_token}"},
            ) as http,
            Client(
                streamable_http_client(f"{base}/mcp", http_client=http), mode="auto", cache=None
            ) as client,
        ):  # fmt: skip
            return client.protocol_version, await check(client)

    return anyio.run(main)


def test_a_cognito_user_negotiates_2026_07_28_and_gets_their_own_tools(
    store: CorvaneDatasetStore, research: Any, tmp_path: Path, idp: cognito_idp.RunningIdp
) -> None:
    async def list_tools(client: Any) -> list[str]:
        return sorted(tool.name for tool in (await client.list_tools()).tools)

    with _server(store, tmp_path, idp, research=research) as app:
        pm_version, pm_tools = _modern(app, idp.pool.access_token("priya"), list_tools)
        client_version, client_tools = _modern(app, idp.pool.access_token("claire"), list_tools)
    assert pm_version == client_version == "2026-07-28"
    assert {"corvane_active_share", "analyze_performance", "find_related_signals"} <= set(pm_tools)
    # Client service sees its client's tools and no more (access.toml's group, not the pool's).
    assert set(client_tools) == {
        "analyze_performance",
        "corvane_tracking_error",
        "list_datasets",
        "server_info",
    }


def test_a_2026_client_gets_the_figures_and_the_view_link_for_a_benchmark_comparison(
    store: CorvaneDatasetStore, tmp_path: Path, idp: cognito_idp.RunningIdp, known: dict[str, Any]
) -> None:
    async def check(client: Any) -> Any:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        called = await client.call_tool(
            "analyze_performance",
            {"returns": {"dataset": "perf_lcc", "column": "net"},
             "benchmark": {"dataset": "bench_ret", "column": "BM_US300"}},
        )  # fmt: skip
        page = await client.read_resource("ui://quant-agent-os/performance.html")  # type: ignore[arg-type]
        return tools["analyze_performance"].meta, called, page.contents[0].text

    with _server(store, tmp_path, idp) as app:
        _, (meta, called, html) = _modern(app, idp.pool.access_token("priya"), check)
    assert meta["ui"]["resourceUri"] == "ui://quant-agent-os/performance.html"
    assert "Performance" in html and called.is_error is False
    metrics = called.structured_content["metrics"]
    for name in ("active_return", "tracking_error", "information_ratio", "up_capture", "beta"):
        assert name in metrics, name
    # The information ratio is the annualised active return over its tracking error.
    assert metrics["information_ratio"] == pytest.approx(
        metrics["active_return"] / metrics["tracking_error"], rel=1e-9
    )
    assert called.structured_content["periods"] == 109
    assert "attribution" not in json.dumps(called.structured_content).lower()  # measured: none


# ---- the candidate: the AI-use report over a test day ------------------------------------------


def _rest(
    client: TestClient, idp: cognito_idp.RunningIdp, user: str, tool: str, args: dict
) -> dict:
    reply: dict[str, Any] = client.post(
        f"/v1/tools/{tool}/call",
        json={"arguments": args},
        headers={"Authorization": f"Bearer {idp.pool.access_token(user)}"},
    ).json()
    return reply


def test_the_ai_use_report_over_a_test_day_names_callers_and_refusals_and_no_content(
    store: CorvaneDatasetStore,
    research: Any,
    tmp_path: Path,
    idp: cognito_idp.RunningIdp,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs_file = tmp_path / "runs.db"
    with (
        _server(store, tmp_path, idp, runs=runs_file, research=research) as app,
        TestClient(app, base_url="https://qaos.corvane.example.com") as client,
    ):
        pm = _rest(client, idp, "priya", "analyze_performance", {
            "returns": {"dataset": "perf_lcc", "column": "net"},
            "benchmark": {"dataset": "bench_ret", "column": "BM_US300"}})  # fmt: skip
        found = _rest(
            client, idp, "priya", "find_related_signals", {"query": "spin-off tobacco mandates"}
        )
        refused = _rest(client, idp, "claire", "analyze_performance", {
            "returns": {"dataset": "perf_lcc", "column": "net"}})  # fmt: skip
        own = _rest(client, idp, "claire", "analyze_performance", {
            "returns": {"dataset": "perf_sma_pen_a", "column": "net"}})  # fmt: skip
        analyst = _rest(client, idp, "ines", "analyze_risk", {
            "portfolio_id": "x", "weights_ref": LCC, "asset_returns": RET})  # fmt: skip
    assert pm["status"] == "ok" and found["status"] == "ok" and own["status"] == "ok"
    assert refused["status"] == "error" and analyst["status"] == "error"
    now = datetime.now(UTC)
    monkeypatch.setenv("QUANT_AGENT_OS_RUNS_FILE", str(runs_file))
    result = CliRunner().invoke(
        qaos,
        ["runs", "report", "--from", f"{now.date() - timedelta(days=1)}",
         "--to", f"{now.date() + timedelta(days=2)}", "--runs", str(runs_file)],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    report = result.output
    for name in ("priya", "claire", "ines"):
        assert name in report
    assert "perf_lcc" in report and "weights_lcc" in report  # the refused reads are named
    # The report holds no content: none of the figures it was computed from appears in it.
    assert str(pm["output"]["metrics"]["tracking_error"])[:6] not in report
    # What the compliance officer still misses: the research document the question retrieved.
    assert "minutes-investment-committee" not in report
    stored = SqliteRunStore(runs_file)
    try:
        calls = stored.list_calls(now - timedelta(days=1), now + timedelta(days=1))
    finally:
        stored.close()
    retrieved = [c for c in calls if c.tool == "find_related_signals"]
    assert retrieved and "minutes-investment-committee" in json.dumps(retrieved[0].output)


# ---- the candidate: readiness, JSON logs, and secrets as files ---------------------------------


class _BrokenRuns(MemoryRunStore):
    def list_runs(self, limit: int = 20, *, tenant: str | None = None) -> Any:
        raise RuntimeError("sqlite3.OperationalError: unable to open database /data/runs/runs.db")


def test_readyz_answers_ready_and_names_the_store_that_fails(
    estate: Path, tmp_path: Path, idp: cognito_idp.RunningIdp
) -> None:
    live = CorvaneDatasetStore(connect({"CORVANE_SQL_URL": f"estate://{estate}"}), ttl_seconds=0)
    with _server(live, tmp_path, idp) as app, TestClient(app) as client:
        ready = client.get("/readyz")
        assert ready.status_code == 200 and ready.json() == {"status": "ready"}
        connection = live.connection
        original = connection.fetch

        def down(sql: str, params: Any = ()) -> Any:
            raise RuntimeError("snowflake: connection to CORVANE_WH_XS refused (account xy12345)")

        connection.fetch = down  # the warehouse stops answering after start-up
        try:
            failing = client.get("/readyz")
        finally:
            connection.fetch = original
    assert failing.status_code == 503
    assert failing.json() == {"status": "not ready", "failed": ["datasets"]}
    assert "snowflake" not in failing.text and "xy12345" not in failing.text  # errors never sent


def test_readyz_names_the_run_store_when_it_cannot_be_read(
    store: CorvaneDatasetStore, tmp_path: Path, idp: cognito_idp.RunningIdp
) -> None:
    with _server(store, tmp_path, idp, runs=_BrokenRuns()) as app, TestClient(app) as client:
        failing = client.get("/readyz")
    assert failing.status_code == 503 and failing.json()["failed"] == ["runs"]
    assert "sqlite3" not in failing.text


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


def test_json_logs_hold_one_object_per_line_and_never_a_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    restored_logging: None,
) -> None:
    secret = "corvane-pg-reader-9f8e7d6c5b4a"
    monkeypatch.setenv("QUANT_AGENT_OS_LOG_FORMAT", "json")
    monkeypatch.setenv("QUANT_AGENT_OS_LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.log_format == "json"  # the descriptor's value
        configure_logging(settings.log_level, secrets=[secret], log_format=settings.log_format)
        import logging

        logger = logging.getLogger("quant_agent_os.corvane_round1")
        logger.warning("refused for priya; the password was %s", secret)
        try:
            raise ValueError(f"connect failed with {secret}")
        except ValueError:
            logger.exception("store failed")
    finally:
        get_settings.cache_clear()
    lines = [line for line in capsys.readouterr().err.splitlines() if "corvane_round1" in line]
    entries = [json.loads(line) for line in lines]  # every line is one JSON object
    assert [e["level"] for e in entries] == ["WARNING", "ERROR"]
    assert {"time", "level", "logger", "message"} <= set(entries[0])
    assert all(secret not in line for line in lines)
    assert "exception" in entries[1] and "ValueError" in entries[1]["exception"]


def test_the_platform_secret_is_read_from_the_file_the_init_container_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Section 11's init container: the deployed entry point, run against a temporary directory,
    writes the knowledge-store password the server then reads through PGPASSWORD_FILE."""
    task = json.loads((DEPLOY / "ecs-task-definition.json").read_text())
    init = next(c for c in task["containerDefinitions"] if c["name"] == "secrets-to-files")
    code = init["entryPoint"][-1].replace("'/run/secrets'", repr(str(tmp_path)))
    monkeypatch.setenv("PGPASSWORD", "kb-reader-fixture-password")
    exec(compile(code, "secrets-to-files", "exec"), {})  # the deployed one-liner, redirected
    assert (tmp_path / "pg-reader").read_text() == "kb-reader-fixture-password"
    server = next(c for c in task["containerDefinitions"] if c["name"] == "server")
    env = {item["name"]: item.get("value") for item in server["environment"]}
    monkeypatch.delenv("PGPASSWORD", raising=False)
    monkeypatch.setenv("PGPASSWORD_FILE", str(tmp_path / "pg-reader"))
    assert env["PGPASSWORD_FILE"] == "/run/secrets/pg-reader"
    assert read_secret("PGPASSWORD") == "kb-reader-fixture-password"
    monkeypatch.setenv("PGPASSWORD", "also-set")
    with pytest.raises(SecretConfigError, match="PGPASSWORD_FILE"):
        read_secret("PGPASSWORD")  # both given: refused, naming both


def test_the_descriptors_follow_section_11(monkeypatch: pytest.MonkeyPatch) -> None:
    task = json.loads((DEPLOY / "ecs-task-definition.json").read_text())
    service = json.loads((DEPLOY / "ecs-service.json").read_text())
    server = next(c for c in task["containerDefinitions"] if c["name"] == "server")
    env = {item["name"]: item.get("value") for item in server["environment"]}
    assert service["desiredCount"] == 1  # one replica: the run store is a SQLite file
    assert service["_targetGroup"]["healthCheck"]["path"] == "/readyz"  # readiness on the ALB
    assert "/healthz" in " ".join(server["healthCheck"]["command"])  # liveness in the container
    assert env["QUANT_AGENT_OS_LOG_FORMAT"] == "json"
    assert service["_alb"]["idleTimeoutSeconds"] == 3600
    assert "CORVANE_INTROSPECTION_SECRET" not in json.dumps(task)  # the shim is retired
    secrets = [s["name"] for c in task["containerDefinitions"] for s in c.get("secrets", [])]
    assert (
        "CORVANE_SNOWFLAKE_PRIVATE_KEY" in secrets
    )  # the fund's own: stays an environment variable
    assert "PGPASSWORD" in secrets and env["PGPASSWORD_FILE"] == "/run/secrets/pg-reader"
    dockerfile = (DEPLOY / "Dockerfile.corvane").read_text()
    assert "COPY --from=uv" in dockerfile and "rm /usr/local/bin/uv" in dockerfile


# ---- the performance report: the administrator's file reconciled to the books ------------------


def _monthly_rows(store: CorvaneDatasetStore, dataset: str) -> list[tuple[str, float]]:
    series = store.get(dataset)
    values = series.column("net")
    return [
        (day, value)
        for day, value in zip(series.index or [], values, strict=True)
        if value == value
    ]


def test_the_administrators_workbook_is_deterministic_and_differs_only_where_planted(
    store: CorvaneDatasetStore, tmp_path: Path, known: dict[str, Any]
) -> None:
    from quant_toolbox.performance import monthly_returns

    columns = {"LCC": _monthly_rows(store, "perf_lcc"), "GQ": _monthly_rows(store, "perf_gq")}
    first, second = tmp_path / "a.xlsx", tmp_path / "b.xlsx"
    write_workbook(first, columns)
    write_workbook(second, columns)
    assert first.read_bytes() == second.read_bytes()  # the same bytes every time

    from openpyxl import load_workbook

    sheet = load_workbook(first).active
    assert sheet.cell(row=1, column=1).value.startswith("Halvorsen")  # the title block
    assert sheet.cell(row=5, column=1).value == "Month end"  # header on row 5
    reference = {}
    for row in sheet.iter_rows(min_row=6, values_only=True):
        if row[0] is None:
            continue
        month = str(row[0])[:7]
        reference[month] = {"LCC": row[1], "GQ": row[2]}
    # Our figures, by the same function the generated report's reconcile uses.
    breaks = []
    for name, rows in columns.items():
        days = [date.fromisoformat(d) for d, _ in rows]
        ours = monthly_returns(days, [v for _, v in rows])
        for (year, month), value in ours.items():
            key = f"{year}-{month:02d}"
            difference = (value * 100 - reference[key][name]) * 100  # basis points
            if abs(difference) > 1.0:
                breaks.append((name, key, round(difference, 1)))
    assert breaks == [("LCC", SPIN_OFF_MONTH, pytest.approx(-SPIN_OFF_ADJUSTMENT_BP, abs=0.2))]


def test_the_returns_export_writes_the_books_net_columns(
    estate: Path, store: CorvaneDatasetStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import corvane_returns_export as export

    monkeypatch.setenv("CORVANE_SQL_URL", f"estate://{estate}")
    out = tmp_path / "returns.csv"
    rows = export.export(out)
    lines = out.read_text().splitlines()
    assert lines[0] == "date,LCC,GQ,BM_US300" and len(lines) == rows + 1 and rows == 109
    assert lines[1].split(",")[0] == store.get("perf_lcc").index[0]
    assert float(lines[1].split(",")[1]) == pytest.approx(store.get("perf_lcc").column("net")[0])
    assert float(lines[1].split(",")[3]) == pytest.approx(
        store.get("bench_ret").column("BM_US300")[0]
    )


# ---- friction: a holding re-keyed mid-period, and a renamed column in the lake ------------------


class _RekeyedStore:
    """A test double for the data team's change: from ``on``, the vendor's new identifier replaces
    ``old`` in every dataset that holds the holding, and history stays with the old identifier.
    The restricted-list dataset keeps the old identifier, as the compliance list would."""

    def __init__(self, inner: Any, *, old: str, new: str, on: str, datasets: set[str]) -> None:
        self.inner, self.old, self.new, self.on, self.datasets = inner, old, new, on, datasets

    def ids(self) -> list[str]:
        return list(self.inner.ids())

    def get(self, dataset_id: str) -> Any:
        dataset = self.inner.get(dataset_id)
        if dataset_id not in self.datasets or self.old not in dataset.columns or not dataset.index:
            return dataset
        cut = next(i for i, day in enumerate(dataset.index) if day >= self.on)
        nan = float("nan")
        old_values = list(dataset.columns[self.old])
        columns = {k: v for k, v in dataset.columns.items() if k != self.old}
        columns[self.old] = old_values[:cut] + [nan] * (len(old_values) - cut)
        columns[self.new] = [nan] * cut + old_values[cut:]
        return dataset.model_copy(
            update={"columns": columns, "fingerprint": dataset.fingerprint + "+rekey"}
        )


def test_a_rekeyed_restricted_holding_drops_out_of_the_mandate_check_without_a_warning(
    store: CorvaneDatasetStore, known: dict[str, Any]
) -> None:
    rekeyed = _RekeyedStore(
        store, old=SPIN_OFF, new="US0302", on="2026-03-16",
        datasets={"weights_sma_fnd_b", "weights_lcc", "px_ret_usd"},
    )  # fmt: skip
    catalog = corvane_server.build_catalog(rekeyed, PermissionPolicy())
    from quant_agent_os.runtime import ToolCall, ToolExecutor
    from quant_agent_os.tools import ToolRegistry

    executor = ToolExecutor(ToolRegistry(catalog.tools), catalog.policy)
    record = executor.execute(ToolCall(tool="corvane_mandate_check", arguments={
        "portfolio": {"dataset": "weights_sma_fnd_b"},
        "excluded": {"dataset": "mandate_flags_sma_fnd_b", "column": "excluded"},
        "start": "2026-01-02", "end": "2026-06-05",
    }))  # fmt: skip
    assert record.status == "ok"
    dates = [b["date"] for b in record.output["breaches"]]
    planted = [b["date"] for b in known["breaches"]["SMA_FND_B"]]
    assert planted == ["2026-03-12", "2026-03-13", "2026-03-16", "2026-03-17"]
    assert dates == ["2026-03-12", "2026-03-13"]  # the two breach days after the re-key are gone
    # The only trace is the generic missing-value warning, which names both identifiers and says
    # nothing about the restricted list, which still holds the old one.
    warnings = record.output.get("warnings", [])
    assert warnings and all("missing values" in w for w in warnings)
    assert any("US0302" in w for w in warnings)


def _rename_the_lake_column(estate: Path, connection: Any) -> None:
    """The curation job writes TOTAL_RETURN_LOCAL under a new name; the serving views still ask for
    it. Done on the live connection, as a data team's change would be seen by the next query."""
    files = estate / "s3" / "corvane-curated" / "curated" / "prices_daily" / "*" / "*.parquet"
    connection._db.execute(
        "CREATE OR REPLACE VIEW CORVANE.CURATED.PRICES_DAILY_EXT AS "
        "SELECT * RENAME (TOTAL_RETURN_LOCAL AS TR_LOCAL) "
        f"FROM read_parquet('{files}', hive_partitioning = true)"
    )  # fmt: skip


def test_a_warm_cache_hides_a_renamed_column_for_the_ttl_then_the_call_fails(
    estate: Path,
) -> None:
    """Measured: the store caches a dataset for 300 s, so the first calls after the rename still
    answer from the old build, with no warning, until the cache expires."""
    live = connect({"CORVANE_SQL_URL": f"estate://{estate}"})
    store = CorvaneDatasetStore(live)  # the deployed default: 300 s
    catalog = corvane_server.build_catalog(store, PermissionPolicy())
    from quant_agent_os.runtime import ToolCall, ToolExecutor
    from quant_agent_os.tools import ToolRegistry

    executor = ToolExecutor(ToolRegistry(catalog.tools), catalog.policy)
    weights = {"dataset": "weights_lcc", "as_of": "2026-05-15"}
    arguments = {"portfolio_id": "lcc", "weights_ref": weights, "asset_returns": RET}
    assert executor.execute(ToolCall(tool="analyze_risk", arguments=arguments)).status == "ok"
    _rename_the_lake_column(estate, live)
    assert (
        executor.execute(ToolCall(tool="analyze_risk", arguments=arguments)).status == "ok"
    )  # cached
    store.ttl_seconds = 0.0  # the cache has expired
    failed = executor.execute(ToolCall(tool="analyze_risk", arguments=arguments))
    assert failed.status == "error" and "internal error" in failed.error


def test_a_renamed_column_in_the_lake_fails_each_call_as_an_internal_error(estate: Path) -> None:
    fresh = connect({"CORVANE_SQL_URL": f"estate://{estate}"})
    _rename_the_lake_column(estate, fresh)
    store = CorvaneDatasetStore(fresh, ttl_seconds=0)
    catalog = corvane_server.build_catalog(store, PermissionPolicy())
    from quant_agent_os.runtime import ToolCall, ToolExecutor
    from quant_agent_os.tools import ToolRegistry

    executor = ToolExecutor(ToolRegistry(catalog.tools), catalog.policy)
    record = executor.execute(ToolCall(tool="analyze_risk", arguments={
        "portfolio_id": "lcc", "weights_ref": {"dataset": "weights_lcc", "as_of": "2026-05-15"},
        "asset_returns": RET,
    }))  # fmt: skip
    assert record.status == "error"
    assert record.error.startswith("Tool 'analyze_risk' failed with an internal error")
    assert "TOTAL_RETURN_LOCAL" not in record.error  # the caller cannot see which column
