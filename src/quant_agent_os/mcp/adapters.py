"""Map registered tools, resources, and prompts onto MCP, without an SDK dependency.

The adapter calls tools through the same `ToolExecutor` as the orchestrator, so MCP clients get
identical validation, permissions, limits, and outputs. A server transport only forwards
``tools/list`` to `tool_definitions`, ``tools/call`` to `call_tool`, the resource requests to
`resource_templates` and `read_resource` (which call the resource's tool), and the prompt
requests to `prompt_definitions` and `get_prompt`.
"""

from __future__ import annotations

import json
from typing import Any

from quant_agent_os.runs.recorder import RunRecorder
from quant_agent_os.runtime.executor import ToolCallRecord, ToolExecutor, failure_details
from quant_agent_os.runtime.plan import PlanStep
from quant_agent_os.tools.catalog import (
    PromptArgumentError,
    PromptSpec,
    ResourceSpec,
    find_resource,
)
from quant_agent_os.tools.contracts import ToolContractError
from quant_agent_os.tools.permissions import Permission

_WRITES = frozenset(
    {Permission.WRITE_LOCAL_DATA, Permission.EXECUTE_CODE, Permission.EXTERNAL_ACTION}
)
_OUTSIDE = frozenset({Permission.NETWORK, Permission.EXTERNAL_ACTION})


class ResourceReadError(RuntimeError):
    """Raised when a resource URI matches nothing served, or its tool call fails."""


# Keywords whose keys are names (of properties or definitions), not schema keywords.
_NAMED_SUBSCHEMAS = frozenset({"properties", "$defs", "definitions", "patternProperties"})


def tool_definitions(executor: ToolExecutor) -> list[dict[str, Any]]:
    """Return MCP ``tools/list`` entries for every tool the policy allows.

    ``outputSchema`` describes ``structuredContent`` on success, so clients can validate and
    rely on typed results. ``annotations`` are MCP behaviour hints derived from the declared
    contract: read-only unless the tool may write, run code, or act externally; open-world when
    it reaches outside the machine (network access or external actions). ``_meta`` carries the
    tool's version and, for a deprecated tool, its replacement and removal date.
    """
    return [
        {
            "name": spec.name,
            "title": spec.title,
            "description": spec.full_description,
            "inputSchema": compact_schema(spec.input_model.model_json_schema()),
            "outputSchema": compact_schema(spec.descriptor()["output_schema"]),
            "annotations": {
                "readOnlyHint": not (spec.permissions & _WRITES),
                "idempotentHint": spec.idempotent,
                "openWorldHint": bool(spec.permissions & _OUTSIDE),
            },
            "_meta": {"qaos/version": spec.version}
            | ({"qaos/deprecated": spec.descriptor()["deprecated"]} if spec.deprecated else {}),
        }
        for spec in executor.registry.specs()
        if executor.policy.allows_tool(spec.name)
    ]


