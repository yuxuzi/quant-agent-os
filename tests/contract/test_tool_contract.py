"""Contract tests: every registered tool honours the contract, over direct calls and MCP alike."""

import json

import pytest
from portfolio_tools import build_registry

from quant_agent_os.mcp import call_tool, tool_definitions
from quant_agent_os.runtime import PlanStep, ToolExecutor
from quant_agent_os.tools import PermissionPolicy

REGISTRY = build_registry()
EXECUTOR = ToolExecutor(REGISTRY, PermissionPolicy())
ARGUMENTS = {"series_id": "s", "returns": [0.01, -0.02, 0.03], "periods_per_year": 12}


@pytest.mark.parametrize("spec", REGISTRY.specs(), ids=lambda spec: spec.name)
def test_registered_tools_declare_a_complete_contract(spec: object) -> None:
    descriptor = REGISTRY.get(spec.name).descriptor()  # type: ignore[attr-defined]

    assert descriptor["description"]
    assert descriptor["input_schema"]["type"] == "object"
    assert {"warnings", "evidence"} <= set(descriptor["output_schema"]["properties"])
    json.dumps(descriptor)


def test_mcp_definitions_mirror_the_registry() -> None:
    definitions = tool_definitions(EXECUTOR)

    assert [item["name"] for item in definitions] == [spec.name for spec in REGISTRY.specs()]
    assert all(item["inputSchema"]["type"] == "object" for item in definitions)


@pytest.mark.parametrize("name", ["annualized_return", "max_drawdown"])
def test_mcp_calls_return_exactly_the_direct_result(name: str) -> None:
    direct = EXECUTOR.execute(PlanStep(tool=name, arguments=ARGUMENTS)).output
    via_mcp = call_tool(EXECUTOR, name, ARGUMENTS)

    assert via_mcp["isError"] is False
    assert via_mcp["structuredContent"] == direct


def test_mcp_reports_invalid_calls_as_errors() -> None:
    result = call_tool(EXECUTOR, "annualized_return", {"series_id": "s", "returns": []})

    assert result["isError"] is True
    assert "invalid_input" in result["content"][0]["text"]
