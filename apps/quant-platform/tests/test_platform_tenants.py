"""Tenant isolation (B-34, decision 0013): two tenants on one server never see each other.

This is a release gate. Each tenant has its own knowledge store (a different corpus) and its
own datasets; both share one run store. Every path is checked: tools, resource URIs (real ones
of the other tenant, guessed ones), REST runs, and a disabled tenant.
"""

import json
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from typer.testing import CliRunner

from quant_agent_os.api import KeyRing, add_key, load_keys
from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.runs import MemoryRunStore
from quant_platform.cli import app as cli
from quant_platform.composition import instructions_for
from quant_platform.http import build_http_app
from quant_platform.tenants import (
    TenantCatalogs,
    TenantConfig,
    TenantsFileError,
    dump_tenants,
    load_tenants,
    load_tenants_file,
    save_tenants,
    separate_approver,
)
from quant_research import open_research

ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "packages" / "quant-research" / "tests" / "fixtures" / "corpus"
BASE = "http://127.0.0.1:8000"
SCOPES: list[Any] = ["tools:list", "tools:call", "runs:read"]


def _store(path: Path, document: str) -> Path:
    research = open_research(path)
    try:
        research.ingest.ingest(CORPUS / document)
    finally:
        research.close()
    return path


def _dataset(root: Path, tenant: str, dataset_id: str) -> None:
    source = root / f"{tenant}-{dataset_id}.csv"
    source.write_text("month,r\n2024-01,0.01\n2024-02,-0.02\n2024-03,0.03\n")
    DirectoryDatasetStore(root / "datasets" / tenant).put(dataset_id, source)


@pytest.fixture
def platform(tmp_path: Path) -> Iterator[tuple[Any, dict[str, str], MemoryRunStore]]:
    tenants = {
        "alpha": TenantConfig(
            store=str(_store(tmp_path / "alpha.db", "reversal-and-microstructure.md")),
            toolsets=["research", "analytics"],
        ),
        "beta": TenantConfig(
            store=str(_store(tmp_path / "beta.db", "momentum-crashes.md")),
            toolsets=["research", "analytics"],
        ),
        "gamma": TenantConfig(toolsets=["analytics"], disabled=True),
    }
    _dataset(tmp_path, "alpha", "alpha-fund")
    _dataset(tmp_path, "beta", "beta-fund")
    keys_path = tmp_path / "keys.toml"
    issued = {
        tenant: add_key(keys_path, f"{tenant}-user", SCOPES, tenant=tenant)
        for tenant in ("alpha", "beta", "gamma")
    }
    store = MemoryRunStore()
    catalogs = TenantCatalogs(tenants, run_store=store, datasets_root=tmp_path / "datasets")
    app = build_http_app(
        catalogs,
        KeyRing(load_keys(keys_path)),
        instructions=instructions_for(["research", "analytics"]),
    )
    yield app, issued, store
    catalogs.close()


@asynccontextmanager
async def _client(app: Any, key: str) -> AsyncIterator[httpx.AsyncClient]:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=BASE,
            headers={"Authorization": f"Bearer {key}"},
        ) as client,
    ):
        yield client


