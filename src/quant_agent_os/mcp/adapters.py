"""Map registered tools onto MCP tool definitions and results, without an SDK dependency.

The adapter calls tools through the same `ToolExecutor` as the orchestrator, so MCP clients get
identical validation, permissions, limits, and outputs. A server transport (milestone Q5) only
needs to forward ``tools/list`` to `tool_definitions` and ``tools/call`` to `call_tool`.
"""

from __future__ import annotations

import json
from typing import Any

from quant_agent_os.runtime.executor import ToolExecutor
from quant_agent_os.runtime.plan import PlanStep


def tool_definitions(executor: ToolExecutor) -> list[dict[str, Any]]:
    """Return MCP ``tools/list`` entries for every tool the policy allows."""
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "inputSchema": spec.input_model.model_json_schema(),
        }
        for spec in executor.registry.specs()
        if executor.policy.allows_tool(spec.name)
    ]


def call_tool(executor: ToolExecutor, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return an MCP ``tools/call`` result for one invocation."""
    record = executor.execute(PlanStep(tool=name, arguments=arguments))
    if record.status == "ok":
        return {
            "content": [{"type": "text", "text": json.dumps(record.output, sort_keys=True)}],
            "structuredContent": record.output,
            "isError": False,
        }
    return {
        "content": [{"type": "text", "text": f"{record.status}: {record.error}"}],
        "isError": True,
    }
