# ruff: noqa: E501
"""Larchmere Loan Partners, round 1 of the 1.0 candidate: the 2026-07-28 client, the AI-use report,
readiness, JSON logs, secrets from files, the performance view, and the round's friction (a
trustee workbook with merged header cells and a restatement, and an administrator's statement).

Every service is a local stand-in, as in the rest of tests/mock_funds. Nothing here leaves loopback.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
import shutil
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import httpx2
import pandas as pd
import pytest
from openpyxl import load_workbook

from quant_agent_os.api import add_key
from quant_agent_os.config import get_settings
from quant_agent_os.config.secrets import SecretConfigError, read_secret
from quant_agent_os.observability.logging import configure_logging
from quant_agent_os.runs import MemoryRunStore
from quant_agent_os.runs.report import read_usage_report, render_markdown
from quant_agent_os.runs.store import RunStoreError
from quant_agent_os.tools import FileAccessPolicy, PermissionPolicy
from quant_platform import build_server_app
from quant_platform.identity.sign_in import SignInError, load_sign_in, load_sign_in_file

ROOT = Path(__file__).resolve().parents[2]
FUND = ROOT / "examples" / "mock-funds" / "larchmere"
for part in ("facility/warehouse", "facility/gateway", "site"):
    sys.path.insert(0, str(FUND / part))

import build_larchmere as estate  # noqa: E402
import build_larchmere_round1 as build_round1  # noqa: E402
import larchmere_entra_mock as entra  # noqa: E402
from larchmere_server import clo  # noqa: E402
from larchmere_store import LarchmereDatasetStore  # noqa: E402

pytest.importorskip("empyrical")
pytest.importorskip("openpyxl")

from mcp import Client  # noqa: E402
from mcp.client.session import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

RESOURCE = "https://larchmere-qaos.example.azurecontainerapps.io/mcp"
SECRET = "shim-fixture-secret"
BASE = "http://127.0.0.1:8000"
PM_2018_2 = "tomas.okafor@larchmere.example"
PM_2021_1 = "ines.vargas@larchmere.example"
ANALYST = "priya.lindqvist@larchmere.example"
PM_2018_2_OID = "a1000000-0000-4000-8000-000000000002"
GROUP_PM_2018_2 = "4b1f0c52-6a4e-4d36-9a21-0a1b2c3d0002"
ANALYST_OID = "a1000000-0000-4000-8000-000000000003"
CUT = "2026-10-30"
TRUSTEE_RESTATED_TEST = ("LARCH-2018-2", "oc_e")


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("larchmere-round1")
    estate.build(out)
    return out


@pytest.fixture(scope="module")
def documents(built: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    return build_round1.build(built, tmp_path_factory.mktemp("round1-documents"))


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home of its own, so no .env or run store of the machine's is read."""
    monkeypatch.setenv("QUANT_AGENT_OS_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    yield tmp_path / "home"
    get_settings.cache_clear()


def store_for(built: Path) -> LarchmereDatasetStore:
    return LarchmereDatasetStore(built / "sql" / "clo.db", built / "lake" / "catalog.json")


def sign_in_text(base: str) -> str:
    """The deployed sign-in file, its introspection URL pointed at this test's shim."""
    return (
        (FUND / "site" / "deploy" / "sign-in.toml")
        .read_text()
        .replace(
            "https://larchmere-token-shim.internal.example.azurecontainerapps.io/shim/introspect",
            f"{base}/shim/introspect",
        )
    )


class Served:
    """The fund's server app over the built estate, behind a shim that issues Entra-shaped tokens."""

    def __init__(self, built: Path, tmp: Path, runs: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tenant = entra.EntraTenant()
        self.runs = runs
        self.built = built
        self.tmp = tmp
        monkeypatch.setenv("LARCHMERE_INTROSPECTION_SECRET", SECRET)
        self._shim = entra.running(self.tenant, RESOURCE, SECRET)
        self.shim_url = self._shim.__enter__()
        add_key(tmp / "keys.toml", "ops", ["tools:list"])  # the sign-in is the way in
        (tmp / "sign-in.toml").write_text(sign_in_text(self.shim_url))
        self.app_cm = build_server_app(
            [clo],
            datasets=store_for(built),
            policy=PermissionPolicy(),
            keys=tmp / "keys.toml",
            runs=runs,
            access=FileAccessPolicy(FUND / "facility" / "access.toml"),
            sign_in=tmp / "sign-in.toml",
        )
        self.app = self.app_cm.__enter__()

    def close(self) -> None:
        self.app_cm.__exit__(None, None, None)
        self._shim.__exit__(None, None, None)

    def token(self, upn: str) -> str:
        return self.tenant.issue(upn)

    def http(self, upn: str) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=self.app),
            base_url=BASE,
            headers={"Authorization": f"Bearer {self.token(upn)}"},
        )

    def call(self, upn: str, body: Any) -> Any:
        """Run ``body(client)`` as a 2026-07-28 MCP client for ``upn``; return (version, result)."""

        async def main() -> Any:
            async with (
                self.app.router.lifespan_context(self.app),
                self.http(upn) as http,
                Client(
                    streamable_http_client(f"{BASE}/mcp", http_client=http),
                    mode="auto",
                    cache=None,
                ) as client,
            ):
                return client.protocol_version, await body(client)

        return anyio.run(main)

    def legacy(self, upn: str, body: Any) -> Any:
        """The same body over the 2025-11-25 handshake, from the same endpoint."""

        async def main() -> Any:
            async with (
                self.app.router.lifespan_context(self.app),
                self.http(upn) as http,
                streamable_http_client(f"{BASE}/mcp", http_client=http) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                return await body(session)

        return anyio.run(main)


@pytest.fixture
def served(built: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path) -> Any:
    runs = MemoryRunStore()
    fund = Served(built, tmp_path, runs, monkeypatch)
    yield fund
    fund.close()


def snapshot(deal: str, as_of: str) -> dict[str, Any]:
    key = deal.lower().replace("-", "_")
    return {
        "par": {"dataset": f"par_{key}"},
        "price": {"dataset": f"price_{key}"},
        "rating_factor": {"dataset": "rf_all"},
        "static": {"dataset": "loan_static"},
        "structure": {"dataset": f"structure_{key}"},
        "sofr": {"dataset": "sofr"},
        "as_of": as_of,
    }


async def _tools(client: Any) -> list[str]:
    return sorted(tool.name for tool in (await client.list_tools()).tools)


# ---- 1. the 2026-07-28 client, over Entra-shaped tokens ----------------------------------------


def test_a_2026_07_28_client_with_an_entra_token_gets_the_funds_tools(served: Any) -> None:
    async def body(client: Any) -> Any:
        tools = await _tools(client)
        result = await client.call_tool("clo_compliance_tests", snapshot("LARCH-2018-2", CUT))
        return tools, result.structured_content, result.is_error

    version, (tools, outcome, is_error) = served.call(PM_2018_2, body)
    assert version == "2026-07-28"
    assert {
        "clo_compliance_tests",
        "clo_what_if",
        "clo_equity_returns",
        "analyze_performance",
    } <= set(tools)
    assert is_error is False and outcome["as_of"] == CUT
    assert outcome["failed"] == [] and outcome["values"]["oc_e"] > 1.1225


def test_the_same_call_gives_the_same_answer_over_both_handshakes(served: Any) -> None:
    async def body(client: Any) -> Any:
        result = await client.call_tool("clo_compliance_tests", snapshot("LARCH-2018-2", CUT))
        return result.structured_content

    modern_version, modern = served.call(PM_2018_2, body)
    legacy = served.legacy(PM_2018_2, body)
    assert modern_version == "2026-07-28"
    assert modern == legacy


def test_an_analyst_is_refused_a_deal_dataset_as_if_it_were_missing(served: Any) -> None:
    async def body(client: Any) -> Any:
        result = await client.call_tool(
            "analyze_performance",
            {
                "series_id": "x",
                "returns": {"dataset": "equity_returns_larch_2018_2", "column": "equity"},
                "periods_per_year": 12,
            },
        )
        return result.is_error, result.structured_content

    _, (is_error, outcome) = served.call(ANALYST, body)
    assert is_error is True and outcome["status"] == "error"
    assert "No dataset 'equity_returns_larch_2018_2'" in outcome["error"]


# ---- 2. the AI-use report for a test day ------------------------------------------------------


def test_the_ai_use_report_for_a_test_day_says_who_called_what_and_leaves_out_the_arguments(
    served: Any,
) -> None:
    async def day(client: Any) -> None:
        await client.call_tool("clo_compliance_tests", snapshot("LARCH-2018-2", CUT))
        await client.call_tool(
            "clo_what_if",
            {
                **snapshot("LARCH-2018-2", CUT),
                "loan_id": "LN0042",
                "par_change": 2500000.0,
                "trade_price": 98.5,
            },
        )
        await client.call_tool(
            "analyze_performance",
            {
                "series_id": "x",
                "returns": {"dataset": "equity_returns_larch_2018_2", "column": "equity"},
                "periods_per_year": 12,
            },
        )

    served.call(PM_2018_2, day)

    async def refused(client: Any) -> None:
        await client.call_tool(
            "analyze_performance",
            {
                "series_id": "x",
                "returns": {"dataset": "equity_returns_larch_2018_2", "column": "equity"},
                "periods_per_year": 12,
            },
        )

    served.call(ANALYST, refused)
    start = datetime.now(UTC) - timedelta(days=1)
    report = read_usage_report(
        served.runs,
        start=start,
        end=datetime.now(UTC) + timedelta(days=1),
        generated_at=datetime.now(UTC),
    )
    by_principal = {row.principal: row for row in report.by_principal}
    pm = by_principal[PM_2018_2_OID]
    assert (pm.calls, pm.ok, pm.errors) == (3, 3, 0)
    assert "clo_what_if" in pm.tools and "equity_returns_larch_2018_2" in pm.datasets_read
    analyst = by_principal[ANALYST_OID]
    assert (analyst.calls, analyst.refused, analyst.errors) == (1, 1, 1)
    markdown = render_markdown(report)
    # What compliance would not find here: the loan, the size, and the price of the what-if.
    assert "LN0042" not in markdown and "2500000" not in markdown and "98.5" not in markdown
    # Who it names: the directory's object id, not the person's name.
    assert "tomas.okafor" not in markdown and PM_2018_2_OID in markdown


# ---- 3. readiness -----------------------------------------------------------------------------


class BrokenRunStore(MemoryRunStore):
    """A run store whose database has gone: every read raises."""

    def list_runs(self, *args: Any, **kwargs: Any) -> Any:
        raise RunStoreError("the run store's database is unreachable")


def test_readyz_is_ready_while_each_store_answers(built: Path, tmp_path: Path, home: Path) -> None:
    add_key(tmp_path / "keys.toml", "ops", ["tools:list"])
    with build_server_app(
        [clo], datasets=store_for(built), policy=PermissionPolicy(), keys=tmp_path / "keys.toml",
        runs=MemoryRunStore(),
    ) as app:  # fmt: skip

        async def probe() -> tuple[int, dict[str, Any]]:
            async with (
                app.router.lifespan_context(app),
                httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE) as http,
            ):
                response = await http.get("/readyz")
                return response.status_code, response.json()

        assert anyio.run(probe) == (200, {"status": "ready"})


