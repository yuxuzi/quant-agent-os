"""Run one tool call under the contract: permissions, validation, timeout, and output size."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from quant_agent_os.runtime.approvals import ApprovalLedger
from quant_agent_os.runtime.plan import PlanStep
from quant_agent_os.tools.permissions import (
    CONSEQUENTIAL,
    PermissionDeniedError,
    PermissionPolicy,
)
from quant_agent_os.tools.registry import ToolRegistry, UnknownToolError

CallStatus = Literal[
    "ok", "invalid_input", "denied", "approval_required", "unknown_tool", "timeout", "error"
]


class ToolCallRecord(BaseModel):
    """Provenance for one tool call; recorded whether it succeeded or not.

    ``input_sha256`` hashes the validated input (or the raw arguments when validation failed),
    so identical calls can be recognised across runs. ``approval`` names what allowed a
    consequential call: an approval id, or ``standing:<permission>,...`` for permissions the
    operator pre-approved.
    """

    tool: str
    tool_version: str | None
    arguments: dict[str, Any]
    status: CallStatus
    output: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: float = 0.0
    call_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    input_sha256: str | None = None
    data_versions: dict[str, str] = Field(default_factory=dict)
    approval: str | None = None


class ToolExecutor:
    """Execute plan steps against a registry under a permission policy.

    Timeouts stop waiting for a tool but cannot kill a running Python thread, so tools must
    not perform unbounded work; tools that run programs use `runner.run_command`, which kills
    them. A tool with a consequential permission (decision 0005) runs only with a matching,
    unused approval from ``approvals`` for this run, tool, and input hash, or when the policy
    pre-approves those permissions; otherwise the call records ``approval_required``.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        policy: PermissionPolicy,
        approvals: ApprovalLedger | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.approvals = approvals

    def execute(self, step: PlanStep, *, run_id: str | None = None) -> ToolCallRecord:
        """Run ``step`` and return its record; failures are recorded, never raised."""
        started_at = datetime.now(UTC)
        started = time.perf_counter()
        input_sha256 = canonical_sha256(step.arguments)

        approval: str | None = None

        def record(status: CallStatus, **fields: Any) -> ToolCallRecord:
            return ToolCallRecord(
                tool=step.tool,
                tool_version=fields.pop("tool_version", None),
                arguments=step.arguments,
                status=status,
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
                run_id=run_id,
                started_at=started_at,
                input_sha256=input_sha256,
                approval=approval,
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
        input_sha256 = canonical_sha256(params.model_dump(mode="json"))
        pending = self.policy.needs_approval(spec.permissions)
        if pending:
            granted = (
                self.approvals.consume(run_id, spec.name, input_sha256)
                if self.approvals is not None and run_id is not None
                else None
            )
            if granted is None:
                names = ", ".join(sorted(permission.value for permission in pending))
                return record(
                    "approval_required",
                    tool_version=spec.version,
                    error=(
                        f"Tool '{spec.name}' needs an operator approval for {names} "
                        f"(run {run_id or 'none'}, input sha256 {input_sha256[:16]})."
                    ),
                )
            approval = granted.approval_id
        standing = (spec.permissions & CONSEQUENTIAL) & self.policy.preapproved
        if approval is None and standing:
            approval = "standing:" + ",".join(sorted(permission.value for permission in standing))

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
        versions = payload.get("data_versions")
        data_versions = (
            {str(key): str(value) for key, value in versions.items()}
            if isinstance(versions, dict)
            else {}
        )
        return record("ok", tool_version=spec.version, output=payload, data_versions=data_versions)


def canonical_sha256(value: Any) -> str:
    """Return the SHA-256 of ``value`` as canonical JSON (sorted keys, no whitespace)."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _brief(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or 'value'}: {item['msg']}"
        for item in error.errors()
    )
