"""Unit tests for the tool contract, registry, and permission policy."""

import pytest
from pydantic import BaseModel

from quant_agent_os.tools import (
    DuplicateToolError,
    Permission,
    PermissionDeniedError,
    PermissionPolicy,
    ToolContractError,
    ToolRegistry,
    UnknownToolError,
    tool,
)


class Numbers(BaseModel):
    values: list[float]


class Total(BaseModel):
    total: float


@tool(name="sum_values", description="Add numbers.")
def sum_values(params: Numbers) -> Total:
    return Total(total=sum(params.values))


def test_decorator_infers_models_and_keeps_the_function_callable() -> None:
    assert sum_values.input_model is Numbers
    assert sum_values.output_model is Total
    assert sum_values.function(Numbers(values=[1, 2])) == Total(total=3)


def test_descriptor_exposes_schemas_permissions_and_limits() -> None:
    descriptor = sum_values.descriptor()

    assert descriptor["name"] == "sum_values"
    assert descriptor["input_schema"]["properties"]["values"]["type"] == "array"
    assert descriptor["output_schema"]["required"] == ["total"]
    assert descriptor["permissions"] == []
    assert descriptor["timeout_seconds"] > 0


@pytest.mark.parametrize("name", ["Sum", "1sum", "sum-values", "x" * 65])
def test_invalid_tool_names_are_rejected(name: str) -> None:
    with pytest.raises(ToolContractError, match="snake_case"):
        tool(name=name, description="d")(sum_values.function)


def test_untyped_tools_are_rejected() -> None:
    def untyped(params):  # type: ignore[no-untyped-def]
        return params

    with pytest.raises(ToolContractError):
        tool(name="untyped", description="d")(untyped)


def test_registry_rejects_duplicates_and_reports_unknown_tools() -> None:
    registry = ToolRegistry([sum_values])

    with pytest.raises(DuplicateToolError):
        registry.register(sum_values)
    with pytest.raises(UnknownToolError, match="Available: sum_values"):
        registry.get("missing")
    assert "sum_values" in registry
    assert [d["name"] for d in registry.descriptors()] == ["sum_values"]


def test_policy_is_read_only_by_default() -> None:
    policy = PermissionPolicy()

    policy.check("reader", [Permission.READ_LOCAL_DATA])
    with pytest.raises(PermissionDeniedError, match="network"):
        policy.check("fetcher", [Permission.NETWORK])


def test_policy_allowlist_blocks_unlisted_tools() -> None:
    policy = PermissionPolicy(tool_allowlist=frozenset({"allowed"}))

    assert policy.allows_tool("allowed")
    with pytest.raises(PermissionDeniedError, match="allowlist"):
        policy.check("other", [])


def test_registry_digest_changes_with_tool_contracts() -> None:
    @tool(name="sum_values", description="Sum numbers.", version="2")
    def sum_values_v2(params: Numbers) -> Total:
        return Total(total=sum(params.values))

    assert ToolRegistry([sum_values]).digest() == ToolRegistry([sum_values]).digest()
    assert ToolRegistry([sum_values]).digest() != ToolRegistry([sum_values_v2]).digest()
    assert sum_values.descriptor()["deterministic"] is True
