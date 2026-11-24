"""An AI-use report: who called which tool on which datasets over a period, with no content.

`qaos runs report` writes it for a firm's own compliance file. It is computed from the call
records and audit events the run store already keeps, and those hold ids, hashes, statuses, and
sizes. The report adds no content: it leaves out arguments, outputs, retrieved text, and error
text. It records what the platform saw. It does not say whether any use complied with a policy.
"""

from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from quant_agent_os.observability.logging import redact_text
from quant_agent_os.runs.models import AuditEvent, RunRecord
from quant_agent_os.runs.store import RunStore
from quant_agent_os.runtime.executor import ToolCallRecord

NO_PRINCIPAL = "(none)"
RUN_NOT_FOUND = "(run not found)"
PRUNED_EVENT = "runs.pruned"
_DATASET_KEY = "dataset:"  # how an analytics call names a dataset in its data versions
_REFUSED_READ = "dataset.read "  # how an access refusal names a dataset (note_refusal)
_STANDING = "standing:"  # the approval of a call the operator pre-approved
_ERRORS = frozenset({"error", "timeout"})
_OTHER = frozenset({"invalid_input", "unknown_tool", "quota_exceeded", "busy"})
_FAR_FUTURE = datetime.max.replace(tzinfo=UTC)


class Counts(BaseModel):
    """How many calls had each outcome.

    A call can be both ``ok`` and ``refused``, when a policy refused a dataset it asked for, so
    the columns need not add up to ``calls``.
    """

    calls: int = 0
    ok: int = 0
    refused: int = 0
    needs_approval: int = 0
    approved_per_call: int = 0
    approved_standing: int = 0
    errors: int = 0
    other: int = 0


class PrincipalUsage(Counts):
    """One principal's calls, with the tools and datasets they touched."""

    principal: str
    tools: list[str] = Field(default_factory=list)
    datasets_read: list[str] = Field(default_factory=list)
    datasets_refused: list[str] = Field(default_factory=list)


class ToolUsage(Counts):
    """One tool's calls, with the versions of it that ran."""

    tool: str
    versions: list[str] = Field(default_factory=list)


class DatasetUsage(BaseModel):
    """One dataset: the calls that read it, who read it, and how often a read was refused."""

    dataset: str
    calls_read: int
    principals_read: list[str]
    refused_reads: int


class PrincipalToolUsage(Counts):
    """One principal's calls to one tool. Only the CSV is made from these rows."""

    principal: str
    tool: str


class ReportHeader(BaseModel):
    """The period, when the report was made, the platform versions it saw, and its totals."""

    start: datetime
    end: datetime = Field(description="The first instant not included")
    generated_at: datetime
    platform_versions: list[str]
    calls: int
    principals: int


class UsageReport(BaseModel):
    """The whole report. Its JSON form has five keys: the header and four sections."""

    header: ReportHeader
    by_principal: list[PrincipalUsage]
    by_tool: list[ToolUsage]
    by_dataset: list[DatasetUsage]
    notes: list[str]
    by_principal_tool: list[PrincipalToolUsage] = Field(default_factory=list, exclude=True)


def build_usage_report(
    calls: Iterable[ToolCallRecord],
    events: Iterable[AuditEvent],
    runs: Mapping[str, RunRecord],
    *,
    start: datetime,
    end: datetime,
    generated_at: datetime,
) -> UsageReport:
    """Return the report for the calls started from ``start`` up to ``end``.

    ``runs`` maps each run id to its run, which names the principal and platform version of
    its calls. ``events`` may cover more than the period: only the prune notes read them. A
    call whose run is not in ``runs`` counts under `RUN_NOT_FOUND`.
    """
    window = [call for call in calls if start <= call.started_at < end]
    by_principal: dict[str, list[ToolCallRecord]] = defaultdict(list)
    by_pair: dict[tuple[str, str], list[ToolCallRecord]] = defaultdict(list)
    by_tool: dict[str, list[ToolCallRecord]] = defaultdict(list)
    reads: Counter[str] = Counter()
    readers: dict[str, set[str]] = defaultdict(set)
    refusals: Counter[str] = Counter()
    for call in window:
        principal = _principal(call, runs)
        by_principal[principal].append(call)
        by_pair[(principal, call.tool)].append(call)
        by_tool[call.tool].append(call)
        for dataset in _datasets_read(call):
            reads[dataset] += 1
            readers[dataset].add(principal)
        for dataset in _refused_reads(call):
            refusals[dataset] += 1

    principals = [
        PrincipalUsage(
            principal=name,
            **_counts(group).model_dump(),
            tools=sorted({call.tool for call in group}),
            datasets_read=sorted({d for call in group for d in _datasets_read(call)}),
            datasets_refused=sorted({d for call in group for d in _refused_reads(call)}),
        )
        for name, group in sorted(by_principal.items())
    ]
    tools = [
        ToolUsage(
            tool=name,
            versions=_versions(group),
            **_counts(group).model_dump(),
        )
        for name, group in sorted(by_tool.items())
    ]
    datasets = [
        DatasetUsage(
            dataset=name,
            calls_read=reads[name],
            principals_read=sorted(readers[name]),
            refused_reads=refusals[name],
        )
        for name in sorted(set(reads) | set(refusals))
    ]
    pairs = [
        PrincipalToolUsage(principal=principal, tool=tool, **_counts(group).model_dump())
        for (principal, tool), group in sorted(by_pair.items())
    ]
    versions = {run.platform_version for call in window if (run := _run(call, runs)) is not None}
    return UsageReport(
        header=ReportHeader(
            start=start,
            end=end,
            generated_at=generated_at,
            platform_versions=sorted(versions),
            calls=len(window),
            principals=len(principals),
        ),
        by_principal=principals,
        by_tool=tools,
        by_dataset=datasets,
        notes=_notes(window, events, start=start, end=end),
        by_principal_tool=pairs,
    )