def _mcp(app: Any, key: str, check: Any) -> Any:
    async def main() -> Any:
        async with (
            _client(app, key) as client,
            streamable_http_client(f"{BASE}/mcp", http_client=client) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            return await check(session)

    return anyio.run(main)


async def _read(session: ClientSession, uri: str) -> Any:
    try:
        result = await session.read_resource(uri)  # type: ignore[arg-type]
    except Exception as error:  # McpError, possibly wrapped by the task group
        return f"error: {error}"
    return json.loads(result.contents[0].text)  # type: ignore[union-attr]


def _sources(packet: dict[str, Any]) -> set[str]:
    return {passage["source"]["source_id"] for passage in packet["passages"]}


def test_each_tenant_searches_only_its_own_corpus(platform: Any) -> None:
    app, issued, _ = platform

    async def search(session: ClientSession) -> Any:
        found = await session.call_tool("find_related_signals", {"query": "momentum reversal"})
        return found.structuredContent

    alpha = _mcp(app, issued["alpha"], search)
    beta = _mcp(app, issued["beta"], search)

    assert _sources(alpha) == {"reversal-and-microstructure.md"}
    assert _sources(beta) == {"momentum-crashes.md"}
    assert alpha["corpus_snapshot"] != beta["corpus_snapshot"]


def test_another_tenants_passage_and_dataset_cannot_be_read_even_by_their_real_ids(
    platform: Any,
) -> None:
    app, issued, _ = platform

    async def own_chunk(session: ClientSession) -> Any:
        found = await session.call_tool("find_related_signals", {"query": "momentum"})
        return found.structuredContent["passages"][0]["chunk_id"]  # type: ignore[index]

    beta_chunk = _mcp(app, issued["beta"], own_chunk)

    async def probe(session: ClientSession) -> Any:
        passage_by_tool = await session.call_tool("get_source_passage", {"chunk_id": beta_chunk})
        return (
            passage_by_tool,
            await _read(session, f"qaos://passage/{beta_chunk}"),
            await _read(session, "qaos://dataset/beta-fund"),
            await _read(session, "qaos://dataset/alpha-fund"),
            (await session.call_tool("list_datasets", {})).structuredContent,
        )

    by_tool, by_uri, beta_dataset, alpha_dataset, listed = _mcp(app, issued["alpha"], probe)

    assert by_tool.isError and "No passage" in by_tool.content[0].text
    assert isinstance(by_uri, str) and "No passage" in by_uri
    assert isinstance(beta_dataset, str) and "No dataset 'beta-fund'" in beta_dataset
    assert alpha_dataset["datasets"][0]["id"] == "alpha-fund"
    assert [item["id"] for item in listed["datasets"]] == ["alpha-fund"]


def test_runs_are_recorded_and_read_per_tenant(platform: Any) -> None:
    app, issued, store = platform

    async def call(session: ClientSession) -> Any:
        return await session.call_tool("list_datasets", {})

    _mcp(app, issued["alpha"], call)
    _mcp(app, issued["beta"], call)

    async def runs(key: str) -> list[dict[str, Any]]:
        async with _client(app, key) as client:
            return list((await client.get("/v1/runs")).json())

    alpha_runs = anyio.run(runs, issued["alpha"])
    assert [(run["tenant"], run["principal"]) for run in alpha_runs] == [("alpha", "alpha-user")]
    assert {run.tenant for run in store.list_runs(10)} == {"alpha", "beta"}
    ever = (datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC))
    [alpha_usage] = store.list_usage(*ever, tenant="alpha")
    assert (alpha_usage.tool, alpha_usage.toolset) == ("list_datasets", "analytics")
    assert {event.tenant for event in store.list_usage(*ever)} == {"alpha", "beta"}


def test_a_disabled_tenant_reaches_nothing(platform: Any) -> None:
    app, issued, _ = platform

    async def tools(key: str) -> int:
        async with _client(app, key) as client:
            return (await client.get("/v1/tools")).status_code

    assert anyio.run(tools, issued["gamma"]) == 403
    assert anyio.run(tools, issued["alpha"]) == 200


def test_the_tenants_file_round_trips_and_refuses_bad_entries(tmp_path: Path) -> None:
    path = tmp_path / "tenants.toml"
    tenants = {
        "acme": TenantConfig(
            store="postgresql://qaos_reader@db/qaos",
            toolsets=["research"],
            granted_permissions=frozenset({"read_local_data", "network"}),  # type: ignore[arg-type]
            issuer="https://login.acme.example",
        ),
        "beta-co": TenantConfig(disabled=True),
        "win": TenantConfig(store='C:\\data\\kb "q".db', datasets=Path("C:\\data\\sets")),
    }
    save_tenants(path, tenants)

    assert load_tenants(path) == tenants
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600
    assert "[tenants.acme]" in dump_tenants(tenants)
    path.write_text('[tenants."Acme Corp"]\n')
    with pytest.raises(TenantsFileError, match="Tenant id"):
        load_tenants(path)
    path.write_text('[tenants.acme]\nstore = "x"\nshell = "rm -rf"\n')
    with pytest.raises(TenantsFileError, match="shell"):
        load_tenants(path)


