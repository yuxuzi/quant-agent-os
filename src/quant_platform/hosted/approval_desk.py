"""Approval requests for remote MCP clients (decision 0014): open, decide, resume.

When a call over HTTP returns ``approval_required``, the desk opens an approval request bound
to the tenant, the run, the tool, the validated input's hash, and the requester. A person
decides it on the approval page with an approver key (scope ``runs:approve``, which can never
come with ``tools:call``). When the client retries the identical call, the desk resumes the
original run, so the approval's run, tool, and hash match (decision 0005), and the call runs
once. Neither a model nor the agent's own key can approve.

Five failed submissions for one request deny it (decision 0015). The count is kept in this
process, so with several replicas a request can take up to five failures on each.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import NoReturn

from pydantic import ValidationError

from quant_agent_os.api.keys import KeyRing
from quant_agent_os.mcp.http import Caller
from quant_agent_os.mcp.server import ApprovalGate
from quant_agent_os.runs.models import ApprovalRequest, AuditEvent
from quant_agent_os.runs.recorder import NotAwaitingApprovalError, RunRecorder
from quant_agent_os.runs.redaction import redact
from quant_agent_os.runs.store import RunStore
from quant_agent_os.runtime.call import ToolCall
from quant_agent_os.runtime.executor import (
    NOT_STARTED,
    ToolCallRecord,
    ToolExecutor,
    canonical_sha256,
)
from quant_agent_os.tenancy import DEFAULT_TENANT
from quant_agent_os.tools.registry import UnknownToolError

REQUEST_LIFETIME = timedelta(minutes=15)
APPROVE_SCOPE = "runs:approve"
MAX_FAILED_ATTEMPTS = 5


class DecisionError(PermissionError):
    """Raised when an approval request cannot be decided; the message is safe to show."""


def four_eyes_except_local(tenant: str) -> bool:
    """Require a different approver for every tenant but the local one (decision 0014 Q-12)."""
    return tenant != DEFAULT_TENANT


class ApprovalDesk:
    """Opens approval requests for waiting calls, decides them, and resumes approved calls."""

    def __init__(
        self,
        run_store: RunStore,
        keys: KeyRing,
        *,
        base_url: str,
        lifetime: timedelta = REQUEST_LIFETIME,
        separate_approver: Callable[[str], bool] = four_eyes_except_local,
        max_failed_attempts: int = MAX_FAILED_ATTEMPTS,
    ) -> None:
        self.run_store = run_store
        self.keys = keys
        self.base_url = base_url.rstrip("/")
        self.lifetime = lifetime
        self.separate_approver = separate_approver
        self.max_failed_attempts = max_failed_attempts
        self._secret = secrets.token_bytes(32)  # form tokens; a restart invalidates open forms
        self._failures: dict[str, int] = {}
        self._lock = threading.Lock()

    def call(
        self,
        executor: ToolExecutor,
        step: ToolCall,
        *,
        tenant: str,
        principal: str | None,
        recorder: RunRecorder,
    ) -> tuple[ToolCallRecord, ApprovalRequest | None]:
        """Run ``step``; resume it if approved, or open a request if it needs an approval."""
        digest = _input_hash(executor, step)
        if digest is not None:
            found = self.run_store.find_request(tenant, principal, step.tool, digest)
            if found is not None and not found.expired():
                used = self.run_store.move_request(found.request_id, "used", source="approved")
                if used is not None:
                    try:
                        records = recorder.resume(executor, found.run_id, [step])
                    except NotAwaitingApprovalError:
                        records = []
                    if records and records[-1].status in NOT_STARTED:
                        # Refused before it started: the approval stays for the next retry.
                        self.run_store.move_request(found.request_id, "approved", source="used")
                    if records:
                        return records[-1], None
        record = recorder.call(executor, step)
        if record.status != "approval_required" or record.run_id is None:
            return record, None
        spec = executor.registry.get(step.tool)
        request = ApprovalRequest(
            tenant=tenant,
            run_id=record.run_id,
            tool=step.tool,
            input_sha256=record.input_sha256 or "",
            requested_by=principal,
            arguments=redact(step.arguments),
            permissions=sorted(permission.value for permission in spec.permissions),
            expires_at=datetime.now(UTC) + self.lifetime,
        )
        self.run_store.save_request(request)
        self._event(request, "approval.requested", principal, {"request_id": request.request_id})
        return record, request

    def url(self, request: ApprovalRequest) -> str:
        """Return the approval page's address for ``request``."""
        return f"{self.base_url}/approve/{request.request_id}"

    def view(self, request_id: str) -> ApprovalRequest | None:
        """Return a request for the approval page to show, or None if it is unknown."""
        return self.run_store.get_request(request_id)

    def form_token(self, request_id: str) -> str:
        """Return the token the approval form must send back (CSRF protection)."""
        return hmac.new(self._secret, request_id.encode(), hashlib.sha256).hexdigest()

    def decide(
        self, request_id: str, presented_key: str, *, approve: bool, form_token: str
    ) -> ApprovalRequest:
        """Approve or deny a pending request with an approver key; raise `DecisionError`.

        A wrong form token, a key that cannot decide the request, or a requester deciding their
        own request is a failed attempt; the last allowed one denies the request.
        """
        request = self.run_store.get_request(request_id)
        if request is None:
            raise DecisionError("There is no such approval request.")
        if request.status != "pending":
            raise DecisionError(f"This request is already {request.status}.")
        if request.expired():
            raise DecisionError("This request has expired; ask for the call again.")
        if not hmac.compare_digest(form_token, self.form_token(request_id)):
            self._failed(request, "This form has expired; reload the page and try again.")
        key = self.keys.authenticate(presented_key)
        if key is None or APPROVE_SCOPE not in key.scopes or key.tenant != request.tenant:
            self._failed(request, "This key cannot approve this request.")
        assert key is not None
        if self.separate_approver(request.tenant) and key.principal == request.requested_by:
            self._failed(request, "You cannot decide your own request; ask another approver.")
        recorder = RunRecorder(
            self.run_store, actor="approval-page", principal=key.principal, tenant=request.tenant
        )
        if approve:
            try:
                recorder.approve(request.run_id, approved_by=key.principal)
            except NotAwaitingApprovalError as error:
                raise DecisionError("This call is no longer waiting for an approval.") from error
        decided = self.run_store.move_request(
            request_id,
            "approved" if approve else "denied",
            source="pending",
            decided_by=key.principal,
        )
        if decided is None:
            raise DecisionError("This request was decided by someone else.")
        with self._lock:
            self._failures.pop(request_id, None)
        if not approve:
            self._event(request, "approval.denied", key.principal, {"request_id": request_id})
        return decided

    def _failed(self, request: ApprovalRequest, message: str) -> NoReturn:
        """Count a failed attempt on ``request``; deny it at the limit; raise `DecisionError`."""
        with self._lock:
            failures = self._failures.get(request.request_id, 0) + 1
            self._failures[request.request_id] = failures
        if failures < self.max_failed_attempts:
            raise DecisionError(message)
        with self._lock:
            self._failures.pop(request.request_id, None)
        if self.run_store.move_request(request.request_id, "denied", source="pending") is not None:
            self._event(
                request,
                "approval.denied",
                None,
                {"request_id": request.request_id, "reason": "too_many_attempts"},
            )
        raise DecisionError(
            f"{message} After {failures} failed attempts this request is denied; ask for the "
            "call again."
        )

    def _event(
        self, request: ApprovalRequest, event: str, principal: str | None, details: dict[str, str]
    ) -> None:
        self.run_store.append_event(
            AuditEvent(
                run_id=request.run_id,
                actor="approval-page" if event == "approval.denied" else "mcp-http",
                principal=principal,
                event=event,
                subject=request.tool,
                details=details | {"input_sha256": request.input_sha256},
            )
        )


def _input_hash(executor: ToolExecutor, step: ToolCall) -> str | None:
    """Return the hash the executor would record for ``step``'s validated input, or None."""
    try:
        spec = executor.registry.get(step.tool)
        params = spec.input_model.model_validate(step.arguments)
    except (UnknownToolError, ValidationError):
        return None
    return canonical_sha256(params.model_dump(mode="json"))


def approval_gates(desk: ApprovalDesk) -> Callable[[Caller], ApprovalGate]:
    """Return what `add_mcp_route` needs: for each caller, a gate that opens their requests."""

    def gate_for(caller: Caller) -> ApprovalGate:
        def gate(
            executor: ToolExecutor, step: ToolCall, recorder: RunRecorder
        ) -> tuple[ToolCallRecord, tuple[str, str] | None]:
            record, opened = desk.call(
                executor, step, tenant=caller.tenant, principal=caller.principal, recorder=recorder
            )
            return record, (opened.request_id, desk.url(opened)) if opened is not None else None

        return gate

    return gate_for
