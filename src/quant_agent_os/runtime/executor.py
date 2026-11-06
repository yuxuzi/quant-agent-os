"""Run one tool call under the contract: permissions, validation, timeout, and output size."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextvars import copy_context
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from quant_agent_os.runtime.approvals import ApprovalLedger
from quant_agent_os.runtime.call import ToolCall
from quant_agent_os.runtime.quotas import UsageGate
from quant_agent_os.runtime.telemetry import call_span
from quant_agent_os.tenancy import DEFAULT_TENANT
from quant_agent_os.tools.access import AccessPolicy, collecting_refusals, note_refusal, permits
from quant_agent_os.tools.permissions import (
    CONSEQUENTIAL,
    PermissionDeniedError,
    PermissionPolicy,
)
from quant_agent_os.tools.registry import ToolRegistry, UnknownToolError

CallStatus = Literal[
    "ok",
    "invalid_input",
    "denied",
    "approval_required",
    "quota_exceeded",
    "busy",
    "unknown_tool",
    "timeout",
    "error",
]

# Refused before the tool started or an approval was used, so an approval stays for a retry.
NOT_STARTED: frozenset[CallStatus] = frozenset({"quota_exceeded", "busy"})
# Calls one executor runs at once. A timed-out tool keeps its slot until its thread returns,
# because a Python thread cannot be killed; `busy` is the bound on how many can pile up.
MAX_CONCURRENT_CALLS = 16
BUSY_RETRY_SECONDS = 5

# What a caller (usually a model) should do after each failure, and whether retrying can help.
FAILURE_HINTS: dict[CallStatus, tuple[str, bool]] = {
    "invalid_input": (
        "Fix the arguments named in the error to match the tool's input schema, then call again.",
        True,
    ),
    "unknown_tool": ("Call one of the available tools: {tools}.", True),
    "denied": (
        "The server's operator has not allowed this call. Do not retry; tell the user what the "
        "error says.",
        False,
    ),
    "approval_required": (
        "This call waits for an operator's approval. Do not retry or try to approve it; tell "
        "the user it needs their approval.",
        False,
    ),
    "quota_exceeded": (
        "The tenant reached a limit of its plan. Wait retry_after_seconds before calling again, "
        "and tell the user which limit was reached.",
        True,
    ),
    "busy": (
        "The server is running as many tool calls as it allows. Wait retry_after_seconds, then "
        "call again.",
        True,
    ),
    "timeout": (
        "The tool ran out of time. Retry once with a narrower request, such as fewer results.",
        True,
    ),
    "error": (
        "The tool failed. The same arguments will fail the same way; change them or report the "
        "error to the user.",
        False,
    ),
}


class ToolCallRecord(BaseModel):
    """Provenance for one tool call; recorded whether it succeeded or not.

    ``input_sha256`` hashes the validated input (or the raw arguments when validation failed),
    so identical calls can be recognised across runs. ``approval`` names what allowed a
    consequential call: an approval id, or ``standing:<permission>,...`` for permissions the
    operator pre-approved. ``error_detail`` keeps the exception text of an internal failure for
    the operator; interfaces never return it to a caller (security review F-5). ``refused`` lists
    what an access policy refused during the call (decision 0021), for the audit trail only: it
    is never returned to a caller, because a refused dataset must look like a missing one.
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
    retry_after_seconds: int | None = None
    error_detail: str | None = None
    refused: list[str] = Field(default_factory=list)

    def result(self) -> dict[str, Any]:
        """Return the tool's output, or raise `ToolCallError` when the call did not succeed."""
        if self.status != "ok":
            raise ToolCallError(self)
        return self.output or {}


class ToolCallError(RuntimeError):
    """A failed call, raised by `ToolCallRecord.result` for code that wants an exception.

    Carries the ``status``, ``error``, and what to do next (``hint``, ``retryable``), and the whole
    ``record``. The executor and interfaces never raise this: they report the record.
    """

    def __init__(self, record: ToolCallRecord) -> None:
        hint, retryable = FAILURE_HINTS[record.status]
        self.record = record
        self.status: CallStatus = record.status
        self.error = record.error
        self.hint = hint.format(tools="see the error")
        self.retryable = retryable
        super().__init__(f"{record.tool} returned {record.status}: {record.error} {self.hint}")


# Errors a caller can act on: tools raise these for bad requests, with a message meant for the
# caller. KeyError and IndexError are lookups too, but they almost always mean a bug.
CALLER_ERRORS: tuple[type[Exception], ...] = (ValueError, LookupError)
INTERNAL_ERRORS: tuple[type[Exception], ...] = (KeyError, IndexError)


