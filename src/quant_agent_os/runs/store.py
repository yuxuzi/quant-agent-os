"""Run stores: in memory for tests and notebooks, SQLite (standard library) for everything else.

Runs and tool calls are written as they happen. Audit events are append-only: the stores offer
no way to change or remove one, and the SQLite store's triggers reject any UPDATE or DELETE.
Both stores are also the `ApprovalLedger` for their runs, so an approval is consumed in the same
place its run is recorded. A call's usage event (decision 0015) is written with the call, in one
transaction, so a recorded call always has one.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Protocol, Self

from quant_agent_os.runs.models import (
    ApprovalRequest,
    AuditEvent,
    RequestStatus,
    RunRecord,
    StoredRun,
    UsageEvent,
)
from quant_agent_os.runtime.approvals import Approval, MemoryApprovalLedger
from quant_agent_os.runtime.executor import ToolCallRecord

# 2 added approvals, 3 the audit events' principal, 4 approval requests, 5 usage events
SCHEMA_VERSION = 5


class RunStoreError(RuntimeError):
    """Raised when a run store cannot be opened or written."""


class RunStore(Protocol):
    """Persists runs, their tool calls, audit events, and approvals (an `ApprovalLedger`)."""

    def grant(self, approval: Approval) -> None:
        """Store a new, unused approval."""
        ...

    def consume(self, run_id: str, tool: str, input_sha256: str) -> Approval | None:
        """Mark one matching unused approval as used and return it, or return None."""
        ...

    def save_run(self, run: RunRecord) -> None:
        """Insert ``run``, or replace the stored run with the same id (to record its end)."""
        ...

    def add_call(self, record: ToolCallRecord, usage: UsageEvent | None = None) -> None:
        """Store one tool call under its ``run_id``, and its ``usage`` in the same transaction."""
        ...

    def append_event(self, event: AuditEvent) -> None:
        """Append an audit event; events are never changed or removed."""
        ...

    def get_run(self, run_id: str, *, tenant: str | None = None) -> StoredRun | None:
        """Return a run with its calls and events, or None if it is unknown.

        With ``tenant``, a run of another tenant is unknown too (decision 0013).
        """
        ...

    def list_runs(self, limit: int = 20, *, tenant: str | None = None) -> list[RunRecord]:
        """Return the most recently started runs, newest first; only ``tenant``'s when given."""
        ...

    def list_usage(
        self, start: datetime, end: datetime, *, tenant: str | None = None
    ) -> list[UsageEvent]:
        """Return the usage events from ``start`` up to ``end``, oldest first (decision 0015).

        Only ``tenant``'s when given; reading every tenant's usage is for operators only.
        """
        ...

    def count_billable(self, tenant: str, start: datetime, end: datetime) -> int:
        """Return how many of ``tenant``'s calls from ``start`` up to ``end`` were billable."""
        ...

    def save_request(self, request: ApprovalRequest) -> None:
        """Store a new approval request (decision 0014)."""
        ...

    def get_request(self, request_id: str) -> ApprovalRequest | None:
        """Return an approval request, or None if it is unknown."""
        ...

    def find_request(
        self, tenant: str, requested_by: str | None, tool: str, input_sha256: str
    ) -> ApprovalRequest | None:
        """Return the newest approved, unused request for this exact call, or None."""
        ...

    def move_request(
        self,
        request_id: str,
        to: RequestStatus,
        *,
        source: RequestStatus,
        decided_by: str | None = None,
    ) -> ApprovalRequest | None:
        """Move a request from ``source`` to ``to`` atomically; None if it was not in ``source``."""
        ...

    def close(self) -> None:
        """Release the store's resources."""
        ...


