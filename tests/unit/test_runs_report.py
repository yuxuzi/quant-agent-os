"""The AI-use report: `qaos runs report` counts calls and names, and never holds content (C3)."""

import csv
import io
import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, Field
from typer.testing import CliRunner

from quant_agent_os import __version__
from quant_agent_os.runs import (
    AuditEvent,
    MemoryRunStore,
    RunRecorder,
    RunStore,
    SqliteRunStore,
    UsageReport,
    build_usage_report,
    read_usage_report,
    render_csv,
    render_json,
    render_markdown,
)
from quant_agent_os.runs.report import RUN_NOT_FOUND
from quant_agent_os.runtime import ToolCall, ToolCallRecord, ToolExecutor
from quant_agent_os.tools import PermissionPolicy, ToolRegistry, tool
from quant_agent_os.tools.access import note_refusal
from quant_agent_os.tools.permissions import Permission
from quant_platform.cli import app

RUNNER = CliRunner()
GENERATED = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
SECRET_ARGUMENT = "sk-live-ARGUMENT-SECRET-7731"
SECRET_OUTPUT = "OUTPUT-PASSAGE-SECRET-4410"
SECRET_ERROR = "ERROR-TEXT-SECRET-9058"
FUND = "fund-returns"
HEDGE = "hedge-book"
CONTENT_KEYS = {"arguments", "output", "error", "error_detail", "answer", "receipt"}


class Query(BaseModel):
    query: str


class Answer(BaseModel):
    answer: str
    data_versions: dict[str, str] = Field(default_factory=dict)


class Receipt(BaseModel):
    receipt: str


@tool(name="analyze", version="2.1.0", description="Analyse one fund's returns.")
def analyze(params: Query) -> Answer:
    return Answer(answer=SECRET_OUTPUT, data_versions={f"dataset:{FUND}": "fp-1"})


@tool(name="compare", description="Compare with a hedge book the caller may not read.")
def compare(params: Query) -> Receipt:
    note_refusal("dataset.read", HEDGE)  # what the dataset store records for a refused read
    raise LookupError(f"No dataset {HEDGE}.")


@tool(
    name="publish",
    description="Publish a note to the team.",
    permissions=(Permission.EXTERNAL_ACTION,),
)
def publish(params: Query) -> Receipt:
    return Receipt(receipt=f"published {params.query}")


@tool(
    name="archive",
    description="Archive a month's book.",
    permissions=(Permission.EXTERNAL_ACTION,),
)
def archive(params: Query) -> Receipt:
    return Receipt(receipt=f"archived {params.query}")


@tool(name="broken", description="Fail on purpose.")
def broken(params: Query) -> Receipt:
    raise RuntimeError(SECRET_ERROR)


@tool(name="fetch_web", description="Fetch a page.", permissions=(Permission.NETWORK,))
def fetch_web(params: Query) -> Receipt:
    return Receipt(receipt=params.query)


REGISTRY = ToolRegistry([analyze, compare, publish, archive, broken, fetch_web])
# EXTERNAL_ACTION is granted, so archive needs an approval per call; publish has a standing one.
POLICY = PermissionPolicy(
    granted=frozenset({Permission.READ_LOCAL_DATA, Permission.EXTERNAL_ACTION}),
    preapproved_tools={Permission.EXTERNAL_ACTION: frozenset({"publish"})},
)


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[RunStore]:
    run_store: RunStore = (
        MemoryRunStore() if request.param == "memory" else SqliteRunStore(tmp_path / "runs.db")
    )
    yield run_store
    run_store.close()


def _run_id(record: ToolCallRecord) -> str:
    assert record.run_id is not None
    return record.run_id


def _populate(store: RunStore) -> None:
    """Record ten calls by two principals: ok, refused, needs approval, approved, and so on."""
    alice = RunRecorder(store, actor="rest", principal="alice")
    bob = RunRecorder(store, actor="mcp", principal="bob")
    executor = ToolExecutor(REGISTRY, POLICY, approvals=store)
    alice.call(executor, ToolCall(tool="analyze", arguments={"query": SECRET_ARGUMENT}))
    alice.call(executor, ToolCall(tool="analyze", arguments={"query": "month"}))
    alice.call(executor, ToolCall(tool="analyze", arguments={}))  # fails validation
    alice.call(executor, ToolCall(tool="compare", arguments={"query": "hedge"}))  # refused
    waiting = alice.call(executor, ToolCall(tool="archive", arguments={"query": "2026-10"}))
    alice.approve(_run_id(waiting), approved_by="ops")
    alice.resume(executor, _run_id(waiting))  # ok, with a per-call approval
    alice.call(executor, ToolCall(tool="broken", arguments={"query": "x"}))
    bob.call(executor, ToolCall(tool="publish", arguments={"query": "note"}))  # standing
    bob.call(executor, ToolCall(tool="fetch_web", arguments={"query": "page"}))  # denied
    bob.call(executor, ToolCall(tool="analyze", arguments={"query": "month"}))


