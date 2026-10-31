"""Run persistence (B-08) and the SDK facade (B-07): runs, calls, audit, and redaction."""

import json
import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel, SecretStr

from quant_agent_os.mcp import call_tool
from quant_agent_os.providers import FakeModelProvider
from quant_agent_os.runs import (
    MemoryRunStore,
    RunRecorder,
    RunStore,
    RunStoreError,
    SqliteRunStore,
    redact,
)
from quant_agent_os.runtime import PlanStep, ToolExecutor
from quant_agent_os.runtime.orchestrator import Orchestrator, PlanningError
from quant_agent_os.sdk import Platform, ProviderRequiredError
from quant_agent_os.tools import PermissionPolicy, ToolRegistry, tool


class Query(BaseModel):
    query: str
    api_key: str | None = None


class Answer(BaseModel):
    answer: str
    data_versions: dict[str, str] = {}


@tool(name="lookup", description="Answer a query from a fixed snapshot.")
def lookup(params: Query) -> Answer:
    return Answer(answer=params.query.upper(), data_versions={"corpus_snapshot": "snap:1"})


@tool(name="broken", description="Always fails.")
def broken(params: Query) -> Answer:
    raise RuntimeError("boom")


REGISTRY = ToolRegistry([lookup, broken])
EXECUTOR = ToolExecutor(REGISTRY, PermissionPolicy())
SECRET = "sk-live-0123456789"


def _plan(*steps: tuple[str, str]) -> str:
    return json.dumps(
        {
            "goal": "g",
            "steps": [{"tool": name, "arguments": {"query": query}} for name, query in steps],
        }
    )


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[RunStore]:
    run_store: RunStore = (
        MemoryRunStore() if request.param == "memory" else SqliteRunStore(tmp_path / "runs.db")
    )
    yield run_store
    run_store.close()


# Run stores and the recorder


def test_a_call_is_recorded_as_a_run_with_its_call_and_audit_trail(store: RunStore) -> None:
    record = RunRecorder(store, actor="mcp").call(
        EXECUTOR, PlanStep(tool="lookup", arguments={"query": "reversal"})
    )

    stored = store.get_run(record.run_id or "")

    assert stored is not None
    assert stored.run.kind == "call"
    assert stored.run.status == "completed"
    assert stored.run.actor == "mcp"
    assert stored.run.goal == "lookup"
    assert stored.run.registry_digest == REGISTRY.digest()
    assert stored.run.data_versions == {"corpus_snapshot": "snap:1"}
    assert stored.run.finished_at is not None
    assert [call.call_id for call in stored.calls] == [record.call_id]
    assert stored.calls[0].input_sha256 == record.input_sha256
    assert stored.calls[0].output == record.output
    assert [event.event for event in stored.events] == [
        "run.started",
        "tool.called",
        "run.finished",
    ]


def test_a_failed_call_is_a_failed_run(store: RunStore) -> None:
    record = RunRecorder(store).call(EXECUTOR, PlanStep(tool="broken", arguments={"query": "x"}))

    stored = store.get_run(record.run_id or "")

    assert stored is not None
    assert (stored.run.status, record.status) == ("failed", "error")
    assert stored.run.error == "RuntimeError: boom"


def test_runs_are_listed_newest_first_and_unknown_runs_are_none(store: RunStore) -> None:
    recorder = RunRecorder(store)
    first = recorder.call(EXECUTOR, PlanStep(tool="lookup", arguments={"query": "a"}))
    second = recorder.call(EXECUTOR, PlanStep(tool="lookup", arguments={"query": "b"}))

    assert [run.run_id for run in store.list_runs()] == [second.run_id, first.run_id]
    assert len(store.list_runs(limit=1)) == 1
    assert store.get_run("missing") is None


def test_audit_events_hold_ids_and_statuses_never_inputs(store: RunStore) -> None:
    record = RunRecorder(store).call(
        EXECUTOR, PlanStep(tool="lookup", arguments={"query": "confidential thesis"})
    )

    stored = store.get_run(record.run_id or "")

    assert stored is not None
    assert "confidential" not in json.dumps([event.details for event in stored.events])


def test_secrets_never_reach_the_run_store_file(tmp_path: Path) -> None:
    path = tmp_path / "runs.db"
    run_store = SqliteRunStore(path)
    RunRecorder(run_store).call(
        EXECUTOR, PlanStep(tool="lookup", arguments={"query": "q", "api_key": SECRET})
    )
    run_store.close()

    raw = b"".join(file.read_bytes() for file in tmp_path.iterdir())

    assert SECRET.encode() not in raw


def test_redaction_covers_secret_types_and_secret_named_keys() -> None:
    value = {
        "query": "q",
        "nested": [{"Authorization": "Bearer x", "token_count": 3}],
        "key": SecretStr("hidden"),
        "password": "p",
    }

    assert redact(value) == {
        "query": "q",
        "nested": [{"Authorization": "[REDACTED]", "token_count": "[REDACTED]"}],
        "key": "[REDACTED]",
        "password": "[REDACTED]",
    }


