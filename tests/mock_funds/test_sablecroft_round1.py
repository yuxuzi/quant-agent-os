# ruff: noqa: E501
"""Sablecroft Alternatives, round 1 of the 1.0 candidate: the 2026-07-28 client over the fund's
Entra ID sidecar, the AI-use report, readiness, JSON logs, secrets from files, quarterly performance,
the performance view, and the round's friction (quarterly NAVs that arrive late, a revised last
quarter, and the administrator's revised statement).

Every service is a local stand-in, as in the rest of tests/mock_funds. Nothing here leaves loopback.
"""

from __future__ import annotations

import csv
import inspect
import json
import logging
import math
import re
import sys
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import httpx2
import pyarrow.parquet as pq
import pytest
from mcp import Client
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from openpyxl import load_workbook

ROOT = Path(__file__).parents[2] / "examples" / "mock-funds" / "sablecroft"
for _folder in ("site", "facility/warehouse", "facility/identity"):
    sys.path.insert(0, str(ROOT / _folder))

import build_sablecroft as build_module  # noqa: E402
import build_sablecroft_round1 as build_round1  # noqa: E402
import sablecroft_entra_mock as entra  # noqa: E402
import sablecroft_server as server  # noqa: E402
from sablecroft_entra import EntraValidator, serve_introspection  # noqa: E402
from sablecroft_store import SablecroftStore  # noqa: E402
from sablecroft_warehouse import LocalDeltaWarehouse  # noqa: E402

from quant_agent_os.api import add_key  # noqa: E402
from quant_agent_os.config import get_settings  # noqa: E402
from quant_agent_os.config.secrets import SecretConfigError, read_secret  # noqa: E402
from quant_agent_os.observability.logging import configure_logging  # noqa: E402
from quant_agent_os.runs import MemoryRunStore  # noqa: E402
from quant_agent_os.runs.report import read_usage_report, render_markdown  # noqa: E402
from quant_agent_os.runs.store import RunStoreError  # noqa: E402
from quant_agent_os.tools import FileAccessPolicy, Permission, PermissionPolicy  # noqa: E402
from quant_platform import build_server_app, compose  # noqa: E402
from quant_platform.acceptance import load_gold, run_acceptance  # noqa: E402
from quant_platform.identity.sign_in import load_sign_in, load_sign_in_file  # noqa: E402

pytest.importorskip("mcp")
pytest.importorskip("openpyxl")

RESOURCE = "https://qaos.sablecroft.example/mcp"
SECRET = "shim-secret"
BASE = "http://127.0.0.1:8000"
ACCESS = ROOT / "facility" / "access.toml"
DEPLOY = ROOT / "site" / "deploy"
CUT = date(2026, 1, 10)
DEV = "dev.shah@sablecroft.example"  # sg-research-eq-ls
CIO = "amira.cole@sablecroft.example"  # sg-sablecroft-cio
CREDIT = "ines.moreau@sablecroft.example"  # sg-research-credit-real-assets
PRIVATE_MARKETS = "tom.okafor@sablecroft.example"  # sg-private-markets
QUARTERLY = ("IC02", "RA02", "RA03")