def test_readyz_is_503_and_names_the_run_store_when_it_breaks(
    built: Path, tmp_path: Path, home: Path
) -> None:
    add_key(tmp_path / "keys.toml", "ops", ["tools:list"])
    with build_server_app(
        [clo], datasets=store_for(built), policy=PermissionPolicy(), keys=tmp_path / "keys.toml",
        runs=BrokenRunStore(),
    ) as app:  # fmt: skip

        async def probe() -> tuple[int, str]:
            async with (
                app.router.lifespan_context(app),
                httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE) as http,
            ):
                response = await http.get("/readyz")
                return response.status_code, response.text

        status, text = anyio.run(probe)
    assert status == 503 and '"failed"' in text and '"runs"' in text
    assert "unreachable" not in text  # the error itself is never sent to the caller


def test_readyz_is_503_naming_datasets_when_the_sql_source_is_gone(
    built: Path, tmp_path: Path, home: Path
) -> None:
    """R2-L1, fixed in round 2: `check()` reads the deal database uncached, so `/readyz` fails."""
    copy = tmp_path / "estate"
    shutil.copytree(built, copy)
    (copy / "sql" / "clo.db").unlink()
    add_key(tmp_path / "keys.toml", "ops", ["tools:list"])
    with build_server_app(
        [clo], datasets=store_for(copy), policy=PermissionPolicy(), keys=tmp_path / "keys.toml",
        runs=MemoryRunStore(),
    ) as app:  # fmt: skip

        async def probe() -> tuple[int, str]:
            async with (
                app.router.lifespan_context(app),
                httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE) as http,
            ):
                response = await http.get("/readyz")
                return response.status_code, response.text

        status, text = anyio.run(probe)
    assert status == 503 and '"datasets"' in text and "missing" not in text


