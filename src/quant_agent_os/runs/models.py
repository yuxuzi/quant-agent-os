"""What a persisted run looks like: the run, its tool calls, and its audit trail."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from quant_agent_os.runtime.executor import ToolCallRecord
from quant_agent_os.tenancy import DEFAULT_TENANT

RunKind = Literal["call", "plan"]
# `planned` and `awaiting_approval` are reserved for the approval gate (decision 0005).
RunStatus = Literal["planned", "awaiting_approval", "running", "completed", "failed"]


class RunRecord(BaseModel):
    """One run: a single tool call (MCP or SDK) or an orchestrated plan.

    ``data_versions`` merges the versions every call reported, for example the corpus
    snapshot id, so the run names the exact data it read.
    """

    run_id: str
    kind: RunKind
    status: RunStatus
    goal: str = Field(description="The request for a plan run, or the tool name for a call run")
    actor: str = Field(description="Who started the run, e.g. 'mcp' or 'sdk'")
    principal: str | None = Field(
        default=None, description="The authenticated caller, e.g. an API key's principal"
    )
    tenant: str = DEFAULT_TENANT
    platform_version: str
    provider: str | None = None
    registry_digest: str
    started_at: datetime
    finished_at: datetime | None = None
    data_versions: dict[str, str] = Field(default_factory=dict)
    error: str | None = None


class AuditEvent(BaseModel):
    """An append-only audit entry. ``details`` holds ids, hashes, and statuses, never inputs.

    ``actor`` is the interface (``mcp``, ``sdk``, ``rest``); ``principal`` is who authenticated
    through it, when the interface authenticates callers.
    """

    run_id: str
    actor: str
    principal: str | None = None
    event: str
    subject: str
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    details: dict[str, Any] = Field(default_factory=dict)


class StoredRun(BaseModel):
    """A run with its tool calls and audit events, as read back from a `RunStore`."""

    run: RunRecord
    calls: list[ToolCallRecord] = Field(default_factory=list)
    events: list[AuditEvent] = Field(default_factory=list)


RequestStatus = Literal["pending", "approved", "denied", "used"]


class ApprovalRequest(BaseModel):
    """A waiting call a person may approve once, before ``expires_at`` (decision 0014).

    Bound to the tenant, the run, the tool, the validated input's hash, and the requester;
    ``arguments`` are redacted and only for display. The id has 128 random bits.
    """

    request_id: str = Field(default_factory=lambda: secrets.token_urlsafe(16))
    tenant: str = DEFAULT_TENANT
    run_id: str
    tool: str
    input_sha256: str
    requested_by: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    permissions: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    status: RequestStatus = "pending"
    decided_by: str | None = None
    decided_at: datetime | None = None

    def expired(self, now: datetime | None = None) -> bool:
        """Return whether the request can no longer be decided or used."""
        return (now or datetime.now(UTC)) >= self.expires_at