@pytest.fixture(scope="module")
def lake(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("sablecroft-round1")
    build_module.build(path)
    return path


@pytest.fixture(scope="module")
def store(lake: Path) -> SablecroftStore:
    return SablecroftStore(LocalDeltaWarehouse(lake))


@pytest.fixture(scope="module")
def documents(lake: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    return build_round1.build(lake, tmp_path_factory.mktemp("sablecroft-round1-docs"))


@pytest.fixture(scope="module")
def research() -> Iterator[Any]:
    """The research corpus the fund's own acceptance run searches, as the fund's test ingests it."""
    from quant_research import open_research

    app = open_research(":memory:")
    app.ingest.ingest(ROOT / "facility" / "research")
    yield app
    app.close()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("QUANT_AGENT_OS_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    yield tmp_path / "home"
    get_settings.cache_clear()


def policy() -> PermissionPolicy:
    return PermissionPolicy(granted=PermissionPolicy().granted | {Permission.READ_LOCAL_DATA})


class Served:
    """The fund's server over the built lake, signing in Entra-shaped tokens through the sidecar."""

    def __init__(
        self, store: SablecroftStore, tmp: Path, runs: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.stack = []
        self.issuer = self._enter(entra.serve())
        jwks = f"{self.issuer.base_url}/{entra.TENANT_ID}/discovery/v2.0/keys"
        validator = EntraValidator(self.issuer.issuer, jwks, RESOURCE)
        url = self._enter(serve_introspection(validator, "qaos", SECRET))
        monkeypatch.setenv("QAOS_INTROSPECTION_SECRET", SECRET)
        (tmp / "sign-in.toml").write_text(
            f'resource = "{RESOURCE}"\n\n[[authorization_servers]]\nissuer = "{self.issuer.issuer}"\n'
            f'introspection_url = "{url}"\nclient_id = "qaos"\n'
            'client_secret_env = "QAOS_INTROSPECTION_SECRET"\ntenant_claim = ""\ngroups_claim = "groups"\n'
        )
        add_key(tmp / "keys.toml", "ops", ["tools:list"])
        self.app = self._enter(
            build_server_app(
                [server.app],
                datasets=store,
                policy=policy(),
                keys=tmp / "keys.toml",
                runs=runs,
                access=FileAccessPolicy(ACCESS),
                sign_in=tmp / "sign-in.toml",
            )
        )
        self.runs = runs

    def _enter(self, manager: Any) -> Any:
        self.stack.append(manager)
        return manager.__enter__()

    def close(self) -> None:
        while self.stack:
            self.stack.pop().__exit__(None, None, None)

    def token(self, upn: str) -> str:
        return self.issuer.user_token(upn, audience=RESOURCE)

    def call(self, upn: str, body: Any) -> Any:
        async def main() -> Any:
            async with (
                self.app.router.lifespan_context(self.app),
                httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=self.app),
                    base_url=BASE,
                    headers={"Authorization": f"Bearer {self.token(upn)}"},
                ) as http,
                Client(
                    streamable_http_client(f"{BASE}/mcp", http_client=http), mode="auto", cache=None
                ) as client,
            ):
                return client.protocol_version, await body(client)

        return anyio.run(main)


@pytest.fixture
def served(
    store: SablecroftStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path
) -> Iterator[Served]:
    fund = Served(store, tmp_path, MemoryRunStore(), monkeypatch)
    yield fund
    fund.close()


EQ01_PERIODS = 59  # the equity long/short manager EQ01's months with a return, in the built lake


def ret_eq_ls(column: str = "EQ01") -> dict[str, Any]:
    return {
        "series_id": column,
        "returns": {"dataset": "ret_final_eq_ls", "column": column},
        "periods_per_year": 12,
    }


def quarterly(column: str = "IC02", periods: int | None = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "series_id": column,
        "returns": {"dataset": "ret_final_q", "column": column},
    }
    if periods is not None:
        arguments["periods_per_year"] = periods
    return arguments


# ---- 1. the 2026-07-28 client over the sidecar -------------------------------------------------


def test_a_2026_07_28_client_with_an_entra_token_gets_the_funds_tools(served: Served) -> None:
    async def body(client: Any) -> Any:
        tools = sorted(tool.name for tool in (await client.list_tools()).tools)
        result = await client.call_tool("analyze_performance", ret_eq_ls())
        return tools, result.is_error, result.structured_content

    version, (tools, is_error, outcome) = served.call(DEV, body)
    assert version == "2026-07-28"
    assert {"sab_private_fund_returns", "sab_revision_bias", "analyze_performance"} <= set(tools)
    assert is_error is False and outcome["periods"] == EQ01_PERIODS


def test_the_same_quarterly_call_gives_the_same_answer_over_both_handshakes(served: Served) -> None:
    async def body(client: Any) -> Any:
        result = await client.call_tool("analyze_performance", quarterly(periods=4))
        return result.structured_content

    modern_version, modern = served.call(PRIVATE_MARKETS, body)

    async def legacy_run() -> Any:
        async with (
            served.app.router.lifespan_context(served.app),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=served.app),
                base_url=BASE,
                headers={"Authorization": f"Bearer {served.token(PRIVATE_MARKETS)}"},
            ) as http,
            streamable_http_client(f"{BASE}/mcp", http_client=http) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool("analyze_performance", quarterly(periods=4))
            return result.structured_content

    assert modern_version == "2026-07-28"
    assert modern == anyio.run(legacy_run)


def _status_of(served: Served, token: str) -> int:
    async def probe() -> int:
        async with (
            served.app.router.lifespan_context(served.app),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=served.app),
                base_url=BASE,
                headers={"Authorization": f"Bearer {token}"},
            ) as http,
        ):
            return (await http.post("/mcp", json={})).status_code

    return anyio.run(probe)