# ---- 4. JSON logs -----------------------------------------------------------------------------


def test_json_logs_write_one_object_per_line_with_the_fields_a_log_service_reads(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], home: Path
) -> None:
    monkeypatch.setenv("QUANT_AGENT_OS_LOG_FORMAT", "json")
    assert get_settings().log_format == "json"
    configure_logging("INFO", log_format="json")
    logging.getLogger("quant_agent_os.larchmere_round1").warning(
        "readiness check failed: %s", "runs"
    )
    lines = [line for line in capsys.readouterr().err.splitlines() if line.startswith("{")]
    assert lines, "no JSON line on stderr"
    record = json.loads(lines[-1])
    assert {"time", "level", "logger", "message"} <= set(record)
    assert record["level"] == "WARNING" and record["message"] == "readiness check failed: runs"
    with pytest.raises(ValueError):
        configure_logging("INFO", log_format="JSON")


# ---- 5. secrets from files --------------------------------------------------------------------


def test_the_sign_in_secret_and_the_password_come_from_files(tmp_path: Path) -> None:
    secret_file = tmp_path / "shim-secret"
    secret_file.write_text(SECRET + "\n")
    password_file = tmp_path / "pg-reader"
    password_file.write_text("pg-fixture-password")
    assert (
        read_secret(
            "LARCHMERE_INTROSPECTION_SECRET",
            {"LARCHMERE_INTROSPECTION_SECRET_FILE": str(secret_file)},
        )
        == SECRET
    )
    assert (
        read_secret("PGPASSWORD", {"PGPASSWORD_FILE": str(password_file)}) == "pg-fixture-password"
    )
    with pytest.raises(SecretConfigError, match="not both"):
        read_secret(
            "PGPASSWORD",
            {"PGPASSWORD": "x", "PGPASSWORD_FILE": str(password_file)},
        )
    parsed = load_sign_in_file(FUND / "site" / "deploy" / "sign-in.toml")
    assert parsed.authorization_servers[0].client_secret_env == "LARCHMERE_INTROSPECTION_SECRET"
    with pytest.raises(SignInError):
        parsed.resolve({"LARCHMERE_INTROSPECTION_SECRET_FILE": str(tmp_path / "missing")})


