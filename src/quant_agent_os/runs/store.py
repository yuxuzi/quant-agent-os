"""Run stores: in memory for tests and notebooks, SQLite (standard library) for everything else.

Runs and tool calls are written as they happen. Audit events are append-only: the stores offer
no way to change or remove one, and the SQLite store's triggers reject any UPDATE or DELETE.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from types import TracebackType
from typing import Protocol, Self

from quant_agent_os.runs.models import AuditEvent, RunRecord, StoredRun
from quant_agent_os.runtime.executor import ToolCallRecord

SCHEMA_VERSION = 1


class RunStoreError(RuntimeError):
    """Raised when a run store cannot be opened or written."""


class RunStore(Protocol):
    """Persists runs, their tool calls, and audit events."""

    def save_run(self, run: RunRecord) -> None:
        """Insert ``run``, or replace the stored run with the same id (to record its end)."""
        ...

    def add_call(self, record: ToolCallRecord) -> None:
        """Store one tool call under its ``run_id``."""
        ...

    def append_event(self, event: AuditEvent) -> None:
        """Append an audit event; events are never changed or removed."""
        ...

    def get_run(self, run_id: str) -> StoredRun | None:
        """Return a run with its calls and events, or None if it is unknown."""
        ...

    def list_runs(self, limit: int = 20) -> list[RunRecord]:
        """Return the most recently started runs, newest first."""
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
        self._lock = threading.Lock()

    def save_run(self, run: RunRecord) -> None:
        """Insert or replace ``run``."""
        with self._lock:
            self._runs[run.run_id] = run.model_copy(deep=True)

    def add_call(self, record: ToolCallRecord) -> None:
        """Store one tool call."""
        with self._lock:
            self._calls.setdefault(record.run_id or "", []).append(record.model_copy(deep=True))

    def append_event(self, event: AuditEvent) -> None:
        """Append an audit event."""
        with self._lock:
            self._events.append(event.model_copy(deep=True))

    def get_run(self, run_id: str) -> StoredRun | None:
        """Return the run with its calls and events."""
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            return StoredRun(
                run=run,
                calls=list(self._calls.get(run_id, [])),
                events=[event for event in self._events if event.run_id == run_id],
            )

    def list_runs(self, limit: int = 20) -> list[RunRecord]:
        """Return the newest runs first."""
        with self._lock:
            runs = sorted(self._runs.values(), key=lambda run: run.started_at, reverse=True)
            return runs[:limit]

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
            if version not in (0, SCHEMA_VERSION):
                self._db.close()
                raise RunStoreError(
                    f"{path} has run-store schema {version}; this version reads "
                    f"{SCHEMA_VERSION}. Use a new runs file or upgrade quant-agent-os."
                )
            if str(path) != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")  # readers never block the server
            with self._db:
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

    def add_call(self, record: ToolCallRecord) -> None:
        """Store one tool call, ordered after the run's earlier calls."""
        self._write(
            "INSERT INTO tool_calls (call_id, run_id, ordinal, tool, status, input_sha256, record) "
            "VALUES (?, ?, (SELECT COUNT(*) FROM tool_calls WHERE run_id = ?), ?, ?, ?, ?)",
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

    def append_event(self, event: AuditEvent) -> None:
        """Append an audit event."""
        self._write(
            "INSERT INTO audit_events (run_id, actor, event, subject, at, details) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                event.run_id,
                event.actor,
                event.event,
                event.subject,
                event.at.isoformat(),
                json.dumps(event.details, sort_keys=True, default=str),
            ),
        )

    def get_run(self, run_id: str) -> StoredRun | None:
        """Return the run with its calls and events."""
        with self._lock:
            row = self._db.execute("SELECT record FROM runs WHERE run_id = ?", (run_id,)).fetchone()
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
                    event=event["event"],
                    subject=event["subject"],
                    at=event["at"],
                    details=json.loads(event["details"]),
                )
                for event in events
            ],
        )

    def list_runs(self, limit: int = 20) -> list[RunRecord]:
        """Return the newest runs first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT record FROM runs ORDER BY started_at DESC, run_id LIMIT ?", (limit,)
            ).fetchall()
        return [RunRecord.model_validate_json(row["record"]) for row in rows]

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