def test_an_expired_or_other_audience_token_reaches_nothing(served: Served) -> None:
    expired = served.issuer.user_token(DEV, audience=RESOURCE, lifetime=-10)
    other = served.issuer.user_token(DEV, audience="api://sablecroft-qaos")
    assert _status_of(served, expired) == 401
    assert _status_of(served, other) == 401


# ---- 2. the AI-use report for a test day, and the acceptance run ------------------------------


def test_the_ai_use_report_for_a_test_day_names_principals_and_leaves_out_the_arguments(
    store: SablecroftStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    runs = MemoryRunStore()
    fund = Served(store, tmp_path, runs, monkeypatch)
    try:
        private_fund = {
            "calls": {"dataset": "pm_calls", "column": "PM01"},
            "distributions": {"dataset": "pm_distributions", "column": "PM01"},
            "nav": {"dataset": "pm_nav", "column": "PM01"},
        }

        async def day(client: Any) -> None:
            await client.call_tool("analyze_performance", ret_eq_ls())
            await client.call_tool("sab_private_fund_returns", private_fund)

        fund.call(DEV, day)  # the private-markets tool is not hers: an error, counted
        fund.call(PRIVATE_MARKETS, day)

        async def refused(client: Any) -> None:
            await client.call_tool("analyze_performance", ret_eq_ls())

        fund.call(CREDIT, refused)
    finally:
        fund.close()
    start = datetime.now(UTC) - timedelta(days=1)
    report = read_usage_report(
        runs, start=start, end=datetime.now(UTC) + timedelta(days=1), generated_at=datetime.now(UTC)
    )
    rows = {row.principal: row for row in report.by_principal}
    assert (rows[entra.USERS[DEV][0]].calls, rows[entra.USERS[DEV][0]].errors) == (2, 1)
    tom = rows[entra.USERS[PRIVATE_MARKETS][0]]
    assert (
        tom.calls == 2 and "sab_private_fund_returns" in tom.tools
    )  # its equity call is refused too
    assert (rows[entra.USERS[CREDIT][0]].calls, rows[entra.USERS[CREDIT][0]].refused) == (1, 1)
    markdown = render_markdown(report)
    assert (
        "PM01" not in markdown and "ret_final_eq_ls" in markdown
    )  # the dataset is named, the column is not
    assert entra.USERS[DEV][0] in markdown and "dev.shah" not in markdown  # a GUID, not the person


def test_the_acceptance_run_leaves_no_record_because_it_takes_no_run_store(
    store: SablecroftStore, research: Any
) -> None:
    """Measured by reading the signature: `run_acceptance` has no run store, so an operator's
    acceptance run never reaches the AI-use report. Its results are the fund's record instead."""
    assert not {"runs", "run_store", "recorder"} & set(inspect.signature(run_acceptance).parameters)
    gold, digest = load_gold(ROOT / "site" / "sablecroft_acceptance.toml")
    catalog = compose(
        policy=PermissionPolicy(),
        datasets=store,
        research=research.research,
        access=FileAccessPolicy(ACCESS),
        apps=[server.app],
    )
    report = run_acceptance(catalog, gold, digest)
    assert report.passed and report.total == 7


# ---- 3. readiness -----------------------------------------------------------------------------


class BrokenRunStore(MemoryRunStore):
    def list_runs(self, *args: Any, **kwargs: Any) -> Any:
        raise RunStoreError("the run store's file is gone")


def _ready(store: Any, tmp: Path, runs: Any) -> tuple[int, str]:
    tmp.mkdir(parents=True, exist_ok=True)
    add_key(tmp / "keys.toml", "ops", ["tools:list"])
    with build_server_app(
        [server.app], datasets=store, policy=policy(), keys=tmp / "keys.toml", runs=runs
    ) as app:

        async def probe() -> tuple[int, str]:
            async with (
                app.router.lifespan_context(app),
                httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE) as http,
            ):
                response = await http.get("/readyz")
                return response.status_code, response.text

        return anyio.run(probe)