def test_the_platforms_jwks_sign_in_takes_the_same_tokens_with_no_shim(
    tmp_path: Path, tenant_base: Any
) -> None:
    """Measured, not deployed: section 10's `verify = "jwks"` checks Entra's own signature, so the
    shim is not needed for these tokens. The deployed descriptor still uses the shim (a security
    choice for the owner); this shows what the switch would change."""
    from quant_agent_os.mcp.http import GROUPS_CLAIM

    tenant, base = tenant_base
    path = tmp_path / "jwks.toml"
    path.write_text(
        f'resource = "{RESOURCE}"\n\n[[authorization_servers]]\nissuer = "{tenant.issuer}"\n'
        f'verify = "jwks"\njwks_uri = "{base}/{tenant.tid}/discovery/v2.0/keys"\n'
        'audiences = ["api://larchmere-qaos"]\ntenant_claim = ""\n'
        'principal_claim = "preferred_username"\nscope_claims = ["scp", "roles"]\n'
        'groups_claims = ["groups", "roles"]\nprovider_tenant_claim = "tid"\n\n'
        '[authorization_servers.scope_map]\n"tools.list" = "tools:list"\n"tools.call" = "tools:call"\n'
    )
    verifier = load_sign_in(path, {}).verifier()
    access = anyio.run(verifier.verify_token, tenant.issue(PM_2018_2))
    assert access is not None and set(access.scopes) == {"tools:list", "tools:call"}
    assert access.subject == f"{PM_2018_2}@{tenant.tid}"  # the person, and the directory
    assert access.claims is not None
    assert access.claims[GROUPS_CLAIM] == [GROUP_PM_2018_2, "CLO.PM"]  # groups and roles together
    assert anyio.run(verifier.verify_token, tenant.issue(PM_2018_2, audience="api://other")) is None


