"""Contract checks that tool packages run in their own tests (`check_tool`, `check_app`).

A tool that passes behaves the same through the executor and through MCP, declares a complete,
serialisable contract, returns output that matches its published output schema, and gives the
same output twice when it says it is deterministic. `check_app` checks a firm's whole app, and
can keep a schema lock so that a tool's schemas do not change without a version bump.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from quant_agent_os.data.datasets import DatasetStore
from quant_agent_os.mcp.adapters import call_tool
from quant_agent_os.registration import FirmApp
from quant_agent_os.runtime.call import ToolCall
from quant_agent_os.runtime.executor import CallStatus, ToolCallRecord, ToolExecutor
from quant_agent_os.tools.compatibility import check_lock, lock_entry
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
    record = executor.execute(ToolCall(tool=spec.name, arguments=arguments))
    if record.status != expect:
        problems.append(f"status is {record.status!r}, expected {expect!r}: {record.error}")
    via_mcp = call_tool(executor, spec.name, arguments)
    if via_mcp["isError"] != (record.status != "ok"):
        problems.append("MCP and direct calls disagree on success")
    if record.status == "ok":
        if via_mcp.get("structuredContent") != record.output:
            problems.append("MCP output differs from the direct output")
        problem = _schema_problem(record.output, descriptor["output_schema"])
        if problem:
            problems.append(f"output does not match the published output schema: {problem}")
        if spec.deterministic:
            again = executor.execute(ToolCall(tool=spec.name, arguments=arguments))
            if again.output != record.output:
                problems.append("declared deterministic, but two calls returned different output")
    if problems:
        raise ToolContractError(f"Tool '{spec.name}' breaks the contract: " + "; ".join(problems))
    return record


def check_app(
    app: FirmApp,
    examples: Mapping[str, dict[str, Any]],
    *,
    datasets: DatasetStore | None = None,
    lock: Path | None = None,
    update_lock: bool = False,
) -> list[ToolCallRecord]:
    """Check every tool in ``app`` with its example call, for a firm's own CI.

    ``examples`` maps each tool's name to the arguments of one call that should succeed over
    ``datasets``. With ``lock``, the tools' versions, permissions, and schemas are compared with
    that file: a schema change needs a version bump at least as big as the change (decision
    0015). ``update_lock`` writes the file, but only when no bump is too small. Raises
    `ToolContractError` listing every problem found.
    """
    tools = app.tools(datasets)
    problems = [
        f"{spec.name} has no example call; add one to examples."
        for spec in tools
        if spec.name not in examples
    ]
    problems += [
        f"{name} is not a tool of app '{app.name}'."
        for name in examples
        if name not in {spec.name for spec in tools}
    ]
    records: list[ToolCallRecord] = []
    for spec in tools:
        if spec.name in examples:
            try:
                records.append(check_tool(spec, examples[spec.name]))
            except ToolContractError as error:
                problems.append(str(error))
    if lock is not None:
        problems += _lock_problems(tools, lock, update_lock)
    if problems:
        raise ToolContractError(f"App '{app.name}' breaks the contract: " + "; ".join(problems))
    return records


def _lock_problems(tools: list[ToolSpec[Any, Any]], lock: Path, update: bool) -> list[str]:
    locked: dict[str, dict[str, Any]] = (
        json.loads(lock.read_text("utf-8"))["tools"] if lock.exists() else {}
    )
    problems = check_lock(locked, tools)
    if not update:
        if not lock.exists():
            return [f"there is no lock at {lock}; create it with update_lock=True."]
        return [f"{problem} (lock: {lock})" for problem in problems]
    blocking = [problem for problem in problems if "version bump" in problem]
    if not blocking:
        document = {
            "comment": "Written by check_app(update_lock=True); do not edit.",
            "tools": {spec.name: lock_entry(spec) for spec in tools},
        }
        lock.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", "utf-8")
    return blocking


def _schema_problem(output: Any, schema: dict[str, Any]) -> str | None:
    """Return why ``output`` fails ``schema``, as an MCP client would check it, or None.

    Uses ``jsonschema`` when it is installed (it comes with the ``mcp`` extra) and skips the
    check otherwise.
    """
    try:
        import jsonschema  # type: ignore[import-untyped]  # no stubs; comes with the mcp extra
    except ImportError:
        return None
    try:
        jsonschema.validate(output, schema)
    except jsonschema.ValidationError as error:
        return str(error.message)
    return None