def test_readyz_is_ready_while_each_store_answers(
    store: SablecroftStore, tmp_path: Path, home: Path
) -> None:
    assert _ready(store, tmp_path / "ready", MemoryRunStore()) == (200, '{"status":"ready"}')


def test_readyz_is_503_and_names_the_run_store_when_it_breaks(
    store: SablecroftStore, tmp_path: Path, home: Path
) -> None:
    status, text = _ready(store, tmp_path / "broken", BrokenRunStore())
    assert status == 503 and '"runs"' in text and "file is gone" not in text


def test_readyz_is_503_naming_datasets_when_the_warehouse_folder_is_gone(
    lake: Path, tmp_path: Path, home: Path
) -> None:
    """R2-S1, fixed in round 2: `check()` reads the warehouse's table folders uncached."""
    import shutil

    copy = tmp_path / "lake-copy"
    shutil.copytree(lake, copy)
    store = SablecroftStore(LocalDeltaWarehouse(copy))
    shutil.rmtree(copy / "uc")  # the warehouse goes after the server has started
    status, text = _ready(store, tmp_path / "keys", MemoryRunStore())
    assert status == 503 and '"datasets"' in text


# ---- 4. JSON logs and secrets from files ------------------------------------------------------


def test_json_logs_write_one_object_per_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], home: Path
) -> None:
    monkeypatch.setenv("QUANT_AGENT_OS_LOG_FORMAT", "json")
    assert get_settings().log_format == "json"
    configure_logging("INFO", log_format="json")
    logging.getLogger("quant_agent_os.sablecroft_round1").warning(
        "readiness check failed: %s", "runs"
    )
    lines = [line for line in capsys.readouterr().err.splitlines() if line.startswith("{")]
    record = json.loads(lines[-1])
    assert record["level"] == "WARNING" and {"time", "logger", "message"} <= set(record)


def test_the_sidecar_secret_and_the_password_come_from_files(tmp_path: Path) -> None:
    secret = tmp_path / "introspection-secret"
    secret.write_text(SECRET + "\n")
    password = tmp_path / "knowledge-password"
    password.write_text("knowledge-fixture")
    assert (
        read_secret("QAOS_INTROSPECTION_SECRET", {"QAOS_INTROSPECTION_SECRET_FILE": str(secret)})
        == SECRET
    )
    assert read_secret("PGPASSWORD", {"PGPASSWORD_FILE": str(password)}) == "knowledge-fixture"
    with pytest.raises(SecretConfigError, match="not both"):
        read_secret("PGPASSWORD", {"PGPASSWORD": "x", "PGPASSWORD_FILE": str(password)})
    parsed = load_sign_in_file(DEPLOY / "sign-in.toml")
    assert parsed.authorization_servers[0].client_secret_env == "QAOS_INTROSPECTION_SECRET"


