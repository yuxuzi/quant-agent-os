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
from quant_agent_os.runtime.executor import ToolCallRecord, ToolExecutor
from quant_agent_os.runtime.plan import PlanStep

logger = logging.getLogger("quant_agent_os.runs")


class RunRecorder:
    """Write runs, tool calls, and audit events to a `RunStore`.

    Arguments and outputs are redacted (`redact`) before they are stored. Audit events carry
    only ids, hashes, and statuses. A store failure is logged and never fails the call: losing
    an answer the tool already produced would not make the record more complete.
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
            "completed" if record.status == "ok" else "failed",
            data_versions=record.data_versions,
            error=record.error,
        )
        return record

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
                "finished_at": datetime.now(UTC),
                "data_versions": data_versions or {},
                "error": error,
            }
        )
        self._safely(self.store.save_run, finished)
        self._event(run.run_id, "run.finished", run.goal, {"status": status})
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