class MemoryRunStore:
    """A `RunStore` that keeps everything in memory, for tests and notebooks."""

    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._calls: dict[str, list[ToolCallRecord]] = {}
        self._events: list[AuditEvent] = []
        self._usage: list[UsageEvent] = []
        self._lock = threading.Lock()
        self._approvals = MemoryApprovalLedger()
        self._requests: dict[str, ApprovalRequest] = {}

    def grant(self, approval: Approval) -> None:
        """Store a new, unused approval."""
        self._approvals.grant(approval)

    def consume(self, run_id: str, tool: str, input_sha256: str) -> Approval | None:
        """Mark one matching unused approval as used and return it."""
        return self._approvals.consume(run_id, tool, input_sha256)

    def save_run(self, run: RunRecord) -> None:
        """Insert or replace ``run``."""
        with self._lock:
            self._runs[run.run_id] = run.model_copy(deep=True)

    def add_call(self, record: ToolCallRecord, usage: UsageEvent | None = None) -> None:
        """Store one tool call and its usage event."""
        with self._lock:
            self._calls.setdefault(record.run_id or "", []).append(record.model_copy(deep=True))
            if usage is not None:
                self._usage.append(usage.model_copy())

    def list_usage(
        self, start: datetime, end: datetime, *, tenant: str | None = None
    ) -> list[UsageEvent]:
        """Return the usage events from ``start`` up to ``end``, oldest first."""
        with self._lock:
            found = [
                event
                for event in self._usage
                if start <= event.at < end and tenant in (None, event.tenant)
            ]
        return sorted(found, key=lambda event: event.at)

    def count_billable(self, tenant: str, start: datetime, end: datetime) -> int:
        """Return how many of ``tenant``'s calls in the window were billable."""
        with self._lock:
            return sum(
                event.billable and event.tenant == tenant and start <= event.at < end
                for event in self._usage
            )

    def append_event(self, event: AuditEvent) -> None:
        """Append an audit event."""
        with self._lock:
            self._events.append(event.model_copy(deep=True))

    def get_run(self, run_id: str, *, tenant: str | None = None) -> StoredRun | None:
        """Return the run with its calls and events."""
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or (tenant is not None and run.tenant != tenant):
                return None
            return StoredRun(
                run=run,
                calls=list(self._calls.get(run_id, [])),
                events=[event for event in self._events if event.run_id == run_id],
            )

    def list_runs(self, limit: int = 20, *, tenant: str | None = None) -> list[RunRecord]:
        """Return the newest runs first."""
        with self._lock:
            runs = [run for run in self._runs.values() if tenant is None or run.tenant == tenant]
            runs.sort(key=lambda run: run.started_at, reverse=True)
            return runs[:limit]

    def save_request(self, request: ApprovalRequest) -> None:
        """Store a new approval request."""
        with self._lock:
            self._requests[request.request_id] = request.model_copy(deep=True)

    def get_request(self, request_id: str) -> ApprovalRequest | None:
        """Return an approval request."""
        with self._lock:
            found = self._requests.get(request_id)
            return found.model_copy(deep=True) if found else None

    def find_request(
        self, tenant: str, requested_by: str | None, tool: str, input_sha256: str
    ) -> ApprovalRequest | None:
        """Return the newest approved, unused request for this exact call."""
        with self._lock:
            matches = [
                request
                for request in self._requests.values()
                if request.status == "approved"
                and (request.tenant, request.requested_by, request.tool, request.input_sha256)
                == (tenant, requested_by, tool, input_sha256)
            ]
            newest = max(matches, key=lambda request: request.created_at, default=None)
            return newest.model_copy(deep=True) if newest else None

    def move_request(
        self,
        request_id: str,
        to: RequestStatus,
        *,
        source: RequestStatus,
        decided_by: str | None = None,
    ) -> ApprovalRequest | None:
        """Move a request between statuses atomically."""
        with self._lock:
            found = self._requests.get(request_id)
            if found is None or found.status != source:
                return None
            moved = found.model_copy(update=_decision(to, decided_by))
            self._requests[request_id] = moved
            return moved.model_copy(deep=True)

    def close(self) -> None:
        """Nothing to release."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    tenant TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    record TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_started ON runs (started_at);
CREATE INDEX IF NOT EXISTS runs_tenant_started ON runs (tenant, started_at);
CREATE TABLE IF NOT EXISTS tool_calls (
    call_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    tool TEXT NOT NULL,
    status TEXT NOT NULL,
    input_sha256 TEXT,
    record TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tool_calls_run ON tool_calls (run_id, ordinal);
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    principal TEXT,
    event TEXT NOT NULL,
    subject TEXT NOT NULL,
    at TEXT NOT NULL,
    details TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_events_run ON audit_events (run_id, id);
CREATE TRIGGER IF NOT EXISTS audit_events_no_update BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_events_no_delete BEFORE DELETE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    used_at TEXT
);
CREATE INDEX IF NOT EXISTS approvals_call ON approvals (run_id, tool, input_sha256);
CREATE TABLE IF NOT EXISTS approval_requests (
    request_id TEXT PRIMARY KEY,
    tenant TEXT NOT NULL,
    requested_by TEXT,
    tool TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    record TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS approval_requests_call
    ON approval_requests (tenant, tool, input_sha256, status);
CREATE TABLE IF NOT EXISTS usage_events (
    call_id TEXT PRIMARY KEY,
    tenant TEXT NOT NULL,
    tool TEXT NOT NULL,
    status TEXT NOT NULL,
    billable INTEGER NOT NULL,
    at TEXT NOT NULL,
    record TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_events_tenant_at ON usage_events (tenant, at);
"""