def test_the_descriptors_use_readiness_json_logs_one_replica_and_secret_files() -> None:
    bicep = (DEPLOY / "containerapp.bicep").read_text()
    assert "path: '/readyz'" in bicep and "path: '/healthz'" in bicep
    assert "{ name: 'QUANT_AGENT_OS_LOG_FORMAT', value: 'json' }" in bicep
    assert (
        "{ name: 'QAOS_INTROSPECTION_SECRET_FILE', value: '/run/secrets/introspection-secret' }"
        in bicep
    )
    assert "{ name: 'PGPASSWORD_FILE', value: '/run/secrets/knowledge-password' }" in bicep
    assert "scale: { minReplicas: 1, maxReplicas: 1 }" in bicep
    # The Databricks secret is the fund's own: an environment variable, as the docs say.
    assert "{ name: 'DATABRICKS_CLIENT_SECRET', secretRef: 'dbx-client-secret' }" in bicep


def test_the_run_store_placeholder_in_the_descriptor_is_not_a_secret_reference() -> None:
    """Measured by reading: the Azure Files key is a placeholder string, not a Key Vault reference."""
    assert (
        "accountKey: 'REPLACE-with-a-secret-reference'"
        in (DEPLOY / "containerapp.bicep").read_text()
    )


def test_the_jwks_sign_in_takes_the_same_tokens_with_no_sidecar(tmp_path: Path) -> None:
    """Measured, not deployed: `verify = "jwks"` checks the signature against the mock issuer's keys."""
    from quant_agent_os.mcp.http import GROUPS_CLAIM

    with entra.serve() as issuer:
        path = tmp_path / "jwks.toml"
        path.write_text(
            f'resource = "{RESOURCE}"\n\n[[authorization_servers]]\nissuer = "{issuer.issuer}"\n'
            f'verify = "jwks"\njwks_uri = "{issuer.base_url}/{entra.TENANT_ID}/discovery/v2.0/keys"\n'
            f'audiences = ["{RESOURCE}"]\ntenant_claim = ""\nprincipal_claim = "preferred_username"\n'
            'provider_tenant_claim = "tid"\n'
            'groups_claims = ["groups"]\n'
        )
        verifier = load_sign_in(path, {}).verifier()
        access = anyio.run(verifier.verify_token, issuer.user_token(DEV, audience=RESOURCE))
        assert access is not None and access.claims is not None
        assert access.claims[GROUPS_CLAIM] == [entra.GROUPS["sg-research-eq-ls"]]
        assert access.subject == f"{DEV}@{entra.TENANT_ID}"
        assert (
            anyio.run(
                verifier.verify_token, issuer.user_token(DEV, audience="api://sablecroft-qaos")
            )
            is None
        )


# ---- 5. quarterly performance and the view ----------------------------------------------------


def test_the_platform_annualises_the_quarterly_managers_on_four_periods(
    served: Served, store: SablecroftStore
) -> None:
    returns = [float(v) for v in store.get("ret_final_q").columns["IC02"] if v == v]
    annual = math.prod(1 + r for r in returns) ** (4 / len(returns)) - 1
    sd = math.sqrt(
        sum((r - sum(returns) / len(returns)) ** 2 for r in returns) / (len(returns) - 1)
    )

    async def body(client: Any) -> Any:
        result = await client.call_tool("analyze_performance", quarterly())
        return result.structured_content

    _, outcome = served.call(CIO, body)
    assert outcome["periods"] == 19 and outcome["periods_per_year"] == 4
    assert outcome["metrics"]["annualized_return"] == pytest.approx(annual, abs=1e-9)
    assert outcome["metrics"]["annualized_volatility"] == pytest.approx(sd * 2, abs=1e-9)
    assert outcome["warnings"] == []  # nothing says the last quarter is 102 days before the cut