def _report(
    store: RunStore, *, start: datetime | None = None, end: datetime | None = None
) -> UsageReport:
    now = datetime.now(UTC)
    return read_usage_report(
        store,
        start=start or now - timedelta(hours=1),
        end=end or now + timedelta(hours=1),
        generated_at=GENERATED,
    )


def _counts(row: Any) -> tuple[int, ...]:
    """Return the counts in the order the assertions name them: calls, ok, and so on."""
    return (
        row.calls,
        row.ok,
        row.refused,
        row.needs_approval,
        row.approved_per_call,
        row.approved_standing,
        row.errors,
        row.other,
    )


def _plain(text: str) -> str:
    """``text`` without terminal colour codes, which CI's forced colour adds to help output."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_the_header_and_principals_are_counted(store: RunStore) -> None:
    _populate(store)
    report = _report(store)

    assert report.header.calls == 10
    assert report.header.principals == 2
    assert report.header.platform_versions == [__version__]
    assert report.header.generated_at == GENERATED
    assert [row.principal for row in report.by_principal] == ["alice", "bob"]
    assert not [note for note in report.notes if "Pruned" in note]


def test_each_principal_has_its_counts_tools_and_datasets(store: RunStore) -> None:
    _populate(store)
    rows = {row.principal: row for row in _report(store).by_principal}

    # calls, ok, refused, needs approval, approved per call, standing, errors, other
    assert _counts(rows["alice"]) == (7, 3, 1, 1, 1, 0, 2, 1)
    assert rows["alice"].tools == ["analyze", "archive", "broken", "compare"]
    assert rows["alice"].datasets_read == [FUND]
    assert rows["alice"].datasets_refused == [HEDGE]

    assert _counts(rows["bob"]) == (3, 2, 1, 0, 0, 1, 0, 0)
    assert rows["bob"].tools == ["analyze", "fetch_web", "publish"]
    assert rows["bob"].datasets_read == [FUND]
    assert rows["bob"].datasets_refused == []


def test_each_tool_has_its_counts_and_versions(store: RunStore) -> None:
    _populate(store)
    rows = {row.tool: row for row in _report(store).by_tool}

    assert _counts(rows["analyze"]) == (4, 3, 0, 0, 0, 0, 0, 1)
    assert rows["analyze"].versions == ["2.1.0"]
    assert _counts(rows["archive"]) == (2, 1, 0, 1, 1, 0, 0, 0)
    assert _counts(rows["publish"]) == (1, 1, 0, 0, 0, 1, 0, 0)
    assert _counts(rows["fetch_web"]) == (1, 0, 1, 0, 0, 0, 0, 0)
    assert _counts(rows["compare"]) == (1, 0, 1, 0, 0, 0, 1, 0)
    assert rows["compare"].versions == ["1.0.0"]
    assert _counts(rows["broken"]) == (1, 0, 0, 0, 0, 0, 1, 0)


def test_each_dataset_has_its_readers_and_refusals(store: RunStore) -> None:
    _populate(store)
    rows = {row.dataset: row for row in _report(store).by_dataset}

    assert rows[FUND].calls_read == 3
    assert rows[FUND].principals_read == ["alice", "bob"]
    assert rows[FUND].refused_reads == 0
    assert rows[HEDGE].calls_read == 0
    assert rows[HEDGE].principals_read == []
    assert rows[HEDGE].refused_reads == 1


def test_every_format_has_its_sections(store: RunStore) -> None:
    _populate(store)
    report = _report(store)

    markdown = render_markdown(report)
    for heading in (
        "# AI use report",
        "## By principal",
        "## By tool",
        "## By dataset",
        "## Notes",
    ):
        assert heading in markdown
    assert "| alice | 7 | 3 | 1 | 1 | 1 | 0 | 2 | 1 |" in markdown

    rows = list(csv.DictReader(io.StringIO(render_csv(report))))
    assert len(rows) == 7  # alice's four tools and bob's three
    archive = next(row for row in rows if row["principal"] == "alice" and row["tool"] == "archive")
    assert (archive["calls"], archive["needs_approval"], archive["approved_per_call"]) == (
        "2",
        "1",
        "1",
    )
    assert {row["period_start"] for row in rows} == {report.header.start.strftime("%Y-%m-%d")}

    data = json.loads(render_json(report))
    assert set(data) == {"header", "by_principal", "by_tool", "by_dataset", "notes"}
    assert data["header"]["calls"] == 10
    assert [row["principal"] for row in data["by_principal"]] == ["alice", "bob"]


def test_no_format_holds_arguments_outputs_or_error_text(store: RunStore) -> None:
    _populate(store)
    report = _report(store)
    texts = [render_markdown(report), render_csv(report), render_json(report)]

    for text in texts:
        for secret in (SECRET_ARGUMENT, SECRET_OUTPUT, SECRET_ERROR, "No dataset", "2026-10"):
            assert secret not in text

    def keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value)) if value else set()
        return set()

    assert keys(json.loads(render_json(report))).isdisjoint(CONTENT_KEYS)


def test_an_empty_window_says_so(store: RunStore) -> None:
    _populate(store)
    later = datetime.now(UTC) + timedelta(days=30)
    report = _report(store, start=later, end=later + timedelta(days=30))

    assert report.header.calls == 0
    assert report.by_principal == [] and report.by_tool == [] and report.by_dataset == []
    assert any("No calls were recorded" in note for note in report.notes)
    assert "No calls in this period." in render_markdown(report)
    assert json.loads(render_json(report))["by_principal"] == []
    assert len(list(csv.reader(io.StringIO(render_csv(report))))) == 1  # the header only


def test_a_prune_in_or_after_the_period_is_noted_and_its_calls_are_not_counted(
    store: RunStore,
) -> None:
    _populate(store)
    # A second ahead: Windows' clock is coarse enough for a call to share "now" with the cutoff.
    cutoff = datetime.now(UTC) + timedelta(seconds=1)
    store.prune_runs(
        cutoff,
        AuditEvent(
            run_id="",
            actor="cli",
            event="runs.pruned",
            subject=cutoff.isoformat(),
            details={"older_than": "0d", "runs": 10},
        ),
    )
    bob = RunRecorder(store, actor="mcp", principal="bob")
    bob.call(
        ToolExecutor(REGISTRY, POLICY, approvals=store),
        ToolCall(tool="publish", arguments={"query": "after the prune"}),
    )

    report = _report(store)

    assert report.header.calls == 1  # only the call made after the prune
    assert [row.principal for row in report.by_principal] == ["bob"]
    notes = " ".join(report.notes)
    assert "Pruned calls are not counted" in notes
    assert "1 prune ran" in notes


def test_the_window_includes_its_start_and_excludes_its_end() -> None:
    start = datetime(2026, 11, 1, tzinfo=UTC)
    end = datetime(2026, 12, 1, tzinfo=UTC)

    def call(at: datetime) -> ToolCallRecord:
        return ToolCallRecord(
            tool="analyze",
            tool_version="2.1.0",
            arguments={},
            status="ok",
            started_at=at,
            run_id="unknown-run",
        )

    report = build_usage_report(
        [call(start), call(end), call(start - timedelta(seconds=1))],
        [],
        {},
        start=start,
        end=end,
        generated_at=GENERATED,
    )

    assert report.header.calls == 1
    assert [row.principal for row in report.by_principal] == [RUN_NOT_FOUND]


@pytest.fixture
def path(tmp_path: Path) -> Path:
    runs = tmp_path / "runs.db"
    with SqliteRunStore(runs) as run_store:
        _populate(run_store)
    return runs


def _window() -> tuple[str, str]:
    now = datetime.now(UTC)
    return (now - timedelta(days=1)).strftime("%Y-%m-%d"), (now + timedelta(days=2)).strftime(
        "%Y-%m-%d"
    )


def test_the_report_command_writes_each_format(path: Path) -> None:
    start, end = _window()
    base = ["runs", "report", "--from", start, "--to", end, "--runs", str(path)]

    markdown = RUNNER.invoke(app, base)
    as_json = RUNNER.invoke(app, [*base, "--format", "json"])
    as_csv = RUNNER.invoke(app, [*base, "--format", "csv"])

    assert markdown.exit_code == as_json.exit_code == as_csv.exit_code == 0
    assert markdown.stdout.startswith("# AI use report")  # markdown is the default
    assert json.loads(as_json.stdout)["header"]["calls"] == 10
    assert len(list(csv.reader(io.StringIO(as_csv.stdout)))) == 8  # the header and seven rows
    for secret in (SECRET_ARGUMENT, SECRET_OUTPUT, SECRET_ERROR):
        assert secret not in markdown.stdout + as_json.stdout + as_csv.stdout


def test_the_report_command_refuses_bad_input(path: Path) -> None:
    start, end = _window()
    report = ["runs", "report", "--runs", str(path)]

    assert RUNNER.invoke(app, report).exit_code == 2  # --from and --to are required
    assert (
        RUNNER.invoke(app, [*report, "--from", start, "--to", end, "--format", "xml"]).exit_code
        == 2
    )
    assert RUNNER.invoke(app, [*report, "--from", end, "--to", start]).exit_code == 2
    missing = [
        "runs",
        "report",
        "--from",
        start,
        "--to",
        end,
        "--runs",
        str(path.with_suffix(".x")),
    ]
    assert RUNNER.invoke(app, missing).exit_code == 1
    assert "--from" in _plain(RUNNER.invoke(app, ["runs", "report", "--help"]).output)
    assert "report" in _plain(RUNNER.invoke(app, ["runs", "--help"]).output)