class SqliteRunStore:
    """A `RunStore` in one SQLite file, safe to share between threads.

    Each record is stored whole as JSON, with the columns needed to find it alongside.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        try:
            if str(path) != ":memory:":
                self.path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(path), check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                self._db.close()
                raise RunStoreError(
                    f"{path} has run-store schema {version}; this version reads "
                    f"{SCHEMA_VERSION}. Use a new runs file or upgrade quant-agent-os."
                )
            if str(path) != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")  # readers never block the server
            with self._db:
                columns = {row[1] for row in self._db.execute("PRAGMA table_info(audit_events)")}
                if columns and "principal" not in columns:  # a version 1 or 2 store
                    self._db.execute("ALTER TABLE audit_events ADD COLUMN principal TEXT")
                self._db.executescript(_SCHEMA)
                self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except (sqlite3.Error, OSError) as error:
            raise RunStoreError(f"Cannot open the run store at {path}: {error}") from error

    def _write(self, sql: str, parameters: tuple[object, ...]) -> None:
        with self._lock:
            try:
                with self._db:
                    self._db.execute(sql, parameters)
            except sqlite3.Error as error:
                raise RunStoreError(
                    f"Cannot write to the run store at {self.path}: {error}"
                ) from error

    def grant(self, approval: Approval) -> None:
        """Store a new, unused approval."""
        self._write(
            "INSERT INTO approvals (approval_id, run_id, tool, input_sha256, approved_by, "
            "approved_at, used_at) VALUES (?, ?, ?, ?, ?, ?, NULL)",
            (
                approval.approval_id,
                approval.run_id,
                approval.tool,
                approval.input_sha256,
                approval.approved_by,
                approval.approved_at.isoformat(),
            ),
        )

    def consume(self, run_id: str, tool: str, input_sha256: str) -> Approval | None:
        """Mark one matching unused approval as used, atomically, and return it."""
        used_at = datetime.now(UTC)
        with self._lock:
            try:
                with self._db:
                    row = self._db.execute(
                        "SELECT * FROM approvals WHERE run_id = ? AND tool = ? AND "
                        "input_sha256 = ? AND used_at IS NULL ORDER BY approved_at LIMIT 1",
                        (run_id, tool, input_sha256),
                    ).fetchone()
                    if row is None:
                        return None
                    claimed = self._db.execute(
                        "UPDATE approvals SET used_at = ? "
                        "WHERE approval_id = ? AND used_at IS NULL",
                        (used_at.isoformat(), row["approval_id"]),
                    ).rowcount
            except sqlite3.Error as error:
                raise RunStoreError(f"Cannot use an approval in {self.path}: {error}") from error
        if claimed != 1:
            return None
        return Approval(
            approval_id=row["approval_id"],
            run_id=row["run_id"],
            tool=row["tool"],
            input_sha256=row["input_sha256"],
            approved_by=row["approved_by"],
            approved_at=row["approved_at"],
            used_at=used_at,
        )

    def save_run(self, run: RunRecord) -> None:
        """Insert or replace ``run``."""
        self._write(
            "INSERT OR REPLACE INTO runs (run_id, tenant, kind, status, started_at, record) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                run.run_id,
                run.tenant,
                run.kind,
                run.status,
                run.started_at.isoformat(),
                run.model_dump_json(),
            ),
        )

    def add_call(self, record: ToolCallRecord, usage: UsageEvent | None = None) -> None:
        """Store one tool call, ordered after the run's earlier calls, with its usage event."""
        with self._lock:
            try:
                with self._db:
                    self._db.execute(
                        "INSERT INTO tool_calls (call_id, run_id, ordinal, tool, status, "
                        "input_sha256, record) VALUES "
                        "(?, ?, (SELECT COUNT(*) FROM tool_calls WHERE run_id = ?), ?, ?, ?, ?)",
                        (
                            record.call_id,
                            record.run_id or "",
                            record.run_id or "",
                            record.tool,
                            record.status,
                            record.input_sha256,
                            record.model_dump_json(),
                        ),
                    )
                    if usage is not None:
                        self._db.execute(
                            "INSERT INTO usage_events (call_id, tenant, tool, status, billable, "
                            "at, record) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                usage.call_id,
                                usage.tenant,
                                usage.tool,
                                usage.status,
                                int(usage.billable),
                                _instant(usage.at),
                                usage.model_dump_json(),
                            ),
                        )
            except sqlite3.Error as error:
                raise RunStoreError(
                    f"Cannot write to the run store at {self.path}: {error}"
                ) from error

    def list_usage(
        self, start: datetime, end: datetime, *, tenant: str | None = None
    ) -> list[UsageEvent]:
        """Return the usage events from ``start`` up to ``end``, oldest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT record FROM usage_events WHERE at >= ? AND at < ? "
                "AND (? IS NULL OR tenant = ?) ORDER BY at, call_id",
                (_instant(start), _instant(end), tenant, tenant),
            ).fetchall()
        return [UsageEvent.model_validate_json(row["record"]) for row in rows]

    def count_billable(self, tenant: str, start: datetime, end: datetime) -> int:
        """Return how many of ``tenant``'s calls in the window were billable."""
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS n FROM usage_events "
                "WHERE tenant = ? AND at >= ? AND at < ? AND billable = 1",
                (tenant, _instant(start), _instant(end)),
            ).fetchone()
        return int(row["n"])

    def append_event(self, event: AuditEvent) -> None:
        """Append an audit event."""
        self._write(
            "INSERT INTO audit_events (run_id, actor, principal, event, subject, at, details) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event.run_id,
                event.actor,
                event.principal,
                event.event,
                event.subject,
                event.at.isoformat(),
                json.dumps(event.details, sort_keys=True, default=str),
            ),
        )

    def get_run(self, run_id: str, *, tenant: str | None = None) -> StoredRun | None:
        """Return the run with its calls and events."""
        with self._lock:
            row = self._db.execute(
                "SELECT record FROM runs WHERE run_id = ? AND (? IS NULL OR tenant = ?)",
                (run_id, tenant, tenant),
            ).fetchone()
            if row is None:
                return None
            calls = self._db.execute(
                "SELECT record FROM tool_calls WHERE run_id = ? ORDER BY ordinal", (run_id,)
            ).fetchall()
            events = self._db.execute(
                "SELECT * FROM audit_events WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
        return StoredRun(
            run=RunRecord.model_validate_json(row["record"]),
            calls=[ToolCallRecord.model_validate_json(call["record"]) for call in calls],
            events=[
                AuditEvent(
                    run_id=event["run_id"],
                    actor=event["actor"],
                    principal=event["principal"],
                    event=event["event"],
                    subject=event["subject"],
                    at=event["at"],
                    details=json.loads(event["details"]),
                )
                for event in events
            ],
        )

    def list_runs(self, limit: int = 20, *, tenant: str | None = None) -> list[RunRecord]:
        """Return the newest runs first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT record FROM runs WHERE (? IS NULL OR tenant = ?) "
                "ORDER BY started_at DESC, run_id LIMIT ?",
                (tenant, tenant, limit),
            ).fetchall()
        return [RunRecord.model_validate_json(row["record"]) for row in rows]

    def save_request(self, request: ApprovalRequest) -> None:
        """Store a new approval request."""
        self._write(
            "INSERT INTO approval_requests (request_id, tenant, requested_by, tool, "
            "input_sha256, status, created_at, record) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                request.request_id,
                request.tenant,
                request.requested_by,
                request.tool,
                request.input_sha256,
                request.status,
                request.created_at.isoformat(),
                request.model_dump_json(),
            ),
        )

    def get_request(self, request_id: str) -> ApprovalRequest | None:
        """Return an approval request."""
        with self._lock:
            row = self._db.execute(
                "SELECT record FROM approval_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
        return ApprovalRequest.model_validate_json(row["record"]) if row else None

    def find_request(
        self, tenant: str, requested_by: str | None, tool: str, input_sha256: str
    ) -> ApprovalRequest | None:
        """Return the newest approved, unused request for this exact call."""
        with self._lock:
            row = self._db.execute(
                "SELECT record FROM approval_requests WHERE tenant = ? AND tool = ? AND "
                "input_sha256 = ? AND status = 'approved' AND requested_by IS ? "
                "ORDER BY created_at DESC LIMIT 1",
                (tenant, tool, input_sha256, requested_by),
            ).fetchone()
        return ApprovalRequest.model_validate_json(row["record"]) if row else None

    def move_request(
        self,
        request_id: str,
        to: RequestStatus,
        *,
        source: RequestStatus,
        decided_by: str | None = None,
    ) -> ApprovalRequest | None:
        """Move a request between statuses atomically."""
        with self._lock:
            try:
                with self._db:
                    row = self._db.execute(
                        "SELECT record FROM approval_requests WHERE request_id = ? AND status = ?",
                        (request_id, source),
                    ).fetchone()
                    if row is None:
                        return None
                    moved = ApprovalRequest.model_validate_json(row["record"]).model_copy(
                        update=_decision(to, decided_by)
                    )
                    self._db.execute(
                        "UPDATE approval_requests SET status = ?, record = ? "
                        "WHERE request_id = ? AND status = ?",
                        (to, moved.model_dump_json(), request_id, source),
                    )
            except sqlite3.Error as error:
                raise RunStoreError(f"Cannot write to the run store: {error}") from error
        return moved

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._db.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _instant(at: datetime) -> str:
    """Return ``at`` in UTC with microseconds, so stored instants sort as text."""
    return at.astimezone(UTC).isoformat(timespec="microseconds")


def _decision(to: RequestStatus, decided_by: str | None) -> dict[str, object]:
    """Return the fields a status change sets: who decided, and when (not for ``used``)."""
    if to == "used":
        return {"status": to}
    return {"status": to, "decided_by": decided_by, "decided_at": datetime.now(UTC)}