def read_usage_report(
    store: RunStore, *, start: datetime, end: datetime, generated_at: datetime
) -> UsageReport:
    """Read the period's calls, audit events, and runs from ``store``, and build the report."""
    calls = store.list_calls(start, end)
    events = store.list_events(start, _FAR_FUTURE)
    runs: dict[str, RunRecord] = {}
    for run_id in {call.run_id or "" for call in calls}:
        stored = store.get_run(run_id)
        if stored is not None:
            runs[run_id] = stored.run
    return build_usage_report(calls, events, runs, start=start, end=end, generated_at=generated_at)


def render_markdown(report: UsageReport) -> str:
    """Return the report as Markdown: a short header, one table per section, and the notes."""
    head = report.header
    lines = [
        "# AI use report",
        "",
        f"- Period: {head.start:%Y-%m-%d} to {head.end:%Y-%m-%d} (UTC, end day not included)",
        f"- Generated: {head.generated_at:%Y-%m-%d %H:%M} UTC",
        f"- Platform versions seen: {_join(head.platform_versions)}",
        f"- Calls: {head.calls}. Principals: {head.principals}.",
        "",
        "## By principal",
        "",
        *_table(
            ["Principal", *_COUNT_HEADINGS, "Tools", "Datasets read", "Datasets refused"],
            [
                [
                    row.principal,
                    *_count_cells(row),
                    _join(row.tools),
                    _join(row.datasets_read),
                    _join(row.datasets_refused),
                ]
                for row in report.by_principal
            ],
            empty="No calls in this period.",
        ),
        "",
        "## By tool",
        "",
        *_table(
            ["Tool", "Versions", *_COUNT_HEADINGS],
            [[row.tool, _join(row.versions), *_count_cells(row)] for row in report.by_tool],
            empty="No calls in this period.",
        ),
        "",
        "## By dataset",
        "",
        *_table(
            ["Dataset", "Calls that read it", "Principals that read it", "Refused reads"],
            [
                [
                    row.dataset,
                    str(row.calls_read),
                    _join(row.principals_read),
                    str(row.refused_reads),
                ]
                for row in report.by_dataset
            ],
            empty="No datasets read or refused in this period.",
        ),
        "",
        "## Notes",
        "",
        *[f"- {note}" for note in report.notes],
        "",
    ]
    return "\n".join(lines)


CSV_HEADINGS = (
    "period_start",
    "period_end",
    "principal",
    "tool",
    "calls",
    "ok",
    "refused",
    "needs_approval",
    "approved_per_call",
    "approved_standing",
    "errors",
    "other",
)