def test_the_view_draws_the_metrics_but_not_the_currency_of_an_eur_manager(served: Served) -> None:
    async def body(client: Any) -> Any:
        result = await client.call_tool(
            "analyze_performance",
            {
                "series_id": "EV03",
                "returns": {"dataset": "ret_final", "column": "EV03"},
                "periods_per_year": 12,
            },
        )
        view = await client.read_resource("ui://quant-agent-os/performance.html")
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        return (
            result.structured_content,
            view.contents[0].mime_type,
            tools["analyze_performance"].meta,
        )

    _, (outcome, mime, meta) = served.call(CIO, body)
    assert mime == "text/html;profile=mcp-app"
    assert meta["ui"]["resourceUri"] == "ui://quant-agent-os/performance.html"
    assert len(outcome["metrics"]) == 18
    assert not any(
        "currency" in key.lower() or "eur" in str(value).lower()
        for key, value in outcome["conventions"].items()
    )


# ---- 6. the round's friction: the late, revised feed and the administrator's statement --------


def test_the_feed_at_the_cut_holds_the_last_quarter_as_a_first_print(
    lake: Path, documents: dict[str, Path]
) -> None:
    answers = json.loads((documents["feed"].parent / "round1-answers.json").read_text())
    assert answers["last_quarter_at_cut"] == "2025-09-30"
    rows = list(csv.DictReader(documents["feed"].read_text().splitlines()))
    assert rows[-1]["date"] == "2025-09-30" and len(rows) == 19
    finals = {
        (row["period_end"], row["manager_id"]): float(row["return_local"])
        for path in (lake / "uc" / "sablecroft" / "curated" / "mgr_returns_q").glob(
            "dt=*/*.parquet"
        )
        for row in pq.read_table(path).to_pylist()
    }
    for manager in QUARTERLY:
        earlier = float(rows[-2][manager]) - finals[("2025-06-30", manager)]
        assert abs(earlier) < 1e-9  # the earlier quarters are finals
        last = float(rows[-1][manager]) - finals[("2025-09-30", manager)]
        assert last == pytest.approx(build_round1.FIRST_PRINT_ERROR[manager], abs=1e-9)


def test_the_statements_arrive_60_to_75_days_late(documents: dict[str, Path]) -> None:
    rows = list(csv.DictReader(documents["statement_log"].read_text().splitlines()))
    lags = {int(row["lag_days"]) for row in rows}
    assert lags == {60, 75}  # inside the round's 45 to 90 days
    last = [row for row in rows if row["period_end"] == "2025-09-30"]
    assert {row["published_date"] for row in last} <= {"2025-11-29", "2025-12-14"}
    assert (CUT - date(2025, 9, 30)).days == 102  # the last quarter is 102 days old at the cut


def test_the_administrators_revised_statement_has_title_rows_and_a_header_on_row_four(
    documents: dict[str, Path],
) -> None:
    sheet = load_workbook(documents["revised"]).active
    assert sheet["A1"].value.startswith("Northgate Fund Services")
    assert [sheet.cell(4, column).value for column in range(1, 5)] == ["Quarter end", *QUARTERLY]
    last = [
        row
        for row in sheet.iter_rows(min_row=5, values_only=True)
        if row[0] is not None and hasattr(row[0], "year")
    ][-1]
    feed = list(csv.DictReader(documents["feed"].read_text().splitlines()))[-1]
    for column, manager in enumerate(QUARTERLY, start=1):
        assert last[column] - float(feed[manager]) == pytest.approx(
            -build_round1.FIRST_PRINT_ERROR[manager], abs=1e-9
        )


def test_round_one_documents_are_the_same_on_every_build(lake: Path, tmp_path: Path) -> None:
    first = build_round1.build(lake, tmp_path / "a")
    second = build_round1.build(lake, tmp_path / "b")
    assert first["feed"].read_bytes() == second["feed"].read_bytes()
    assert first["statement_log"].read_bytes() == second["statement_log"].read_bytes()


def test_the_facility_imports_nothing_of_the_platform() -> None:
    text = (ROOT / "facility" / "warehouse" / "build_sablecroft_round1.py").read_text()
    assert not re.search(r"^\s*(from|import)\s+(quant_\w+|mcp|httpx2)\b", text, re.MULTILINE)