@pytest.fixture
def tenant_base() -> Iterator[tuple[entra.EntraTenant, str]]:
    tenant = entra.EntraTenant()
    with entra.running(tenant, RESOURCE, SECRET) as base:
        yield tenant, base


# ---- 6. the deployment descriptors, against the docs --------------------------------------------


def _env_file() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (FUND / "site" / "deploy" / "larchmere.env").read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.split("   #")[0].strip()
    return values


def test_the_descriptors_use_readiness_json_logs_one_replica_and_secret_files() -> None:
    env = _env_file()
    bicep = (FUND / "site" / "deploy" / "containerapp.bicep").read_text()
    assert env["QUANT_AGENT_OS_LOG_FORMAT"] == "json"
    assert "name: 'QUANT_AGENT_OS_LOG_FORMAT', value: 'json'" in bicep
    assert "path: '/readyz'" in bicep and "path: '/healthz'" in bicep
    assert "minReplicas: 1, maxReplicas: 1" in bicep
    # Each platform secret is a _FILE path that the mounted secret volume holds.
    for name in ("PGPASSWORD_FILE", "LARCHMERE_INTROSPECTION_SECRET_FILE"):
        mount = env[name]
        assert mount.startswith("/run/secrets/") and mount.split("/")[-1] in bicep
        assert f"'{name}'" in bicep or f"'{name}', value" in bicep
    assert "LARCHMERE_SQL_CONNECTION" in env and "PGPASSWORD" not in env  # the fund's own env var


def test_the_image_pin_is_still_0_7_0_the_release_before_the_features_the_descriptors_set() -> None:
    """Round 1, R1-L14: the Dockerfile pins 0.7.0, released before /readyz, JSON logs, and NAME_FILE
    (the changelog's Unreleased section). When the pin moves to the 1.0 release, change this line."""
    dockerfile = (FUND / "site" / "deploy" / "Dockerfile").read_text()
    pin = re.search(r"quant-agent-os:(\d+\.\d+\.\d+)", dockerfile)
    assert pin is not None and pin.group(1) == "0.7.0"


