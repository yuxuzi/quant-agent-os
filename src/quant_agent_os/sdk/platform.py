"""The `Platform` facade: one object for calling tools, making reports, and reading runs back."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self

from quant_agent_os.reporting.generator import build_report
from quant_agent_os.reporting.models import Report
from quant_agent_os.runs.models import RunRecord, StoredRun
from quant_agent_os.runs.recorder import RunRecorder, outcome
from quant_agent_os.runs.store import MemoryRunStore, RunStore
from quant_agent_os.runtime.approvals import Approval
from quant_agent_os.runtime.call import ToolCall
from quant_agent_os.runtime.executor import ToolCallRecord, ToolExecutor
from quant_agent_os.tools.contracts import ToolSpec
from quant_agent_os.tools.permissions import PermissionPolicy
from quant_agent_os.tools.registry import ToolRegistry


class Platform:
    """Call registered tools and make reports, with every run persisted to a `RunStore`.

    Calls go through the same `ToolExecutor` as the MCP server, so an SDK call and an MCP call
    with the same arguments produce the same record. Runs are kept in memory unless a
    ``run_store`` (for example a `SqliteRunStore`) is given.

        platform = Platform(research_tools(app.research), run_store=SqliteRunStore("runs.db"))
        record = platform.call("find_related_signals", query="short-term reversal")
        platform.get_run(record.run_id)
    """

    def __init__(
        self,
        tools: ToolRegistry | Iterable[ToolSpec[Any, Any]],
        *,
        policy: PermissionPolicy | None = None,
        run_store: RunStore | None = None,
        actor: str = "sdk",
    ) -> None:
        self.registry = tools if isinstance(tools, ToolRegistry) else ToolRegistry(tools)
        self.policy = policy or PermissionPolicy()
        self.run_store = run_store or MemoryRunStore()
        self.executor = ToolExecutor(self.registry, self.policy, approvals=self.run_store)
        self.recorder = RunRecorder(self.run_store, actor=actor)

    def call(
        self, tool: str, arguments: Mapping[str, Any] | None = None, /, **kwargs: Any
    ) -> ToolCallRecord:
        """Call ``tool`` once and return its record; failures are recorded, never raised.

        Arguments come as a mapping, as keywords, or both (keywords win). ``record.result()``
        returns the output, or raises `ToolCallError` for a failed call.
        """
        step = ToolCall(tool=tool, arguments={**(arguments or {}), **kwargs})
        return self.recorder.call(self.executor, step)

    def report(self, goal: str, calls: Iterable[ToolCall]) -> Report:
        """Run ``calls`` in order as one recorded run, and return its evidence-linked report.

        The calls are the caller's; nothing here reads a result to choose what runs next. A call
        that fails becomes a warning in the report, and the later calls still run.
        """
        started_at = datetime.now(UTC)
        digest = self.registry.digest()
        run = self.recorder.start("plan", goal, registry_digest=digest)
        records = []
        for call in calls:
            record = self.executor.execute(call, run_id=run.run_id)
            self.recorder.record_call(record)
            records.append(record)
        report = build_report(
            goal,
            records,
            run_id=run.run_id,
            registry_digest=digest,
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )
        self.recorder.finish(run, outcome(records), data_versions=report.metadata.data_versions)
        return report

    def approve(self, run_id: str, *, approved_by: str = "operator") -> list[Approval]:
        """Approve, once, each call ``run_id`` is waiting on (an operator action).

        A consequential call returns ``approval_required`` and leaves its run
        ``awaiting_approval``; approve it, then `resume` the run. Never call this on a model's
        behalf: approvals are how the operator says yes.
        """
        return self.recorder.approve(run_id, approved_by=approved_by)

    def resume(self, run_id: str) -> list[ToolCallRecord]:
        """Run the approved calls ``run_id`` was waiting on, in the same run."""
        return self.recorder.resume(self.executor, run_id)

    def get_run(self, run_id: str) -> StoredRun | None:
        """Return a persisted run with its tool calls and audit events."""
        return self.run_store.get_run(run_id)

    def runs(self, limit: int = 20) -> list[RunRecord]:
        """Return the most recent runs, newest first."""
        return self.run_store.list_runs(limit)

    def close(self) -> None:
        """Close the run store."""
        self.run_store.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
