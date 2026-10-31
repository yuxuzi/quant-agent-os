"""Operator approvals for consequential tool calls (decision 0005).

An `Approval` is bound to one run, one tool, and the SHA-256 of one validated input, and an
`ApprovalLedger` lets each approval be consumed once. Approvals are created only by the
operator (the SDK's `Platform.approve` or a CLI); no tool can create one.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, Field


class Approval(BaseModel):
    """Permission to run ``tool`` once, in ``run_id``, on the input hashing to ``input_sha256``."""

    approval_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str
    tool: str
    input_sha256: str
    approved_by: str
    approved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    used_at: datetime | None = None


class ApprovalLedger(Protocol):
    """Holds granted approvals and consumes each one at most once."""

    def grant(self, approval: Approval) -> None:
        """Store a new, unused approval."""
        ...

    def consume(self, run_id: str, tool: str, input_sha256: str) -> Approval | None:
        """Mark one matching unused approval as used and return it, or return None."""
        ...


class MemoryApprovalLedger:
    """An `ApprovalLedger` in memory."""

    def __init__(self) -> None:
        self._approvals: list[Approval] = []
        self._lock = threading.Lock()

    def grant(self, approval: Approval) -> None:
        """Store a new, unused approval."""
        with self._lock:
            self._approvals.append(approval.model_copy(update={"used_at": None}))

    def consume(self, run_id: str, tool: str, input_sha256: str) -> Approval | None:
        """Mark one matching unused approval as used and return it."""
        with self._lock:
            for index, approval in enumerate(self._approvals):
                if approval.used_at is None and (
                    approval.run_id,
                    approval.tool,
                    approval.input_sha256,
                ) == (run_id, tool, input_sha256):
                    used = approval.model_copy(update={"used_at": datetime.now(UTC)})
                    self._approvals[index] = used
                    return used
        return None
