"""MCP Apps views (decision 0036): a tool links its page, the page is a ui:// resource, and every
other answer stays as it was.

Every in-process check runs over the 2025-11-25 handshake and over 2026-07-28, and both must give
the same answer (decision 0034).
"""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import httpx2
import pytest
from mcp import Client
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from portfolio_tools import build_registry

from quant_agent_os.api import KeyRing, add_key, build_app, load_keys
from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.mcp import build_server, served_views, tool_definitions
from quant_agent_os.mcp.http import KeyVerifier, add_mcp_route
from quant_agent_os.mcp.server import Served
from quant_agent_os.runs import MemoryRunStore, RunRecorder
from quant_agent_os.runtime import ToolCall, ToolExecutor
from quant_agent_os.serving import SingleTenant, Surface
from quant_agent_os.tools import PermissionPolicy, ToolContractError, ToolRegistry, ViewSpec
from quant_analytics.computed import METRIC_KINDS as ANALYTICS_KINDS
from quant_analytics.tools import analytics_tools
from quant_analytics.views import PERFORMANCE_VIEW
from quant_platform import compose
from quant_toolbox.report import metric_kind

ERAS = ("legacy", "2026-07-28")
BASE = "http://127.0.0.1:8000"
UI_EXTENSION = "io.modelcontextprotocol/ui"
APP_MIME_TYPE = "text/html;profile=mcp-app"
ARGUMENTS = {"series_id": "s", "returns": [0.01, -0.02, 0.03], "periods_per_year": 12}
PAGE = "<!doctype html><title>Returns</title><p>A page.</p>"
VIEW = ViewSpec(
    uri="ui://test/returns.html",
    name="returns",
    description="The annualised return, as a page.",
    html=PAGE,
    tools=("annualized_return",),
)
EXECUTOR = ToolExecutor(ToolRegistry(build_registry().specs()), PermissionPolicy())


def _session(check: Any, *, executor: ToolExecutor = EXECUTOR, **kwargs: Any) -> Any:
    """Run ``check`` against a fresh server in each era; both must answer the same."""

    async def run(mode: str) -> Any:
        server = build_server(executor, name="test", instructions="Use the tools.", **kwargs)
        async with Client(server, mode=mode, cache=None) as client:
            return await check(client)

    first, second = (anyio.run(run, mode) for mode in ERAS)
    assert first == second, f"the eras disagree: {first} != {second}"
    return first


def test_a_tool_with_a_view_links_it_and_keeps_its_other_meta() -> None:
    async def check(session: Client) -> Any:
        tools = {item.name: item.meta for item in (await session.list_tools()).tools}
        return tools

    meta = _session(check, views=[VIEW])

    assert meta["annualized_return"]["ui"] == {"resourceUri": VIEW.uri}
    assert (
        meta["annualized_return"]["qaos/version"]
        == EXECUTOR.registry.get("annualized_return").version
    )
    assert "ui" not in meta["max_drawdown"]
    assert "qaos/version" in meta["max_drawdown"]


def test_without_views_no_tool_has_ui_metadata_and_resources_are_not_offered() -> None:
    async def check(session: Client) -> Any:
        return [item.meta for item in (await session.list_tools()).tools]

    metas = _session(check)

    assert all("ui" not in (meta or {}) for meta in metas)
    plain = build_server(EXECUTOR, name="test").create_initialization_options().capabilities
    assert plain.resources is None


def test_the_view_is_listed_with_the_apps_mime_type() -> None:
    async def check(session: Client) -> Any:
        return [
            (item.uri, item.name, item.description, item.mime_type)
            for item in (await session.list_resources()).resources
        ]

    listed = _session(check, views=[VIEW])

    assert listed == [(VIEW.uri, VIEW.name, VIEW.description, APP_MIME_TYPE)]


def test_reading_the_view_returns_its_page_and_calls_no_tool() -> None:
    async def check(session: Client) -> Any:
        read = await session.read_resource(VIEW.uri)  # type: ignore[arg-type]
        return read.contents[0].text, read.contents[0].mime_type

    assert _session(check, views=[VIEW]) == (PAGE, APP_MIME_TYPE)


def _runs_after_a_view_read_and_a_call(mode: str) -> tuple[int, int]:
    store = MemoryRunStore()
    recorder = RunRecorder(store, actor="test")
    server = build_server(EXECUTOR, name="test", recorder=recorder, views=[VIEW])

    async def run() -> tuple[int, int]:
        async with Client(server, mode=mode, cache=None) as client:
            await client.read_resource(VIEW.uri)  # type: ignore[arg-type]
            after_read = len(store.list_runs())
            await client.call_tool("annualized_return", ARGUMENTS)
            return after_read, len(store.list_runs())

    return anyio.run(run)