def test_sqlite_runs_survive_reopening(tmp_path: Path) -> None:
    path = tmp_path / "runs.db"
    first = SqliteRunStore(path)
    record = RunRecorder(first).call(EXECUTOR, PlanStep(tool="lookup", arguments={"query": "q"}))
    first.close()

    reopened = SqliteRunStore(path)
    stored = reopened.get_run(record.run_id or "")
    reopened.close()

    assert stored is not None
    assert stored.calls[0].call_id == record.call_id


@pytest.mark.parametrize(
    "statement", ["UPDATE audit_events SET event = 'x'", "DELETE FROM audit_events"]
)
def test_the_audit_trail_is_append_only(tmp_path: Path, statement: str) -> None:
    path = tmp_path / "runs.db"
    run_store = SqliteRunStore(path)
    RunRecorder(run_store).call(EXECUTOR, PlanStep(tool="lookup", arguments={"query": "q"}))
    run_store.close()

    with sqlite3.connect(path) as db, pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.execute(statement)
    db.close()


def test_a_newer_run_store_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "runs.db"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version = 99")
    db.close()

    with pytest.raises(RunStoreError, match="schema 99"):
        SqliteRunStore(path)


class _FailingStore(MemoryRunStore):
    def add_call(self, record: object) -> None:
        raise RunStoreError("disk full")


def test_a_store_failure_is_logged_and_never_fails_the_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="quant_agent_os.runs"):
        record = RunRecorder(_FailingStore()).call(
            EXECUTOR, PlanStep(tool="lookup", arguments={"query": "q"})
        )

    assert record.status == "ok"
    assert "disk full" in caplog.text


# The orchestrator and MCP


def test_an_orchestrated_run_records_every_call() -> None:
    run_store = MemoryRunStore()
    orchestrator = Orchestrator(
        REGISTRY,
        FakeModelProvider([_plan(("lookup", "a"), ("lookup", "b"))]),
        recorder=RunRecorder(run_store),
    )

    report = orchestrator.run("Look up two things")
    stored = run_store.get_run(report.metadata.run_id)

    assert stored is not None
    assert (stored.run.kind, stored.run.status, stored.run.provider) == (
        "plan",
        "completed",
        "fake",
    )
    assert stored.run.goal == "Look up two things"
    assert [call.call_id for call in stored.calls] == [c.call_id for c in report.tool_calls]
    assert stored.run.data_versions == report.metadata.data_versions


def test_a_rejected_plan_is_recorded_as_a_failed_run() -> None:
    run_store = MemoryRunStore()
    orchestrator = Orchestrator(
        REGISTRY, FakeModelProvider(["not json"]), recorder=RunRecorder(run_store)
    )

    with pytest.raises(PlanningError):
        orchestrator.run("g")

    (run,) = run_store.list_runs()
    assert run.status == "failed"
    assert run.error is not None and "invalid plan" in run.error


def test_mcp_calls_are_recorded_when_a_recorder_is_given() -> None:
    run_store = MemoryRunStore()

    result = call_tool(EXECUTOR, "lookup", {"query": "q"}, recorder=RunRecorder(run_store))

    (run,) = run_store.list_runs()
    assert result["isError"] is False
    assert run.goal == "lookup"


# The SDK facade


def test_platform_call_accepts_a_mapping_and_keywords() -> None:
    with Platform([lookup]) as platform:
        record = platform.call("lookup", {"query": "a"}, query="b")

        assert record.output == {"answer": "B", "data_versions": {"corpus_snapshot": "snap:1"}}
        stored = platform.get_run(record.run_id or "")
        assert stored is not None and stored.run.actor == "sdk"
        assert [run.run_id for run in platform.runs()] == [record.run_id]


def test_platform_and_mcp_return_the_same_output() -> None:
    with Platform(REGISTRY) as platform:
        sdk = platform.call("lookup", query="same")
    mcp = call_tool(EXECUTOR, "lookup", {"query": "same"})

    assert mcp["structuredContent"] == sdk.output


def test_platform_run_needs_a_provider_and_records_the_plan() -> None:
    with Platform(REGISTRY) as platform, pytest.raises(ProviderRequiredError):
        platform.run("g")

    with Platform(REGISTRY, provider=FakeModelProvider([_plan(("lookup", "a"))])) as platform:
        report = platform.run("g")
        stored = platform.get_run(report.metadata.run_id)

    assert stored is not None and stored.run.kind == "plan"
    assert len(stored.calls) == 1


def test_a_version_1_run_store_is_upgraded_in_place(tmp_path: Path) -> None:
    path = tmp_path / "runs.db"
    SqliteRunStore(path).close()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE approvals")
        db.execute("PRAGMA user_version = 1")
    db.close()

    upgraded = SqliteRunStore(path)
    upgraded.close()

    with sqlite3.connect(path) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master")}
    db.close()
    assert version == 2
    assert "approvals" in tables
