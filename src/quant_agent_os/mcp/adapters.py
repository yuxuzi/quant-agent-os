"""Map registered tools onto MCP tool definitions and results, without an SDK dependency.

The adapter calls tools through the same `ToolExecutor` as the orchestrator, so MCP clients get
identical validation, permissions, limits, and outputs. A server transport (milestone Q5) only
needs to forward ``tools/list`` to `tool_definitions` and ``tools/call`` to `call_tool`.
"""

from __future__ import annotations

import json
from typing import Any

from quant_agent_os.runs.recorder import RunRecorder
from quant_agent_os.runtime.executor import ToolExecutor
from quant_agent_os.runtime.plan import PlanStep
from quant_agent_os.tools.permissions import Permission

_WRITES = frozenset(
    {Permission.WRITE_LOCAL_DATA, Permission.EXECUTE_CODE, Permission.EXTERNAL_ACTION}
)
_OUTSIDE = frozenset({Permission.NETWORK, Permission.EXTERNAL_ACTION})


def tool_definitions(executor: ToolExecutor) -> list[dict[str, Any]]:
    """Return MCP ``tools/list`` entries for every tool the policy allows.

    ``annotations`` are MCP behaviour hints derived from the declared contract: read-only unless
    the tool may write, run code, or act externally; open-world when it reaches outside the
    machine (network access or external actions).
    """
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "inputSchema": spec.input_model.model_json_schema(),
            "annotations": {
                "readOnlyHint": not (spec.permissions & _WRITES),
                "idempotentHint": spec.idempotent,
                "openWorldHint": bool(spec.permissions & _OUTSIDE),
            },
        }
        for spec in executor.registry.specs()
        if executor.policy.allows_tool(spec.name)
    ]


def call_tool(
    executor: ToolExecutor,
    name: str,
    arguments: dict[str, Any],
    *,
    recorder: RunRecorder | None = None,
) -> dict[str, Any]:
    """Return an MCP ``tools/call`` result for one invocation, recorded as a run if asked."""
    step = PlanStep(tool=name, arguments=arguments)
    record = recorder.call(executor, step) if recorder else executor.execute(step)
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
