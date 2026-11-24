"""The MCP stdio transport forwards to the adapter: same tools, same results, same errors.

Every in-process check runs twice, over the 2025-11-25 handshake and over 2026-07-28, and both
must give the same answer (decision 0034).
"""

import json
import sys
from typing import Any

import anyio
import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError
from portfolio_tools import build_registry
from pydantic import BaseModel

from quant_agent_os.mcp import (
    MCPUnavailableError,
    ResourceReadError,
    build_server,
    call_tool,
    compact_schema,
    read_resource,
    run_stdio,
    served_resources,
    tool_definitions,
)
from quant_agent_os.runtime import ToolCall, ToolExecutor
from quant_agent_os.tools import (
    Permission,
    PermissionPolicy,
    PromptArgument,
    PromptSpec,
    ResourceSpec,
    ToolContractError,
    ToolRegistry,
    tool,
)

ARGUMENTS = {"series_id": "s", "returns": [0.01, -0.02, 0.03], "periods_per_year": 12}


class Query(BaseModel):
    url: str


@tool(name="fetch_page", description="Needs the network.", permissions=[Permission.NETWORK])
def fetch_page(params: Query) -> Query:
    return params


REGISTRY = ToolRegistry([*build_registry().specs(), fetch_page])
EXECUTOR = ToolExecutor(REGISTRY, PermissionPolicy())


# The 2025-11-25 initialize handshake, and the stateless revision without one.
ERAS = ("legacy", "2026-07-28")
SERVER_INFO_STAMP = "io.modelcontextprotocol/serverInfo"  # 2026-07-28 stamps each result


def _unstamped(answer: Any) -> Any:
    """``answer`` without the 2026-07-28 server-info stamp, which only that era sends."""
    if isinstance(answer, tuple | list):
        return type(answer)(_unstamped(item) for item in answer)
    if isinstance(answer, BaseModel) and isinstance(getattr(answer, "meta", None), dict):
        meta = {k: v for k, v in answer.meta.items() if k != SERVER_INFO_STAMP}  # type: ignore[attr-defined]
        return answer.model_copy(update={"meta": meta or None})
    return answer


def _in_both_eras(make_server: Any, check: Any) -> Any:
    """Run ``check`` against a fresh server in each era, and return the answer both gave."""

    async def main(mode: str) -> Any:
        async with Client(make_server(), mode=mode, cache=None) as client:
            return await check(client)

    answers = [_unstamped(anyio.run(main, mode)) for mode in ERAS]
    assert answers[0] == answers[1], f"the eras disagree: {answers}"
    return answers[0]


def _session(check: Any) -> Any:
    return _in_both_eras(
        lambda: build_server(EXECUTOR, name="test", instructions="Use the tools."), check
    )


def test_tools_list_mirrors_the_registry_with_behaviour_hints() -> None:
    async def check(session: Client) -> Any:
        return (await session.list_tools()).tools

    tools = {item.name: item for item in _session(check)}

    assert sorted(tools) == ["annualized_return", "fetch_page", "max_drawdown"]
    assert tools["max_drawdown"].annotations.read_only_hint is True
    assert tools["max_drawdown"].annotations.open_world_hint is False
    assert tools["fetch_page"].annotations.open_world_hint is True
    descriptor = REGISTRY.get("max_drawdown").descriptor()
    assert tools["max_drawdown"].input_schema == compact_schema(descriptor["input_schema"])
    assert tools["max_drawdown"].output_schema == compact_schema(descriptor["output_schema"])
    assert tools["max_drawdown"].title == "Max drawdown"


def test_tool_results_equal_direct_executor_calls() -> None:
    async def check(session: Client) -> Any:
        return await session.call_tool("annualized_return", ARGUMENTS)

    result = _session(check)
    direct = EXECUTOR.execute(ToolCall(tool="annualized_return", arguments=ARGUMENTS))

    assert result.is_error is False
    assert result.structured_content == direct.output
    assert (
        result.content[0].text
        == call_tool(EXECUTOR, "annualized_return", ARGUMENTS)["content"][0]["text"]
    )


def test_invalid_input_and_denied_calls_report_executor_errors() -> None:
    async def check(session: Client) -> Any:
        invalid = await session.call_tool("max_drawdown", {"series_id": "s", "returns": []})
        denied = await session.call_tool("fetch_page", {"url": "https://example.com"})
        unknown = await session.call_tool("place_trade", {})
        return invalid, denied, unknown

    invalid, denied, unknown = _session(check)

    assert invalid.is_error and invalid.content[0].text.startswith("invalid_input:")
    assert denied.is_error and denied.content[0].text.startswith("denied:")
    assert unknown.is_error and unknown.content[0].text.startswith("unknown_tool:")
    assert invalid.structured_content["retryable"] is True
    assert "returns" in invalid.structured_content["error"]
    assert denied.structured_content["retryable"] is False
    assert unknown.structured_content["hint"] == (
        "Call one of the available tools: annualized_return, fetch_page, max_drawdown."
    )
    assert "Hint: Call one of the available tools" in unknown.content[0].text


def test_the_server_announces_its_name_version_and_instructions() -> None:
    options = build_server(
        EXECUTOR, name="test", instructions="Use the tools."
    ).create_initialization_options()
    name, version, instructions = options.server_name, options.server_version, options.instructions

    assert name == "test" and version and instructions == "Use the tools."