def test_the_tenant_commands_add_list_and_disable(tmp_path: Path) -> None:
    path = tmp_path / "tenants.toml"
    runner = CliRunner()

    added = runner.invoke(
        cli,
        [
            "tenant",
            "add",
            "acme",
            "--store",
            "postgresql://reader:hunter2@db/qaos",
            "--toolsets",
            "research",
            "--tenants",
            str(path),
        ],
    )
    listed = runner.invoke(cli, ["tenant", "list", "--tenants", str(path)])
    disabled = runner.invoke(cli, ["tenant", "disable", "acme", "--tenants", str(path)])
    refused = runner.invoke(cli, ["tenant", "add", "Bad Name", "--tenants", str(path)])

    assert added.exit_code == 0 and "--schema qr_acme" in added.output
    assert listed.exit_code == 0 and "acme" in listed.output and "hunter2" not in listed.output
    assert disabled.exit_code == 0 and load_tenants(path)["acme"].disabled
    assert refused.exit_code == 1


OAUTH_FILE = """
[[authorization_servers]]
issuer = "https://login.acme.example"
introspection_url = "https://login.acme.example/introspect"
client_id = "qaos"
client_secret_env = "QAOS_TEST_ACME_SECRET"

[tenants.acme]
toolsets = ["analytics"]
issuer = "https://login.acme.example"
"""


def test_authorization_servers_survive_edits_and_pin_issuers(tmp_path: Path) -> None:
    path = tmp_path / "tenants.toml"
    path.write_text(OAUTH_FILE)

    added = CliRunner().invoke(
        cli, ["tenant", "add", "beta", "--toolsets", "analytics", "--tenants", str(path)]
    )
    whole = load_tenants_file(path)

    assert added.exit_code == 0
    assert [server.issuer for server in whole.authorization_servers] == [
        "https://login.acme.example"
    ]
    assert whole.tenants_by_issuer() == {"https://login.acme.example": {"acme"}}
    assert "s3cret" not in path.read_text()
    path.write_text(OAUTH_FILE + '\n[tenants.rogue]\nissuer = "https://evil.example"\n')
    with pytest.raises(TenantsFileError, match="not among the authorization_servers"):
        load_tenants_file(path)


def test_serve_http_needs_the_resource_url_and_the_secret_for_oauth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "tenants.toml"
    path.write_text(OAUTH_FILE)
    add_key(tmp_path / "keys.toml", "svc", ["tools:list"], tenant="acme")
    base = ["serve-http", "--tenants", str(path), "--keys", str(tmp_path / "keys.toml")]
    monkeypatch.delenv("QAOS_TEST_ACME_SECRET", raising=False)

    no_url = CliRunner().invoke(cli, base)
    no_secret = CliRunner().invoke(cli, [*base, "--resource-url", "https://qaos.example/mcp"])

    assert no_url.exit_code == 2 and "--resource-url" in no_url.output
    assert no_secret.exit_code == 1 and "QAOS_TEST_ACME_SECRET" in no_secret.output


def test_four_eyes_follows_each_tenants_setting() -> None:
    needs = separate_approver(
        {
            "acme": TenantConfig(),
            "solo": TenantConfig(separate_approver=False),
            "local": TenantConfig(separate_approver=True),
        }
    )

    assert needs("acme") and not needs("solo") and needs("local")
    assert needs("unlisted") and not separate_approver({})("local")
    assert "separate_approver = false" in dump_tenants(
        {"solo": TenantConfig(separate_approver=False)}
    )