# ---- 7. the performance view -----------------------------------------------------------------


def test_the_equity_return_gets_the_view_with_18_metrics_and_no_money_weighted_return(
    served: Any,
) -> None:
    async def body(client: Any) -> Any:
        result = await client.call_tool(
            "analyze_performance",
            {
                "series_id": "equity 2018-2",
                "returns": {"dataset": "equity_returns_larch_2018_2", "column": "equity"},
                "periods_per_year": 12,
            },
        )
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        view = await client.read_resource("ui://quant-agent-os/performance.html")
        return result.structured_content, tools["analyze_performance"].meta, view

    _, (outcome, meta, view) = served.call(PM_2018_2, body)
    assert meta["ui"]["resourceUri"] == "ui://quant-agent-os/performance.html"
    assert view.contents[0].mime_type == "text/html;profile=mcp-app"
    assert len(outcome["metrics"]) == 18
    assert not {"irr", "moic", "cash_on_cash", "xirr"} & set(outcome["metrics"])
    assert outcome["warnings"] == [
        "Left out 1 rows where equity_returns_larch_2018_2.equity is missing; nothing was filled in."
    ]


def test_the_deal_cash_flow_measures_are_a_tool_with_no_view(served: Any) -> None:
    async def body(client: Any) -> Any:
        result = await client.call_tool(
            "clo_equity_returns", {"cashflows": {"dataset": "equity_cf_larch_2018_2"}}
        )
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        return result.structured_content, tools["clo_equity_returns"].meta

    _, (outcome, meta) = served.call(PM_2018_2, body)
    assert {"irr", "moic", "cash_on_cash_annual"} <= set(outcome)
    assert not (meta or {}).get("ui")


# ---- 8. the round's friction: the trustee's workbook -----------------------------------------


def test_the_trustee_workbook_has_two_header_rows_and_merged_cells(
    documents: dict[str, Path],
) -> None:
    sheet = load_workbook(documents["trustee"]).active
    merged = {str(item) for item in sheet.merged_cells.ranges}
    assert {"A4:A5", "B4:B5", "E4:E5", "C4:D4", "F4:H4"} <= merged
    assert (sheet["A4"].value, sheet["C5"].value, sheet["G5"].value) == (
        "Deal",
        "Value",
        "Restated value",
    )
    # pandas' default reads the title as the header: the two header rows need an explicit header.
    default = pd.read_excel(documents["trustee"])
    assert next(iter(default.columns)).startswith("Harbourview")


def test_the_restatement_turns_a_passing_oc_test_into_a_failing_one(
    documents: dict[str, Path],
) -> None:
    sheet = load_workbook(documents["trustee"]).active
    rows = [
        row
        for row in sheet.iter_rows(min_row=6, values_only=True)
        if row[0] == TRUSTEE_RESTATED_TEST[0] and row[1] == TRUSTEE_RESTATED_TEST[1]
    ]
    assert len(rows) == 1
    _, _, value, trigger, result, prior, restated, change = rows[0]
    assert result == "PASS" and value == pytest.approx(1.169039)
    assert prior == pytest.approx(1.167871) and restated < trigger
    assert change == pytest.approx(restated - prior, abs=1e-6)
    assert "restated result is FAIL" in " ".join(
        str(cell.value) for cell in sheet["A"] if cell.value
    )


def test_the_fund_reads_the_trustee_restatement_and_finds_our_figure_disagrees(
    served: Any, documents: dict[str, Path]
) -> None:
    """The fund's own parser (site/larchmere_trustee.py) reads the workbook; our OC test for the
    prior month, recomputed by the fund's tool, is the trustee's original figure, not the restated one."""
    from larchmere_trustee import read_trustee_report

    report = read_trustee_report(documents["trustee"])
    restated = report.restatements()
    assert [(r.deal_id, r.test_name) for r in restated] == [TRUSTEE_RESTATED_TEST]

    async def body(client: Any) -> Any:
        result = await client.call_tool(
            "clo_compliance_tests", snapshot("LARCH-2018-2", "2026-09-30")
        )
        return result.structured_content

    _, ours = served.call(PM_2018_2, body)
    ours_oc = ours["values"]["oc_e"]
    assert ours_oc == pytest.approx(restated[0].prior_value, abs=1e-5)
    assert ours_oc - restated[0].restated_value > 0.04  # a break the reconciliation must show


