"""MCP over Streamable HTTP (B-33, B-34): same results as stdio and REST, per credential tenant."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.provider import AccessToken
from portfolio_tools import build_registry
from starlette.applications import Starlette

from quant_agent_os.api import KeyRing, add_key, build_app, load_keys
from quant_agent_os.mcp import call_tool
from quant_agent_os.mcp.http import KeyVerifier, add_mcp_route
from quant_agent_os.runs import MemoryRunStore
from quant_agent_os.runtime import PlanStep, ToolExecutor
from quant_agent_os.serving import SingleTenant, Surface
from quant_agent_os.tools import PermissionPolicy, ResourceSpec

ARGUMENTS = {"series_id": "s", "returns": [0.01, -0.02, 0.03], "periods_per_year": 12}
REGISTRY = build_registry()
EXECUTOR = ToolExecutor(REGISTRY, PermissionPolicy())
DRAWDOWN = ResourceSpec(
    uri_template="test://drawdown/{series_id}",
    name="drawdown",
    description="Reads max_drawdown; its returns argument is missing, so a read fails.",
    tool="max_drawdown",
)
BASE = "http://127.0.0.1:8000"


class Tenants:
    """local serves both tools and the drawdown resource; acme serves only annualized_return."""

    def __init__(self, store: MemoryRunStore) -> None:
        self.surfaces = {
            "local": Surface(
                ToolExecutor(REGISTRY, PermissionPolicy(), approvals=store),
                resources=[DRAWDOWN],
                run_store=store,
            ),
            "acme": Surface(
                ToolExecutor(
                    REGISTRY, PermissionPolicy(tool_allowlist=frozenset({"annualized_return"}))
                ),
                resources=[DRAWDOWN],  # its tool is not served to acme, so neither is this
                run_store=store,
            ),
        }

    def surface(self, tenant: str) -> Surface | None:
        return self.surfaces.get(tenant)


@pytest.fixture
def server(tmp_path: Path) -> tuple[Any, dict[str, str], MemoryRunStore]:
    path = tmp_path / "keys.toml"
    issued = {
        "alice": add_key(path, "alice", ["tools:list", "tools:call"]),
        "lister": add_key(path, "lister", ["tools:list"]),
        "acme": add_key(path, "bo", ["tools:list", "tools:call"], tenant="acme"),
        "globex": add_key(path, "cy", ["tools:list", "tools:call"], tenant="globex"),
    }
    keys = KeyRing(load_keys(path))
    store = MemoryRunStore()
    tenancy = Tenants(store)
    app = build_app(None, keys, None, tenancy=tenancy)
    add_mcp_route(app, tenancy, name="test", verifier=KeyVerifier(keys))
    return app, issued, store


@asynccontextmanager
async def _http(app: Any, headers: dict[str, str]) -> AsyncIterator[httpx.AsyncClient]:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=BASE, headers=headers
        ) as client,
    ):
        yield client


def _session(app: Any, key: str, check: Any) -> Any:
    async def main() -> Any:
        async with (
            _http(app, {"Authorization": f"Bearer {key}"}) as client,
            streamable_http_client(f"{BASE}/mcp", http_client=client) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            return await check(session)

    return anyio.run(main)


def _post(app: Any, headers: dict[str, str]) -> httpx.Response:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}

    async def main() -> httpx.Response:
        async with _http(app, headers) as client:
            return await client.post(
                "/mcp",
                json=body,
                headers={"Accept": "application/json, text/event-stream"},
            )

    return anyio.run(main)


def test_http_results_equal_direct_and_stdio_calls_and_name_the_caller(server: Any) -> None:
    app, issued, store = server

    async def check(session: ClientSession) -> Any:
        tools = (await session.list_tools()).tools
        result = await session.call_tool("annualized_return", ARGUMENTS)
        return tools, result

    tools, result = _session(app, issued["alice"], check)
    direct = EXECUTOR.execute(PlanStep(tool="annualized_return", arguments=ARGUMENTS))

    assert sorted(item.name for item in tools) == ["annualized_return", "max_drawdown"]
    assert result.isError is False
    assert result.structuredContent == direct.output
    assert (
        result.content[0].text
        == call_tool(EXECUTOR, "annualized_return", ARGUMENTS)["content"][0]["text"]
    )
    (run,) = store.list_runs(5)
    assert (run.actor, run.principal) == ("mcp-http", "alice")


def test_a_missing_or_unknown_key_is_refused_with_401(server: Any) -> None:
    app, _, store = server

    missing = _post(app, {})
    unknown = _post(app, {"Authorization": "Bearer qaos_not-a-key"})

    assert missing.status_code == 401 and "Bearer" in missing.headers["www-authenticate"]
    assert unknown.status_code == 401
    assert store.list_runs(5) == []


def test_each_method_checks_the_keys_scope(server: Any) -> None:
    app, issued, store = server

    async def check(session: ClientSession) -> Any:
        listed = (await session.list_tools()).tools
        called = await session.call_tool("annualized_return", ARGUMENTS)
        return listed, called

    listed, called = _session(app, issued["lister"], check)

    assert listed
    assert called.isError and "lacks the 'tools:call' scope" in called.content[0].text
    assert store.list_runs(5) == []


def test_resource_reads_need_tools_call_and_report_failures(server: Any) -> None:
    app, issued, _ = server

    async def check(session: ClientSession) -> Any:
        try:
            await session.read_resource("test://drawdown/s")  # type: ignore[arg-type]
        except Exception as error:  # McpError, possibly wrapped by the task group
            return str(error)
        return None

    assert "invalid_input" in _session(app, issued["alice"], check)
    assert "lacks the 'tools:call' scope" in _session(app, issued["lister"], check)


def test_a_foreign_host_or_origin_is_refused(server: Any) -> None:
    app, issued, _ = server
    auth = {"Authorization": f"Bearer {issued['alice']}"}

    foreign_host = _post(app, {**auth, "Host": "evil.example"})
    foreign_origin = _post(app, {**auth, "Origin": "https://evil.example"})
    loopback = _post(app, {**auth, "Origin": "http://localhost:3000"})

    assert foreign_host.status_code in {400, 403, 421}
    assert foreign_origin.status_code == 403
    assert loopback.status_code == 200


def test_rest_still_answers_on_the_same_app(server: Any) -> None:
    app, issued, _ = server

    async def main() -> httpx.Response:
        async with _http(app, {"Authorization": f"Bearer {issued['alice']}"}) as client:
            return await client.get("/v1/tools")

    response = anyio.run(main)

    assert response.status_code == 200
    assert {item["name"] for item in response.json()} == {"annualized_return", "max_drawdown"}


def test_keys_never_reach_the_logs(server: Any, caplog: pytest.LogCaptureFixture) -> None:
    app, issued, _ = server
    caplog.set_level(logging.DEBUG)

    _post(app, {"Authorization": f"Bearer {issued['alice']}"})
    _post(app, {"Authorization": "Bearer qaos_wrong-key-value"})

    assert issued["alice"] not in caplog.text
    assert "qaos_wrong-key-value" not in caplog.text


def test_each_caller_is_served_its_own_tenant(server: Any) -> None:
    app, issued, store = server

    async def check(session: ClientSession) -> Any:
        tools = [tool.name for tool in (await session.list_tools()).tools]
        templates = (await session.list_resource_templates()).resourceTemplates
        hidden = await session.call_tool("max_drawdown", ARGUMENTS)
        shown = await session.call_tool("annualized_return", ARGUMENTS)
        return tools, templates, hidden, shown

    tools, templates, hidden, shown = _session(app, issued["acme"], check)

    assert tools == ["annualized_return"]
    assert templates == []
    assert hidden.isError and "denied" in hidden.content[0].text
    assert shown.isError is False
    assert [(run.principal, run.tenant) for run in store.list_runs(5)] == [
        ("bo", "acme"),
        ("bo", "acme"),
    ]


def test_a_tenant_not_served_here_reaches_nothing(server: Any) -> None:
    app, issued, _ = server

    async def check(session: ClientSession) -> Any:
        try:
            await session.list_tools()
        except Exception as error:  # McpError, possibly wrapped by the task group
            return str(error)
        return None

    assert "Tenant 'globex' is not served here" in _session(app, issued["globex"], check)


def test_a_credential_without_a_tenant_is_refused() -> None:
    class NoTenant:
        async def verify_token(self, token: str) -> AccessToken | None:
            return AccessToken(token=token, client_id="x", scopes=["tools:list"])

    app = Starlette()
    add_mcp_route(app, SingleTenant(Surface(EXECUTOR)), name="test", verifier=NoTenant())

    async def check(session: ClientSession) -> Any:
        try:
            await session.list_tools()
        except Exception as error:
            return str(error)
        return None

    assert "names no tenant" in _session(app, "any-token", check)
