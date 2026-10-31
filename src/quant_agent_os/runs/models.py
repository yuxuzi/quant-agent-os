"""What a persisted run looks like: the run, its tool calls, and its audit trail."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from quant_agent_os.runtime.executor import ToolCallRecord

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
    tenant: str = "local"
    platform_version: str
    provider: str | None = None
    registry_digest: str
    started_at: datetime
    finished_at: datetime | None = None
    data_versions: dict[str, str] = Field(default_factory=dict)
    error: str | None = None


class AuditEvent(BaseModel):
    """An append-only audit entry. ``details`` holds ids, hashes, and statuses, never inputs."""

    run_id: str
    actor: str
    event: str
    subject: str
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    details: dict[str, Any] = Field(default_factory=dict)


class StoredRun(BaseModel):
    """A run with its tool calls and audit events, as read back from a `RunStore`."""

    run: RunRecord
    calls: list[ToolCallRecord] = Field(default_factory=list)
    events: list[AuditEvent] = Field(default_factory=list)