def render_csv(report: UsageReport) -> str:
    """Return one row per principal and tool, with the period on each row.

    The notes are not in the CSV: use the Markdown or JSON form for them.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_HEADINGS)
    start = f"{report.header.start:%Y-%m-%d}"
    end = f"{report.header.end:%Y-%m-%d}"
    for row in report.by_principal_tool:
        writer.writerow(
            [
                start,
                end,
                row.principal,
                row.tool,
                row.calls,
                row.ok,
                row.refused,
                row.needs_approval,
                row.approved_per_call,
                row.approved_standing,
                row.errors,
                row.other,
            ]
        )
    return buffer.getvalue()


def render_json(report: UsageReport) -> str:
    """Return the report as one JSON object: header, by_principal, by_tool, by_dataset, notes."""
    return report.model_dump_json(indent=2) + "\n"


def _principal(call: ToolCallRecord, runs: Mapping[str, RunRecord]) -> str:
    run = _run(call, runs)
    if run is None:
        return RUN_NOT_FOUND
    return run.principal or NO_PRINCIPAL


def _run(call: ToolCallRecord, runs: Mapping[str, RunRecord]) -> RunRecord | None:
    return runs.get(call.run_id or "")


def _datasets_read(call: ToolCallRecord) -> set[str]:
    return {
        key.removeprefix(_DATASET_KEY) for key in call.data_versions if key.startswith(_DATASET_KEY)
    }


def _versions(calls: Iterable[ToolCallRecord]) -> list[str]:
    found = {call.tool_version for call in calls}
    return sorted(version for version in found if version is not None)


def _refused_reads(call: ToolCallRecord) -> list[str]:
    """Return the dataset ids a policy refused during ``call``, one entry per refusal."""
    return [
        redact_text(entry.removeprefix(_REFUSED_READ))
        for entry in call.refused
        if entry.startswith(_REFUSED_READ)
    ]


def _counts(calls: Iterable[ToolCallRecord]) -> Counts:
    counts = Counts()
    for call in calls:
        counts.calls += 1
        counts.ok += call.status == "ok"
        counts.refused += call.status == "denied" or bool(call.refused)
        counts.needs_approval += call.status == "approval_required"
        if call.approval:
            if call.approval.startswith(_STANDING):
                counts.approved_standing += 1
            else:
                counts.approved_per_call += 1
        counts.errors += call.status in _ERRORS
        counts.other += call.status in _OTHER
    return counts


def _notes(
    window: list[ToolCallRecord], events: Iterable[AuditEvent], *, start: datetime, end: datetime
) -> list[str]:
    notes: list[str] = []
    if not window:
        notes.append(f"No calls were recorded from {start:%Y-%m-%d} to {end:%Y-%m-%d}.")
    pruned = sorted(
        (e for e in events if e.event == PRUNED_EVENT and e.at >= start), key=lambda e: e.at
    )
    if pruned:
        latest = pruned[-1]
        count = len(pruned)
        notes.append(
            f"Pruned calls are not counted. {count} prune{'s' if count != 1 else ''} ran on or "
            f"after {start:%Y-%m-%d}; the latest, at {latest.at:%Y-%m-%d %H:%M} UTC, removed runs "
            f"started before {_day(latest.subject)}. Calls in this period may be missing."
        )
    notes += [
        "Not in this report: arguments, outputs, retrieved text, and error text. It holds counts, "
        "names of principals, tools, and datasets, and versions.",
        "Refused: the platform denied the tool, or an access policy refused a dataset during the "
        "call. Errors: the tool failed or timed out. Other: invalid input, an unknown tool, a "
        "quota, or a busy server.",
        "Approved per call: an operator approved that call. Approved standing: the operator "
        "pre-approved the tool for every call.",
        "A call can be both OK and refused, when a policy refused a dataset it asked for. So the "
        "status columns need not add up to Calls.",
        "Refused dataset names are the ids the caller asked for, as the run store recorded them.",
        f"{NO_PRINCIPAL} is a call whose run has no principal. {RUN_NOT_FOUND} is a call whose "
        "run is missing from the store.",
        "This is a record of what the platform saw. It does not say whether any use was allowed "
        "under a firm's policies.",
    ]
    return notes


def _day(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d")
    except ValueError:
        return value


_COUNT_HEADINGS = (
    "Calls",
    "OK",
    "Refused",
    "Needs approval",
    "Approved per call",
    "Approved standing",
    "Errors",
    "Other",
)


def _count_cells(counts: Counts) -> list[str]:
    values: list[Any] = [
        counts.calls,
        counts.ok,
        counts.refused,
        counts.needs_approval,
        counts.approved_per_call,
        counts.approved_standing,
        counts.errors,
        counts.other,
    ]
    return [str(value) for value in values]


def _join(items: list[str]) -> str:
    return ", ".join(items) if items else "-"


def _table(headings: list[str], rows: list[list[str]], *, empty: str) -> list[str]:
    if not rows:
        return [empty]
    lines = ["| " + " | ".join(headings) + " |", "|" + "---|" * len(headings)]
    for row in rows:
        cells = [cell.replace("|", "\\|").replace("\n", " ") for cell in row]
        lines.append("| " + " | ".join(cells) + " |")
    return lines
