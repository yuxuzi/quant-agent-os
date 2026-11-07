"""The audit trail as plain records, for a firm's own log system.

Each line says who called what, on which data, with what result, and when. It holds hashes and
statuses, never a call's arguments, output, or internal error text, so a log system learns no more
than the audit events already say.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

from quant_agent_os.runs.models import RunRecord
from quant_agent_os.runs.store import RunStore


def audit_lines(store: RunStore, start: datetime, end: datetime) -> Iterator[dict[str, Any]]:
    """Yield each tool call and audit event from ``start`` up to ``end``, oldest first."""
    runs: dict[str, RunRecord | None] = {}

    def run_of(run_id: str) -> RunRecord | None:
        if run_id not in runs:
            stored = store.get_run(run_id)
            runs[run_id] = stored.run if stored else None
        return runs[run_id]

    lines: list[dict[str, Any]] = []
    for call in store.list_calls(start, end):
        run = run_of(call.run_id or "")
        lines.append(
            {
                "type": "call",
                "at": call.started_at.isoformat(),
                "run_id": call.run_id,
                "call_id": call.call_id,
                "tenant": run.tenant if run else None,
                "actor": run.actor if run else None,
                "principal": run.principal if run else None,
                "platform_version": run.platform_version if run else None,
                "tool": call.tool,
                "tool_version": call.tool_version,
                "input_sha256": call.input_sha256,
                "data_versions": call.data_versions,
                "status": call.status,
                "duration_ms": call.duration_ms,
                "approval": call.approval,
                "refused": call.refused,
            }
        )
    for event in store.list_events(start, end):
        lines.append(
            {
                "type": "event",
                "at": event.at.isoformat(),
                "run_id": event.run_id,
                "actor": event.actor,
                "principal": event.principal,
                "event": event.event,
                "subject": event.subject,
                "details": event.details,
            }
        )
    yield from sorted(lines, key=lambda line: datetime.fromisoformat(line["at"]))