def compact_schema(schema: Any) -> Any:
    """Return ``schema`` without its ``title`` keywords, which pydantic derives from names.

    Titles repeat the property or model name, so they cost every client context on every turn
    and validate nothing. A property that is itself named ``title`` is kept.
    """
    if isinstance(schema, list):
        return [compact_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    return {
        key: (
            {name: compact_schema(value) for name, value in child.items()}
            if key in _NAMED_SUBSCHEMAS and isinstance(child, dict)
            else compact_schema(child)
        )
        for key, child in schema.items()
        if key != "title"
    }


def call_tool(
    executor: ToolExecutor,
    name: str,
    arguments: dict[str, Any],
    *,
    recorder: RunRecorder | None = None,
) -> dict[str, Any]:
    """Return an MCP ``tools/call`` result for one invocation, recorded as a run if asked.

    A failure is an error result whose text leads with the status and whose structured content
    adds a hint on what to do next and whether retrying can help (`failure_details`).
    """
    step = PlanStep(tool=name, arguments=arguments)
    record = recorder.call(executor, step) if recorder else executor.execute(step)
    return call_result(executor, record)


def call_result(executor: ToolExecutor, record: ToolCallRecord) -> dict[str, Any]:
    """Return the MCP ``tools/call`` result for a finished call's ``record``."""
    if record.status == "ok":
        return {
            "content": [{"type": "text", "text": json.dumps(record.output, sort_keys=True)}],
            "structuredContent": record.output,
            "isError": False,
        }
    available = [
        spec.name for spec in executor.registry.specs() if executor.policy.allows_tool(spec.name)
    ]
    details = failure_details(record, available)
    return {
        "content": [
            {"type": "text", "text": f"{record.status}: {record.error}\nHint: {details['hint']}"}
        ],
        "structuredContent": details,
        "isError": True,
    }


def served_resources(executor: ToolExecutor, resources: list[ResourceSpec]) -> list[ResourceSpec]:
    """Return the resources whose tool is registered and allowed, checking each template.

    Raises `ToolContractError` when a template variable is not an argument of its tool.
    """
    served: list[ResourceSpec] = []
    for resource in resources:
        if resource.tool not in executor.registry or not executor.policy.allows_tool(resource.tool):
            continue
        fields = executor.registry.get(resource.tool).input_model.model_fields
        unknown = [name for name in resource.variables if name not in fields]
        if unknown:
            raise ToolContractError(
                f"Resource '{resource.name}' uses {', '.join(unknown)}, which "
                f"'{resource.tool}' does not take."
            )
        served.append(resource)
    return served


def resource_templates(
    executor: ToolExecutor, resources: list[ResourceSpec]
) -> list[dict[str, Any]]:
    """Return MCP ``resources/templates/list`` entries for the served resources with variables."""
    return [
        {
            "uriTemplate": resource.uri_template,
            "name": resource.name,
            "description": resource.description,
            "mimeType": resource.mime_type,
        }
        for resource in served_resources(executor, resources)
        if resource.variables
    ]


def fixed_resources(executor: ToolExecutor, resources: list[ResourceSpec]) -> list[dict[str, Any]]:
    """Return MCP ``resources/list`` entries: the served resources without variables."""
    return [
        {
            "uri": resource.uri_template,
            "name": resource.name,
            "description": resource.description,
            "mimeType": resource.mime_type,
        }
        for resource in served_resources(executor, resources)
        if not resource.variables
    ]


def read_resource(
    executor: ToolExecutor,
    resources: list[ResourceSpec],
    uri: str,
    *,
    recorder: RunRecorder | None = None,
) -> dict[str, Any]:
    """Return the MCP ``resources/read`` contents of ``uri`` by calling its resource's tool.

    Raises `ResourceReadError` with the status, error, and hint when nothing matches or the call
    fails, since a resource read has no error result of its own.
    """
    try:
        resource, arguments = find_resource(served_resources(executor, resources), uri)
    except LookupError as error:
        raise ResourceReadError(str(error)) from None
    step = PlanStep(tool=resource.tool, arguments=dict(arguments))
    record = recorder.call(executor, step) if recorder else executor.execute(step)
    if record.status != "ok":
        details = failure_details(record, [resource.tool])
        raise ResourceReadError(f"{record.status}: {record.error} Hint: {details['hint']}")
    return {
        "uri": uri,
        "mimeType": resource.mime_type,
        "text": json.dumps(record.output, sort_keys=True),
    }


def prompt_definitions(prompts: list[PromptSpec]) -> list[dict[str, Any]]:
    """Return MCP ``prompts/list`` entries."""
    return [
        {
            "name": prompt.name,
            "description": prompt.description,
            "arguments": [
                {"name": a.name, "description": a.description, "required": a.required}
                for a in prompt.arguments
            ],
        }
        for prompt in prompts
    ]


def get_prompt(
    prompts: list[PromptSpec], name: str, arguments: dict[str, str] | None
) -> dict[str, Any]:
    """Return the MCP ``prompts/get`` result: one user message rendered from the prompt.

    Raises `PromptArgumentError` for an unknown prompt or wrong arguments.
    """
    prompt = next((item for item in prompts if item.name == name), None)
    if prompt is None:
        raise PromptArgumentError(f"No prompt named '{name}'.")
    return {
        "description": prompt.description,
        "messages": [
            {"role": "user", "content": {"type": "text", "text": prompt.render(arguments)}}
        ],
    }