def test_external_actions_are_advertised_as_open_world() -> None:
    @tool(
        name="send_order", description="Acts externally.", permissions=[Permission.EXTERNAL_ACTION]
    )
    def send_order(params: Query) -> Query:
        return params

    executor = ToolExecutor(ToolRegistry([send_order]), PermissionPolicy())
    (definition,) = tool_definitions(executor)

    assert definition["annotations"] == {
        "readOnlyHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }


def test_a_missing_extra_gives_the_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "anyio", None)

    with pytest.raises(MCPUnavailableError, match="quant-agent-os\\[server\\]"):
        run_stdio(EXECUTOR, name="test")


def test_compact_schemas_drop_derived_titles_but_keep_a_property_named_title() -> None:
    class Source(BaseModel):
        title: str

    class Report(BaseModel):
        name: str
        sources: list[Source]

    schema = compact_schema(Report.model_json_schema())

    assert "title" not in schema
    assert schema["properties"]["name"] == {"type": "string"}
    assert schema["$defs"]["Source"] == {
        "properties": {"title": {"type": "string"}},
        "required": ["title"],
        "type": "object",
    }


class Key(BaseModel):
    key: str


class Entry(BaseModel):
    key: str
    length: int


@tool(name="lookup", description="Look up an entry by key.")
def lookup(params: Key) -> Entry:
    if params.key == "missing":
        raise LookupError("No entry 'missing'.")
    return Entry(key=params.key, length=len(params.key))


CATALOG_EXECUTOR = ToolExecutor(ToolRegistry([lookup, fetch_page]), PermissionPolicy())
ENTRY = ResourceSpec(
    uri_template="test://entry/{key}", name="entry", description="An entry.", tool="lookup"
)
PAGE = ResourceSpec(
    uri_template="test://page/{url}", name="page", description="A page.", tool="fetch_page"
)
PROMPT = PromptSpec(
    name="study",
    description="Study an entry.",
    template="Read test://entry/{key} and summarise it.",
    arguments=(PromptArgument("key", "The entry key"),),
)


def _catalog_session(check: Any, **kwargs: Any) -> Any:
    return _in_both_eras(lambda: build_server(CATALOG_EXECUTOR, name="test", **kwargs), check)


def test_resources_are_read_through_their_tool_with_the_same_output() -> None:
    async def check(session: Client) -> Any:
        templates = (await session.list_resource_templates()).resource_templates
        read = await session.read_resource("test://entry/abc")  # type: ignore[arg-type]
        return templates, read

    templates, read = _catalog_session(check, resources=[ENTRY, PAGE])
    direct = CATALOG_EXECUTOR.execute(ToolCall(tool="lookup", arguments={"key": "abc"}))

    assert [item.uri_template for item in templates] == ["test://entry/{key}", "test://page/{url}"]
    assert read.contents[0].text == json.dumps(direct.output, sort_keys=True)
    assert read.contents[0].mime_type == "application/json"


def test_a_failed_or_unmatched_read_is_an_error_with_the_status_and_hint() -> None:
    with pytest.raises(ResourceReadError, match=r"error: LookupError: No entry 'missing'\. Hint:"):
        read_resource(CATALOG_EXECUTOR, [ENTRY], "test://entry/missing")
    with pytest.raises(ResourceReadError, match="denied"):
        read_resource(CATALOG_EXECUTOR, [PAGE], "test://page/example")
    with pytest.raises(ResourceReadError, match="No resource matches"):
        read_resource(CATALOG_EXECUTOR, [ENTRY], "test://other/abc")

    async def check(session: Client) -> Any:
        try:
            await session.read_resource("test://entry/missing")  # type: ignore[arg-type]
        except MCPError as error:
            return error.error.message
        return None

    assert "LookupError" in _catalog_session(check, resources=[ENTRY])


def test_resources_of_unserved_tools_are_not_listed_and_bad_templates_are_refused() -> None:
    narrowed = ToolExecutor(
        ToolRegistry([lookup, fetch_page]), PermissionPolicy(tool_allowlist=frozenset({"lookup"}))
    )
    assert served_resources(narrowed, [ENTRY, PAGE]) == [ENTRY]

    wrong = ResourceSpec(
        uri_template="test://entry/{name}", name="wrong", description="W.", tool="lookup"
    )
    with pytest.raises(ToolContractError, match="'lookup' does not take"):
        build_server(CATALOG_EXECUTOR, name="test", resources=[wrong])


def test_prompts_are_listed_and_rendered() -> None:
    async def check(session: Client) -> Any:
        listed = (await session.list_prompts()).prompts
        got = await session.get_prompt("study", {"key": "abc"})
        return listed, got

    listed, got = _catalog_session(check, prompts=[PROMPT])

    assert [(item.name, [a.name for a in item.arguments or []]) for item in listed] == [
        ("study", ["key"])
    ]
    assert got.messages[0].content.text == "Read test://entry/abc and summarise it."

    async def missing(session: Client) -> Any:
        try:
            await session.get_prompt("study", {})
        except MCPError as error:
            return error.error.message
        return None

    assert _catalog_session(missing, prompts=[PROMPT]) == "Prompt 'study' needs key."


def test_a_server_without_resources_or_prompts_does_not_advertise_them() -> None:
    plain = build_server(EXECUTOR, name="test").create_initialization_options().capabilities
    full = (
        build_server(CATALOG_EXECUTOR, name="test", resources=[ENTRY], prompts=[PROMPT])
        .create_initialization_options()
        .capabilities
    )

    assert plain.resources is None and plain.prompts is None
    assert full.resources is not None and full.prompts is not None