def test_the_trustee_numbers_are_a_dataset_and_its_results_are_not(built: Path) -> None:
    """Measured: the trustee's numbers are served (one column per test); PASS and FAIL are text,
    which a numeric dataset cannot hold, so the results stay in the trustee's own file."""
    store = store_for(built)
    assert "trustee_numbers_larch_2018_2" in store.ids()
    frame = pd.DataFrame(store.get("trustee_numbers_larch_2018_2").columns)
    assert "oc_e" in frame.columns and "result" not in frame.columns
    assert frame.loc[
        "2026-09-30" if "2026-09-30" in frame.index else frame.index[-2], "oc_e"
    ] == pytest.approx(1.167871)


# ---- 9. the round's friction: the administrator's statement ------------------------------------


def test_the_administrator_statement_is_one_month_short_with_one_planted_break(
    documents: dict[str, Path],
) -> None:
    sheet = load_workbook(documents["administrator"]).active
    assert str(sheet["A4"].value) == "Month end" and sheet["B5"].value == "Equity 2018-2"
    dates = [
        row[0]
        for row in sheet.iter_rows(min_row=6, values_only=True)
        if row[0] is not None and hasattr(row[0], "year")
    ]
    assert dates[-1].date().isoformat() == "2026-09-30"  # the fund's file runs to 2026-10-30
    fund = list(csv.DictReader(documents["fund_returns"].read_text().splitlines()))
    by_date = {row["date"]: float(row["Equity 2018-2"]) * 100 for row in fund}
    diffs = {
        row[0].date().isoformat(): round(row[1] - by_date[row[0].date().isoformat()], 4)
        for row in sheet.iter_rows(min_row=6, values_only=True)
        if row[0] is not None and hasattr(row[0], "year")
    }
    breaks = {day: gap for day, gap in diffs.items() if abs(gap) > 0.01}
    assert breaks == {"2025-06-30": 0.4}


def test_the_fund_copy_is_the_platforms_equity_series(
    built: Path, documents: dict[str, Path]
) -> None:
    store = store_for(built)
    series = store.get("equity_returns_larch_2018_2")
    values = series.columns["equity"]
    fund = list(csv.DictReader(documents["fund_returns"].read_text().splitlines()))
    present = [
        value for value in values if value == value
    ]  # the store holds NaN for the first month
    assert len(fund) == len(present) == 36
    assert float(fund[-1]["Equity 2018-2"]) == pytest.approx(values[-1], abs=1e-9)


def test_round_one_documents_are_the_same_on_every_build(built: Path, tmp_path: Path) -> None:
    first = build_round1.build(built, tmp_path / "a")
    second = build_round1.build(built, tmp_path / "b")
    assert first["fund_returns"].read_bytes() == second["fund_returns"].read_bytes()
    for name in ("trustee", "administrator"):
        left = [list(row) for row in load_workbook(first[name]).active.values]
        right = [list(row) for row in load_workbook(second[name]).active.values]
        assert left == right
    digest = hashlib.sha256(first["fund_returns"].read_bytes()).hexdigest()
    assert len(digest) == 64


def test_the_facility_imports_nothing_of_the_platform() -> None:
    text = (FUND / "facility" / "warehouse" / "build_larchmere_round1.py").read_text()
    assert not re.search(r"^\s*(from|import)\s+(quant_\w+|mcp|httpx2)\b", text, re.MULTILINE)


CUT = CUT  # used by the 2026-07-28 test above
