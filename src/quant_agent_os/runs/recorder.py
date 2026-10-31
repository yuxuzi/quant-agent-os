"""Record runs as they happen: the run, each tool call, and the audit trail."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from quant_agent_os import __version__
from quant_agent_os.runs.models import AuditEvent, RunKind, RunRecord, RunStatus
from quant_agent_os.runs.redaction import redact
from quant_agent_os.runs.store import RunStore, RunStoreError
from quant_agent_os.runtime.approvals import Approval
from quant_agent_os.runtime.executor import ToolCallRecord, ToolExecutor
from quant_agent_os.runtime.plan import PlanStep

logger = logging.getLogger("quant_agent_os.runs")


class RunNotFoundError(LookupError):
    """Raised when a run id is not in the run store."""


class NotAwaitingApprovalError(RuntimeError):
    """Raised when approving or resuming a run that is not waiting for an approval."""


def outcome(records: list[ToolCallRecord]) -> RunStatus:
    """Return a run's status from its calls; a later call for the same input supersedes."""
    latest = {(record.tool, record.input_sha256): record for record in records}
    statuses = {record.status for record in latest.values()}
    if "approval_required" in statuses:
        return "awaiting_approval"
    return "completed" if statuses <= {"ok"} else "failed"


def _merged_versions(records: list[ToolCallRecord]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for record in records:
        if record.status == "ok":
            versions.update(record.data_versions)
    return versions


class RunRecorder:
    """Write runs, tool calls, and audit events to a `RunStore`.

    Arguments and outputs are redacted (`redact`) before they are stored. Audit events carry
    only ids, hashes, and statuses. A store failure is logged and never fails the call: losing
    an answer the tool already produced would not make the record more complete.

    A call that needs an approval leaves its run ``awaiting_approval``. The operator grants it
    with `approve` and runs the waiting calls with `resume`, in the same run, so the approval's
    run id, tool, and input hash all match.
    """

    def __init__(self, store: RunStore, *, actor: str = "local") -> None:
        self.store = store
        self.actor = actor

    def call(self, executor: ToolExecutor, step: PlanStep) -> ToolCallRecord:
        """Execute ``step`` as a one-call run and record it."""
        run = self.start("call", step.tool, registry_digest=executor.registry.digest())
        record = executor.execute(step, run_id=run.run_id)
        self.record_call(record)
        self.finish(
            run,
            outcome([record]),
            data_versions=record.data_versions,
            error=None if record.status == "ok" else record.error,
        )
        return record

    def approve(self, run_id: str, *, approved_by: str) -> list[Approval]:
        """Grant one single-use approval for each call the run is waiting on.

        Only an operator may call this; no tool or client can. Raises `RunNotFoundError` or
        `NotAwaitingApprovalError`.
        """
        stored = self.store.get_run(run_id)
        if stored is None:
            raise RunNotFoundError(f"No run {run_id} in the run store.")
        if stored.run.status != "awaiting_approval":
            raise NotAwaitingApprovalError(
                f"Run {run_id} is {stored.run.status}, not awaiting an approval."
            )
        approvals = [
            Approval(
                run_id=run_id,
                tool=record.tool,
                input_sha256=record.input_sha256 or "",
                approved_by=approved_by,
            )
            for record in _waiting(stored.calls)
        ]
        for approval in approvals:
            self.store.grant(approval)
            self._event(
                run_id,
                "approval.granted",
                approval.tool,
                {
                    "approval_id": approval.approval_id,
                    "input_sha256": approval.input_sha256,
                    "approved_by": approved_by,
                },
            )
        return approvals

    def resume(self, executor: ToolExecutor, run_id: str) -> list[ToolCallRecord]:
        """Run the calls ``run_id`` is waiting on, in that run, and record how it ends.

        A call whose approval is missing records ``approval_required`` again, so resuming
        without `approve` changes nothing.
        """
        stored = self.store.get_run(run_id)
        if stored is None:
            raise RunNotFoundError(f"No run {run_id} in the run store.")
        if stored.run.status != "awaiting_approval":
            raise NotAwaitingApprovalError(
                f"Run {run_id} is {stored.run.status}, not awaiting an approval."
            )
        records = [
            executor.execute(PlanStep(tool=call.tool, arguments=call.arguments), run_id=run_id)
            for call in _waiting(stored.calls)
        ]
        for record in records:
            self.record_call(record)
        history = [*stored.calls, *records]
        failed = [record.error for record in records if record.status not in ("ok",)]
        self.finish(
            stored.run,
            outcome(history),
            data_versions=_merged_versions(history),
            error=failed[0] if failed else None,
        )
        return records

    def start(
        self,
        kind: RunKind,
        goal: str,
        *,
        registry_digest: str,
        provider: str | None = None,
        run_id: str | None = None,
    ) -> RunRecord:
        """Record that a run started and return it."""
        run = RunRecord(
            run_id=run_id or uuid.uuid4().hex,
            kind=kind,
            status="running",
            goal=goal,
            actor=self.actor,
            platform_version=__version__,
            provider=provider,
            registry_digest=registry_digest,
            started_at=datetime.now(UTC),
        )
        self._safely(self.store.save_run, run)
        self._event(run.run_id, "run.started", goal, {"kind": kind})
        return run

    def record_call(self, record: ToolCallRecord) -> None:
        """Record one tool call (redacted) and its audit event."""
        stored = record.model_copy(
            update={"arguments": redact(record.arguments), "output": redact(record.output)}
        )
        self._safely(self.store.add_call, stored)
        self._event(
            record.run_id or "",
            "tool.called",
            record.tool,
            {
                "call_id": record.call_id,
                "status": record.status,
                "input_sha256": record.input_sha256,
                "duration_ms": record.duration_ms,
                "approval": record.approval,
            },
        )

    def finish(
        self,
        run: RunRecord,
        status: RunStatus,
        *,
        data_versions: dict[str, str] | None = None,
        error: str | None = None,
    ) -> RunRecord:
        """Record how the run ended and return the final record."""
        finished = run.model_copy(
            update={
                "status": status,
                "finished_at": None if status == "awaiting_approval" else datetime.now(UTC),
                "data_versions": data_versions or {},
                "error": error,
            }
        )
        self._safely(self.store.save_run, finished)
        event = "approval.required" if status == "awaiting_approval" else "run.finished"
        self._event(run.run_id, event, run.goal, {"status": status})
        return finished

    def _event(self, run_id: str, event: str, subject: str, details: dict[str, Any]) -> None:
        self._safely(
            self.store.append_event,
            AuditEvent(
                run_id=run_id, actor=self.actor, event=event, subject=subject, details=details
            ),
        )

    @staticmethod
    def _safely(write: Any, item: Any) -> None:
        try:
            write(item)
        except RunStoreError as error:
            logger.warning("Run not fully recorded: %s", error)


def _waiting(calls: list[ToolCallRecord]) -> list[ToolCallRecord]:
    """Return the calls whose latest attempt (same tool and input) needs an approval."""
    latest = {(call.tool, call.input_sha256): call for call in calls}
    return [call for call in latest.values() if call.status == "approval_required"]