def test_reading_a_view_records_no_run_and_a_tool_call_does() -> None:
    for mode in ERAS:
        assert _runs_after_a_view_read_and_a_call(mode) == (0, 1), mode


def test_a_caller_not_served_the_tool_sees_neither_the_view_nor_its_link() -> None:
    narrow = Surface(
        ToolExecutor(
            ToolRegistry(build_registry().specs()),
            PermissionPolicy(tool_allowlist=frozenset({"max_drawdown"})),
        ),
        views=[VIEW],
    )

    async def check(session: Client) -> Any:
        tools = [item.name for item in (await session.list_tools()).tools]
        resources = [item.uri for item in (await session.list_resources()).resources]
        try:
            await session.read_resource(VIEW.uri)  # type: ignore[arg-type]
        except MCPError as error:
            return tools, resources, error.error.message
        return tools, resources, None

    async def run(mode: str) -> Any:
        server = build_server(
            None,
            name="test",
            route=lambda request, scope: Served(narrow),
        )
        async with Client(server, mode=mode, cache=None) as client:
            return await check(client)

    for mode in ERAS:
        tools, resources, read_error = anyio.run(run, mode)
        assert "annualized_return" not in tools
        assert resources == []
        assert read_error is not None and "No resource matches" in read_error


def test_a_route_serves_each_caller_its_own_views() -> None:
    allowed = Surface(EXECUTOR, views=[VIEW])
    narrow = Surface(
        ToolExecutor(
            ToolRegistry(build_registry().specs()),
            PermissionPolicy(tool_allowlist=frozenset({"max_drawdown"})),
        ),
        views=[VIEW],
    )
    surfaces = {"allowed": allowed, "narrow": narrow}

    async def run(mode: str, tenant: str) -> list[str]:
        server = build_server(
            None,
            name="test",
            route=lambda request, scope: Served(surfaces[tenant]),
        )
        async with Client(server, mode=mode, cache=None) as client:
            return [item.uri for item in (await client.list_resources()).resources]

    for mode in ERAS:
        assert anyio.run(run, mode, "allowed") == [VIEW.uri]
        assert anyio.run(run, mode, "narrow") == []


def test_the_tools_text_and_structure_are_unchanged_by_the_view() -> None:
    async def check(session: Client) -> Any:
        result = await session.call_tool("annualized_return", ARGUMENTS)
        return result.content[0].text, result.structured_content, result.is_error

    with_view = _session(check, views=[VIEW])
    without_view = _session(check)

    assert with_view == without_view
    assert with_view[2] is False


@asynccontextmanager
async def _http(tmp_path: Path, surface: Surface) -> AsyncIterator[httpx2.AsyncClient]:
    """An HTTP app that serves ``surface`` to one key, with the client it is called with."""
    path = tmp_path / "keys.toml"
    key = add_key(path, "reader", ["tools:list", "tools:call"])
    keys = KeyRing(load_keys(path))
    tenancy = SingleTenant(surface)
    app = build_app(None, keys, None, title="test", tenancy=tenancy)
    add_mcp_route(app, tenancy, name="test", verifier=KeyVerifier(keys))
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url=BASE,
            headers={"Authorization": f"Bearer {key}"},
        ) as client,
    ):
        yield client


def test_the_apps_extension_is_advertised_to_2026_07_28_clients(tmp_path: Path) -> None:
    async def main() -> tuple[Any, Any]:
        async with (
            _http(tmp_path, Surface(EXECUTOR, views=[VIEW])) as http,
            Client(
                streamable_http_client(f"{BASE}/mcp", http_client=http), mode="auto", cache=None
            ) as client,
        ):
            modern = client.server_capabilities.extensions
            tools = {item.name: item.meta or {} for item in (await client.list_tools()).tools}
        return modern, tools

    modern, tools = anyio.run(main)

    assert modern == {UI_EXTENSION: {}}
    assert tools["annualized_return"]["ui"] == {"resourceUri": VIEW.uri}
    assert "ui" not in tools["max_drawdown"]


def test_the_2025_11_25_handshake_cannot_carry_the_extension(tmp_path: Path) -> None:
    # The 2025-11-25 InitializeResult schema has no extensions field, so the SDK drops the
    # advertisement from that handshake (decision 0036). The view and its link still reach the
    # client: they are ordinary resources and tool metadata.
    async def main() -> tuple[Any, Any]:
        async with (
            _http(tmp_path, Surface(EXECUTOR, views=[VIEW])) as http,
            streamable_http_client(f"{BASE}/mcp", http_client=http) as (read, write),
            ClientSession(read, write) as session,
        ):
            initialized = await session.initialize()
            listed = [item.uri for item in (await session.list_resources()).resources]
        return initialized.capabilities.extensions, listed

    extensions, listed = anyio.run(main)

    assert extensions is None
    assert listed == [VIEW.uri]


