"""Retention and export: `qaos runs prune` and `qaos runs export` (S5.2, S5.4)."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from quant_agent_os.runs import (
    AuditEvent,
    MemoryRunStore,
    RunRecorder,
    RunStore,
    SqliteRunStore,
    audit_lines,
)
from quant_agent_os.runtime import ToolCall, ToolCallRecord, ToolExecutor
from quant_agent_os.tools import PermissionPolicy, ToolRegistry, tool
from quant_platform.cli import app

RUNNER = CliRunner()
PASSAGE = "a retrieved passage only this test ever writes"
SOON = timedelta(seconds=5)


class Query(BaseModel):
    query: str


class Answer(BaseModel):
    answer: str
    data_versions: dict[str, str] = {}


@tool(name="lookup", version="1.2.3", description="Answer a query from a fixed snapshot.")
def lookup(params: Query) -> Answer:
    return Answer(answer=PASSAGE, data_versions={"corpus_snapshot": "snap:1"})


EXECUTOR = ToolExecutor(ToolRegistry([lookup]), PermissionPolicy())


def _record(store: RunStore, principal: str = "alice") -> ToolCallRecord:
    recorder = RunRecorder(store, actor="rest", principal=principal)
    return recorder.call(EXECUTOR, ToolCall(tool="lookup", arguments={"query": "q"}))


def _event() -> AuditEvent:
    return AuditEvent(run_id="", actor="cli", event="runs.pruned", subject="test")


def _run_id(record: ToolCallRecord) -> str:
    assert record.run_id is not None
    return record.run_id


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[RunStore]:
    run_store: RunStore = (
        MemoryRunStore() if request.param == "memory" else SqliteRunStore(tmp_path / "runs.db")
    )
    yield run_store
    run_store.close()


def test_prune_removes_old_ended_runs_and_their_calls(store: RunStore) -> None:
    record = _record(store)
    now = datetime.now(UTC)

    assert store.prunable_runs(now - SOON) == 0
    assert store.prunable_runs(now + SOON) == 1
    pruned = store.prune_runs(now + SOON, _event())

    assert (pruned.runs, pruned.calls) == (1, 1)
    assert store.get_run(_run_id(record)) is None
    assert store.list_calls(now - SOON, now + SOON) == []


def test_prune_spares_newer_runs_and_runs_still_in_progress(store: RunStore) -> None:
    newer = _record(store)
    stored = store.get_run(_run_id(_record(store)))
    assert stored is not None
    waiting = stored.run.model_copy(update={"status": "awaiting_approval"})
    store.save_run(waiting)

    assert store.prune_runs(datetime.now(UTC) - SOON, _event()).runs == 0
    store.prune_runs(datetime.now(UTC) + SOON, _event())

    assert store.get_run(_run_id(newer)) is None
    assert store.get_run(waiting.run_id) is not None


def test_prune_keeps_the_audit_trail_and_usage_and_records_itself(store: RunStore) -> None:
    now = datetime.now(UTC)
    record = _record(store)
    before = store.list_events(now - SOON, now + SOON)

    store.prune_runs(now + SOON, _event())

    after = store.list_events(now - SOON, now + SOON)
    assert after[: len(before)] == before
    assert any(event.run_id == record.run_id for event in after)
    assert [event.event for event in after[len(before) :]] == ["runs.pruned"]


def test_pruned_text_is_gone_from_the_files(tmp_path: Path) -> None:
    run_store = SqliteRunStore(tmp_path / "runs.db")
    _record(run_store)
    assert PASSAGE.encode() in b"".join(file.read_bytes() for file in tmp_path.iterdir())

    pruned = run_store.prune_runs(datetime.now(UTC) + SOON, _event())

    assert pruned.settled
    assert PASSAGE.encode() not in b"".join(file.read_bytes() for file in tmp_path.iterdir())
    run_store.close()


def test_export_round_trips_every_agreed_audit_field(store: RunStore) -> None:
    now = datetime.now(UTC)
    record = _record(store)
    stored = store.get_run(_run_id(record))
    assert stored is not None

    lines = list(audit_lines(store, now - SOON, now + SOON))
    call = next(line for line in lines if line["type"] == "call")

    assert call["principal"] == "alice"
    assert call["actor"] == "rest"
    assert call["tool"] == "lookup"
    assert call["tool_version"] == "1.2.3"
    assert call["input_sha256"] == record.input_sha256
    assert call["data_versions"] == {"corpus_snapshot": "snap:1"}
    assert call["status"] == "ok"
    assert datetime.fromisoformat(call["at"]) == record.started_at
    assert call["platform_version"] == stored.run.platform_version
    assert [line["event"] for line in lines if line["type"] == "event"] == [
        event.event for event in stored.events
    ]
    assert [line["at"] for line in lines] == sorted(line["at"] for line in lines)


def test_export_holds_no_content_and_honours_the_window(store: RunStore) -> None:
    now = datetime.now(UTC)
    _record(store)

    exported = json.dumps(list(audit_lines(store, now - SOON, now + SOON)))

    assert PASSAGE not in exported
    assert '"q"' not in exported
    assert list(audit_lines(store, now + SOON, now + 2 * SOON)) == []


def test_export_still_shows_the_audit_events_of_a_pruned_run(store: RunStore) -> None:
    now = datetime.now(UTC)
    record = _record(store)
    store.prune_runs(now + SOON, _event())

    lines = list(audit_lines(store, now - SOON, now + SOON))

    assert not [line for line in lines if line["type"] == "call"]
    assert record.run_id in {line["run_id"] for line in lines}
    assert "runs.pruned" in {line.get("event") for line in lines}


@pytest.fixture
def path(tmp_path: Path) -> Path:
    runs = tmp_path / "runs.db"
    with SqliteRunStore(runs) as run_store:
        _record(run_store)
    return runs


def test_the_runs_command_still_lists_and_shows_runs(path: Path) -> None:
    listed = RUNNER.invoke(app, ["runs", "--runs", str(path)], env={"COLUMNS": "200"})
    run_id = listed.output.split()[0]

    shown = RUNNER.invoke(app, ["runs", run_id, "--runs", str(path)], env={"COLUMNS": "200"})
    named = RUNNER.invoke(app, ["runs", "show", run_id, "--runs", str(path)])

    assert listed.exit_code == shown.exit_code == named.exit_code == 0
    assert "tool.called" in shown.output
    assert RUNNER.invoke(app, ["runs", "unknown", "--runs", str(path)]).exit_code == 1
    assert "prune" in RUNNER.invoke(app, ["runs", "--help"]).output


def test_the_export_command_writes_json_lines(path: Path) -> None:
    done = RUNNER.invoke(app, ["runs", "export", "--runs", str(path)])

    assert done.exit_code == 0
    lines = [json.loads(line) for line in done.stdout.splitlines()]
    assert {line["type"] for line in lines} == {"call", "event"}

    assert RUNNER.invoke(app, ["runs", "export", "--format", "csv"]).exit_code == 2
    assert (
        RUNNER.invoke(app, ["runs", "export", "--runs", str(path.with_suffix(".x"))]).exit_code == 1
    )


def test_the_prune_command_asks_first_and_takes_days(path: Path) -> None:
    def remaining() -> int:
        with SqliteRunStore(path) as run_store:
            return len(run_store.list_runs())

    def backdate() -> None:
        with SqliteRunStore(path) as run_store:
            run = run_store.list_runs()[0]
            old = run.started_at - timedelta(days=100)
            run_store.save_run(run.model_copy(update={"started_at": old}))

    prune = ["runs", "prune", "--runs", str(path)]
    assert RUNNER.invoke(app, prune).exit_code == 2  # --older-than is required
    assert RUNNER.invoke(app, [*prune, "--older-than", "90"]).exit_code == 2
    assert RUNNER.invoke(app, [*prune, "--older-than", "0d"]).exit_code == 2

    assert "No ended runs" in RUNNER.invoke(app, [*prune, "--older-than", "90d"]).output
    assert remaining() == 1

    backdate()
    declined = RUNNER.invoke(app, [*prune, "--older-than", "90d"], input="n\n")
    assert declined.exit_code == 1
    assert remaining() == 1

    done = RUNNER.invoke(app, [*prune, "--older-than", "90d", "--yes"])
    assert done.exit_code == 0
    assert "Removed 1 runs and 1 calls" in done.output
    assert remaining() == 0
