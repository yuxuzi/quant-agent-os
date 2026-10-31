"""Contract checks that tool packages run in their own tests (`check_tool`).

A tool that passes behaves the same through the executor and through MCP, declares a complete,
serialisable contract, and gives the same output twice when it says it is deterministic.
"""

from __future__ import annotations

import json
from typing import Any

from quant_agent_os.mcp.adapters import call_tool
from quant_agent_os.runtime.executor import CallStatus, ToolCallRecord, ToolExecutor
from quant_agent_os.runtime.plan import PlanStep
from quant_agent_os.tools.contracts import ToolContractError, ToolSpec
from quant_agent_os.tools.permissions import READ_ONLY, PermissionPolicy
from quant_agent_os.tools.registry import ToolRegistry


def check_tool(
    spec: ToolSpec[Any, Any], arguments: dict[str, Any], *, expect: CallStatus = "ok"
) -> ToolCallRecord:
    """Check ``spec`` against the platform contract with ``arguments`` and return the record.

    The call runs with exactly the permissions the tool declares, granted and pre-approved, so
    the check exercises the tool, not the approval gate. The tool is called up to three times
    (direct, MCP, and a determinism repeat), so give a tool with side effects a scratch target.
    Raises `ToolContractError` listing every problem found.
    """
    problems: list[str] = []
    descriptor = spec.descriptor()
    try:
        json.dumps(descriptor)
    except (TypeError, ValueError) as error:
        problems.append(f"descriptor is not JSON-serialisable: {error}")
    if not spec.description.strip():
        problems.append("description is empty")
    for schema in ("input_schema", "output_schema"):
        if descriptor[schema].get("type") != "object":
            problems.append(f"{schema} is not a JSON object schema")
    policy = PermissionPolicy(granted=READ_ONLY | spec.permissions, preapproved=spec.permissions)
    executor = ToolExecutor(ToolRegistry([spec]), policy)
    record = executor.execute(PlanStep(tool=spec.name, arguments=arguments))
    if record.status != expect:
        problems.append(f"status is {record.status!r}, expected {expect!r}: {record.error}")
    via_mcp = call_tool(executor, spec.name, arguments)
    if via_mcp["isError"] != (record.status != "ok"):
        problems.append("MCP and direct calls disagree on success")
    if record.status == "ok":
        if via_mcp.get("structuredContent") != record.output:
            problems.append("MCP output differs from the direct output")
        if spec.deterministic:
            again = executor.execute(PlanStep(tool=spec.name, arguments=arguments))
            if again.output != record.output:
                problems.append("declared deterministic, but two calls returned different output")
    if problems:
        raise ToolContractError(f"Tool '{spec.name}' breaks the contract: " + "; ".join(problems))
    return record