def test_a_view_needs_a_ui_uri_a_description_and_a_tool() -> None:
    with pytest.raises(ToolContractError, match="ui://"):
        ViewSpec(
            uri="https://example.com/page.html",
            name="page",
            description="A page.",
            html=PAGE,
            tools=("annualized_return",),
        )
    with pytest.raises(ToolContractError, match="ui://"):
        ViewSpec(uri="ui://", name="page", description="A page.", html=PAGE, tools=("x",))
    with pytest.raises(ToolContractError, match="needs a description"):
        ViewSpec(uri=VIEW.uri, name="page", description=" ", html=PAGE, tools=("x",))
    with pytest.raises(ToolContractError, match="renders no tool"):
        ViewSpec(uri=VIEW.uri, name="page", description="A page.", html=PAGE, tools=())


def test_a_view_is_served_only_while_one_of_its_tools_is() -> None:
    narrow = ToolExecutor(
        ToolRegistry(build_registry().specs()),
        PermissionPolicy(tool_allowlist=frozenset({"max_drawdown"})),
    )
    both = ViewSpec(
        uri="ui://test/both.html",
        name="both",
        description="Two tools.",
        html=PAGE,
        tools=("annualized_return", "max_drawdown"),
    )

    assert served_views(narrow, [VIEW]) == []
    assert served_views(narrow, [VIEW, both]) == [both]
    assert served_views(EXECUTOR, [VIEW, both]) == [VIEW, both]


def test_a_tool_named_by_two_views_links_the_first() -> None:
    second = ViewSpec(
        uri="ui://test/second.html",
        name="second",
        description="Another page.",
        html=PAGE,
        tools=("annualized_return",),
    )

    links = {
        definition["name"]: definition["_meta"].get("ui")
        for definition in tool_definitions(EXECUTOR, [VIEW, second])
    }

    assert links["annualized_return"] == {"resourceUri": VIEW.uri}
    assert links["max_drawdown"] is None


def test_the_platform_offers_the_performance_view_with_analyze_performance(tmp_path: Path) -> None:
    catalog = compose(policy=PermissionPolicy(), datasets=DirectoryDatasetStore(tmp_path))
    executor = ToolExecutor(ToolRegistry(catalog.tools), catalog.policy)

    async def check(session: Client) -> Any:
        tools = {item.name: item.meta or {} for item in (await session.list_tools()).tools}
        resources = [str(item.uri) for item in (await session.list_resources()).resources]
        page = await session.read_resource(PERFORMANCE_VIEW.uri)  # type: ignore[arg-type]
        called = await session.call_tool("analyze_performance", ARGUMENTS)
        return (
            tools["analyze_performance"]["ui"],
            resources,
            page.contents[0].text,
            called.is_error,
        )

    ui, resources, page, is_error = _session(check, executor=executor, views=catalog.views)

    assert ui == {"resourceUri": PERFORMANCE_VIEW.uri}
    assert resources == [PERFORMANCE_VIEW.uri]
    assert page == PERFORMANCE_VIEW.html
    assert is_error is False
    assert compose(policy=PermissionPolicy()).views == []  # no datasets, no analytics toolset


# The page is one static file: its protocol, its safety, and its formatting, checked as text.
PAGE_METHODS = (
    "ui/initialize",
    "ui/notifications/initialized",
    "ui/notifications/tool-result",
    "ui/notifications/host-context-changed",
    "ui/notifications/size-changed",
    "ui/resource-teardown",
)


def test_the_page_speaks_the_apps_protocol() -> None:
    html = PERFORMANCE_VIEW.html

    for method in PAGE_METHODS:
        assert method in html, method
    assert '"2026-01-26"' in html
    assert "window.parent.postMessage" in html
    assert "event.source !== window.parent" in html


def test_the_page_loads_nothing_and_renders_text_as_text() -> None:
    html = PERFORMANCE_VIEW.html

    assert "http://" not in html and "https://" not in html
    assert "fetch(" not in html and "XMLHttpRequest" not in html
    assert "<link" not in html and "@import" not in html and "url(" not in html
    assert "innerHTML" not in html and "insertAdjacentHTML" not in html and "eval(" not in html
    assert "textContent" in html
    assert len(html.encode("utf-8")) <= 14 * 1024


