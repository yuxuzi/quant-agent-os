"""The platform over HTTP (B-33, decision 0012): every toolset as MCP at /mcp and REST at /v1."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from typer.testing import CliRunner

from quant_agent_os.api import KeyRing, add_key, load_keys
from quant_agent_os.approval_desk import ApprovalDesk
from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.runs import MemoryRunStore
from quant_agent_os.tools import PermissionPolicy
from quant_platform import compose
from quant_platform.cli import app as cli
from quant_platform.http import build_http_app, single_tenant
from quant_research import ResearchApp

BASE = "http://127.0.0.1:8000"


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


def test_one_port_serves_every_toolset_as_mcp_and_rest(
    research: ResearchApp, datasets: DirectoryDatasetStore, tmp_path: Path
) -> None:
    key = add_key(tmp_path / "keys.toml", "team", ["tools:list", "tools:call"])
    store = MemoryRunStore()
    catalog = compose(policy=PermissionPolicy(), research=research.research, datasets=datasets)
    app = build_http_app(
        single_tenant(catalog, store, PermissionPolicy()),
        KeyRing(load_keys(tmp_path / "keys.toml")),
        instructions=catalog.instructions,
    )

    async def main() -> Any:
        async with _client(app, key) as client:
            rest = (await client.get("/v1/tools")).json()
            async with (
                streamable_http_client(f"{BASE}/mcp", http_client=client) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                initialised = await session.initialize()
                tools = (await session.list_tools()).tools
                prompts = (await session.list_prompts()).prompts
                report = await session.call_tool(
                    "research_report",
                    {"focus": "momentum", "returns": {"dataset": "returns", "column": "fund"}},
                )
            return rest, initialised, tools, prompts, report

    rest, initialised, tools, prompts, report = anyio.run(main)

    assert initialised.serverInfo.name == "quant-agent-os"
    assert {item["name"] for item in rest} == {tool.name for tool in tools}
    assert "research_report" in {tool.name for tool in tools}
    assert "report_on_signal" in {prompt.name for prompt in prompts}
    assert report.isError is False and report.structuredContent["metrics"]["sharpe_ratio"]
    (run,) = store.list_runs(5)
    assert (run.actor, run.principal) == ("mcp-http", "team")


def test_serve_http_refuses_to_start_without_a_key(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli,
        ["serve-http", "--toolsets", "analytics", "--keys", str(tmp_path / "none.toml")],
    )

    assert result.exit_code == 1
    assert "api-key new" in " ".join(result.output.split())


def test_the_approval_page_is_served_only_with_a_desk(
    datasets: DirectoryDatasetStore, tmp_path: Path
) -> None:
    add_key(tmp_path / "keys.toml", "team", ["tools:list"])
    keys = KeyRing(load_keys(tmp_path / "keys.toml"))
    store = MemoryRunStore()
    catalog = compose(policy=PermissionPolicy(), datasets=datasets)
    tenancy = single_tenant(catalog, store, PermissionPolicy())

    def status(app: Any) -> tuple[int, str]:
        async def main() -> httpx.Response:
            async with _client(app, "none") as client:
                return await client.get("/approve/unknown")

        response = anyio.run(main)
        return response.status_code, response.headers.get("content-security-policy", "")

    with_desk = build_http_app(
        tenancy,
        keys,
        instructions=catalog.instructions,
        desk=ApprovalDesk(store, keys, base_url="https://qaos.example"),
    )
    without = build_http_app(tenancy, keys, instructions=catalog.instructions)

    assert status(with_desk)[0] == 404 and "default-src 'none'" in status(with_desk)[1]
    assert status(without) == (404, "")
