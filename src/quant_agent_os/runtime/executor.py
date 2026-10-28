"""Run one tool call under the contract: permissions, validation, timeout, and output size."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from quant_agent_os.runtime.plan import PlanStep
from quant_agent_os.tools.permissions import PermissionDeniedError, PermissionPolicy
from quant_agent_os.tools.registry import ToolRegistry, UnknownToolError

CallStatus = Literal["ok", "invalid_input", "denied", "unknown_tool", "timeout", "error"]


class ToolCallRecord(BaseModel):
    """Provenance for one tool call; recorded whether it succeeded or not."""

    tool: str
    tool_version: str | None
    arguments: dict[str, Any]
    status: CallStatus
    output: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: float = 0.0


class ToolExecutor:
    """Execute plan steps against a registry under a permission policy.

    Timeouts stop waiting for a tool but cannot kill a running Python thread, so tools must
    not perform unbounded or side-effecting work.
    """

    def __init__(self, registry: ToolRegistry, policy: PermissionPolicy) -> None:
        self.registry = registry
        self.policy = policy

    def execute(self, step: PlanStep) -> ToolCallRecord:
        """Run ``step`` and return its record; failures are recorded, never raised."""
        started = time.perf_counter()

        def record(status: CallStatus, **fields: Any) -> ToolCallRecord:
            return ToolCallRecord(
                tool=step.tool,
                tool_version=fields.pop("tool_version", None),
                arguments=step.arguments,
                status=status,
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
                **fields,
            )

        try:
            spec = self.registry.get(step.tool)
        except UnknownToolError as error:
            return record("unknown_tool", error=str(error))
        try:
            self.policy.check(spec.name, spec.permissions)
        except PermissionDeniedError as error:
            return record("denied", tool_version=spec.version, error=str(error))
        try:
            params = spec.input_model.model_validate(step.arguments)
        except ValidationError as error:
            return record("invalid_input", tool_version=spec.version, error=_brief(error))

        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"tool-{spec.name}")
        try:
            result = pool.submit(spec.function, params).result(timeout=spec.timeout_seconds)
        except FutureTimeoutError:
            return record(
                "timeout",
                tool_version=spec.version,
                error=f"Tool '{spec.name}' exceeded {spec.timeout_seconds}s.",
            )
        except Exception as error:
            return record(
                "error", tool_version=spec.version, error=f"{type(error).__name__}: {error}"
            )
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        try:
            output = spec.output_model.model_validate(result)
        except ValidationError as error:
            return record("error", tool_version=spec.version, error=_brief(error))
        payload = output.model_dump(mode="json")
        size = len(output.model_dump_json().encode("utf-8"))
        if size > spec.max_output_bytes:
            return record(
                "error",
                tool_version=spec.version,
                error=f"Output of {size} bytes exceeds the {spec.max_output_bytes}-byte limit.",
            )
        return record("ok", tool_version=spec.version, output=payload)


def _brief(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or 'value'}: {item['msg']}"
        for item in error.errors()
    )