class ToolExecutor:
    """Execute plan steps against a registry under a permission policy.

    Timeouts stop waiting for a tool but cannot kill a running Python thread, so tools must
    not perform unbounded work; tools that run programs use `runner.run_command`, which kills
    them. A tool that raises a `ValueError` or `LookupError` returns its message to the caller;
    any other exception returns a generic message with the call id, and its text is kept in
    ``error_detail``. A tool with a consequential permission (decision 0005) runs only with a
    matching, unused approval from ``approvals`` for this run, tool, and input hash, or when the
    policy pre-approves those permissions; otherwise the call records ``approval_required``.

    With ``usage`` (a tenant's plan limits, decision 0015), a call whose permissions and input
    are valid must be admitted before any approval is used or the tool starts; a refusal
    records ``quota_exceeded``. ``max_output_bytes`` caps every tool's own output limit.

    With ``access`` (decision 0021), a call needs the current caller to be allowed the tool, and
    no bound caller is a refusal. The caller is carried onto the thread the tool runs on.

    Every call is traced and counted through OpenTelemetry (`telemetry.call_span`) under
    ``tenant``, the tenant this executor serves.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        policy: PermissionPolicy,
        approvals: ApprovalLedger | None = None,
        *,
        usage: UsageGate | None = None,
        max_output_bytes: int | None = None,
        tenant: str = DEFAULT_TENANT,
        max_concurrent_calls: int = MAX_CONCURRENT_CALLS,
        access: AccessPolicy | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.approvals = approvals
        self.usage = usage
        self.max_output_bytes = max_output_bytes
        self.tenant = tenant
        self.access = access
        self._pool = ThreadPoolExecutor(max_concurrent_calls, thread_name_prefix="tool")
        self._slots = threading.BoundedSemaphore(max_concurrent_calls)

    def serves(self, tool_name: str) -> bool:
        """Return whether the policy allows ``tool_name``, and the access policy, this caller."""
        return self.policy.allows_tool(tool_name) and (
            self.access is None or permits(self.access, "tool.call", tool_name)
        )

    def execute(self, step: ToolCall, *, run_id: str | None = None) -> ToolCallRecord:
        """Run ``step`` and return its record; failures are recorded, never raised."""
        # An unregistered name comes from the caller, so it never becomes a span or metric label.
        traced = step.tool if step.tool in self.registry else "unknown"
        with call_span(traced, self.tenant) as finish, collecting_refusals() as refused:
            record = self._execute(step, run_id)
            finish(record.status, record.tool_version, record.duration_ms)
        return record.model_copy(update={"refused": list(refused)}) if refused else record

    def _execute(self, step: ToolCall, run_id: str | None) -> ToolCallRecord:
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
        if self.access is not None and not permits(self.access, "tool.call", spec.name):
            note_refusal("tool.call", spec.name)
            return record(
                "denied",
                tool_version=spec.version,
                error=(
                    f"Your access does not include tool '{spec.name}'; ask the server's operator."
                ),
            )
        try:
            params = spec.input_model.model_validate(step.arguments)
        except ValidationError as error:
            return record("invalid_input", tool_version=spec.version, error=_brief(error))
        input_sha256 = canonical_sha256(params.model_dump(mode="json"))
        # Taken before the quota and the approval, so that a busy refusal spends neither.
        if not self._slots.acquire(blocking=False):
            return record(
                "busy",
                tool_version=spec.version,
                error="The server is running as many tool calls as it allows.",
                retry_after_seconds=BUSY_RETRY_SECONDS,
            )
        admitted = False
        try:
            refusal = self.usage.admit(spec.name) if self.usage is not None else None
            if refusal is not None:
                return record(
                    "quota_exceeded",
                    tool_version=spec.version,
                    error=refusal.message,
                    retry_after_seconds=refusal.retry_after_seconds,
                )
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
                approval = "standing:" + ",".join(
                    sorted(permission.value for permission in standing)
                )
            admitted = True
        finally:
            if not admitted:
                self._slots.release()

        future: Future[Any] | None = None
        try:
            future = self._pool.submit(copy_context().run, spec.function, params)
            future.add_done_callback(lambda _: self._slots.release())
            result = future.result(timeout=spec.timeout_seconds)
        except FutureTimeoutError:
            return record(
                "timeout",
                tool_version=spec.version,
                error=f"Tool '{spec.name}' exceeded {spec.timeout_seconds}s.",
            )
        except Exception as error:
            detail = f"{type(error).__name__}: {error}"
            if isinstance(error, CALLER_ERRORS) and not isinstance(error, INTERNAL_ERRORS):
                return record("error", tool_version=spec.version, error=detail)
            failed = record("error", tool_version=spec.version, error_detail=detail)
            return failed.model_copy(
                update={
                    "error": (
                        f"Tool '{spec.name}' failed with an internal error (call "
                        f"{failed.call_id}). The operator can find the details in the run record."
                    )
                }
            )
        finally:
            if future is None:
                self._slots.release()

        try:
            output = spec.output_model.model_validate(result)
        except ValidationError as error:
            return record("error", tool_version=spec.version, error=_brief(error))
        payload = output.model_dump(mode="json")
        size = len(output.model_dump_json().encode("utf-8"))
        limit = min(spec.max_output_bytes, self.max_output_bytes or spec.max_output_bytes)
        if size > limit:
            return record(
                "error",
                tool_version=spec.version,
                error=f"Output of {size} bytes exceeds the {limit}-byte limit.",
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


def failure_details(record: ToolCallRecord, tools: list[str]) -> dict[str, Any]:
    """Return a failed call as ``status``, ``error``, ``hint``, and ``retryable``.

    The hint tells the caller what to do next; ``tools`` names the available tools for an
    unknown-tool hint. Every interface (MCP, REST) reports failures this way.
    """
    if record.status == "ok":
        raise ValueError("failure_details needs a failed call record")
    hint, retryable = FAILURE_HINTS[record.status]
    details: dict[str, Any] = {
        "status": record.status,
        "error": record.error,
        "hint": hint.format(tools=", ".join(tools) or "none"),
        "retryable": retryable,
    }
    if record.retry_after_seconds is not None:
        details["retry_after_seconds"] = record.retry_after_seconds
    return details