def test_the_page_formats_each_metric_as_the_toolbox_does() -> None:
    html = PERFORMANCE_VIEW.html

    def listed(name: str) -> set[str]:
        match = re.search(rf"var {name} = \[(.*?)\];", html, re.DOTALL)
        assert match is not None, name
        return set(re.findall(r'"([a-z_]+)"', match.group(1)))

    # Every metric analyze_performance returns, with a benchmark, so each kind is exercised.
    executor = ToolExecutor(ToolRegistry(analytics_tools(None).specs()), PermissionPolicy())
    output = executor.execute(
        ToolCall(
            tool="analyze_performance",
            arguments={**ARGUMENTS, "benchmark": [0.005, -0.01, 0.002]},
        )
    ).output
    metrics = set(output["metrics"])  # type: ignore[index]
    kinds = {name: metric_kind(name, ANALYTICS_KINDS) for name in metrics}

    assert {n for n, k in kinds.items() if k == "percent"} == listed("PERCENT") & metrics
    assert {n for n, k in kinds.items() if k == "count"} == listed("COUNT") & metrics
    assert "minimum_track_record" in metrics and "best_period" in metrics


BENCHMARK = [0.005, -0.01, 0.002]
HEADLINE = ["annualized_return", "annualized_volatility", "sharpe_ratio", "max_drawdown"]


def _analysis(arguments: dict[str, Any]) -> dict[str, Any]:
    """The structured result analyze_performance gives for ``arguments``, from the platform."""
    executor = ToolExecutor(ToolRegistry(analytics_tools(None).specs()), PermissionPolicy())
    record = executor.execute(ToolCall(tool="analyze_performance", arguments=arguments))
    assert record.status == "ok", record.error
    return record.output  # type: ignore[return-value]


def _groups(html: str) -> list[tuple[str, list[str]]]:
    """The page's metric groups: each title with the metric names it lists, in order."""
    body = re.search(r"var GROUPS = \[(.*?)\];\n", html, re.DOTALL)
    assert body is not None, "GROUPS"
    return [
        (title, re.findall(r'"([a-z_]+)"', names))
        for title, names in re.findall(r'\["([^"]+)", \[([^\]]*)\]\]', body.group(1))
    ]


def test_the_page_shows_four_figures_first_then_groups_each_metric_once() -> None:
    html = PERFORMANCE_VIEW.html
    headline = re.search(r"var HEADLINE = \[(.*?)\];", html)
    assert headline is not None
    assert re.findall(r'"([a-z_]+)"', headline.group(1)) == HEADLINE

    groups = _groups(html)
    assert [title for title, _ in groups] == [
        "Returns",
        "Risk",
        "Ratios",
        "Against the benchmark",
        "Statistical confidence",
    ]
    grouped = [name for _, names in groups for name in names]
    assert len(grouped) == len(set(grouped)), "a metric is in two groups"
    assert set(grouped).isdisjoint(HEADLINE)
    # Every metric the tool returns with a benchmark is a figure or in a group, and no other is.
    returned = set(_analysis({**ARGUMENTS, "benchmark": BENCHMARK})["metrics"])
    assert set(grouped) | set(HEADLINE) == returned


def test_the_benchmark_group_holds_exactly_the_metrics_only_a_benchmark_adds() -> None:
    plain = set(_analysis(ARGUMENTS)["metrics"])
    with_benchmark = set(_analysis({**ARGUMENTS, "benchmark": BENCHMARK})["metrics"])
    group = dict(_groups(PERFORMANCE_VIEW.html))["Against the benchmark"]

    assert set(group) == with_benchmark - plain
    assert set(group) == {
        "active_return",
        "alpha",
        "beta",
        "correlation",
        "down_capture",
        "information_ratio",
        "tracking_error",
        "up_capture",
    }


def test_the_basis_names_only_conventions_the_tool_states() -> None:
    html = PERFORMANCE_VIEW.html
    match = re.search(r"var BASIS = (\[.*\]);", html)
    assert match is not None
    pairs = re.findall(r'\["([^"]+)", "([a-z_]+)"\]', match.group(1))
    conventions = _analysis(ARGUMENTS)["conventions"]

    assert [title for title, _ in pairs] == [
        "Source",
        "Returns",
        "Periods per year",
        "Value at risk",
        "Sharpe ratio and risk-free rate",
    ]
    for _, key in pairs:
        assert key in conventions, key
    assert "Basis" in html and "Against the benchmark" in html


def test_the_page_titles_the_result_with_its_series_and_names_its_window() -> None:
    html = PERFORMANCE_VIEW.html

    assert 'el("h1", data.series_id)' in html
    assert "data.periods" in html and "data.periods_per_year" in html
