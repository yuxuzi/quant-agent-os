"""Metering (B-36 P7.1, decision 0015): one usage event per recorded call, written with it."""

import sqlite3
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel

from quant_agent_os.runs import (
    BILLABLE,
    MemoryRunStore,
    RunRecorder,
    RunStore,
    RunStoreError,
    SqliteRunStore,
    UsageEvent,
    summarize,
)
from quant_agent_os.runs.store import SCHEMA_VERSION
from quant_agent_os.runtime import ToolCall, ToolExecutor
from quant_agent_os.tools import PermissionPolicy, ToolRegistry, tool


class Query(BaseModel):
    query: str
    api_key: str | None = None


class Answer(BaseModel):
    answer: str


@tool(name="lookup", description="Answer a query.")
def lookup(params: Query) -> Answer:
    return Answer(answer=params.query.upper())


@tool(name="broken", description="Always fails.")
def broken(params: Query) -> Answer:
    raise RuntimeError("boom")


@tool(name="slow", description="Takes too long.", timeout_seconds=0.01)
def slow(params: Query) -> Answer:
    time.sleep(0.2)
    return Answer(answer="late")


EXECUTOR = ToolExecutor(ToolRegistry([lookup, broken, slow]), PermissionPolicy())
EVER = (datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC))


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[RunStore]:
    run_store: RunStore = (
        MemoryRunStore() if request.param == "memory" else SqliteRunStore(tmp_path / "runs.db")
    )
    yield run_store
    run_store.close()


def _call(recorder: RunRecorder, tool_name: str, query: str = "q") -> None:
    recorder.call(EXECUTOR, ToolCall(tool=tool_name, arguments={"query": query}))


def test_a_recorded_call_has_one_usage_event(store: RunStore) -> None:
    recorder = RunRecorder(
        store, actor="rest", principal="alice", tenant="acme", toolsets={"lookup": "research"}
    )
    record = recorder.call(EXECUTOR, ToolCall(tool="lookup", arguments={"query": "abc"}))

    [usage] = store.list_usage(*EVER)

    assert (usage.call_id, usage.run_id) == (record.call_id, record.run_id)
    assert (usage.tenant, usage.principal, usage.interface) == ("acme", "alice", "rest")
    assert (usage.tool, usage.tool_version, usage.toolset) == ("lookup", "1.0.0", "research")
    assert (usage.status, usage.billable) == ("ok", True)
    assert usage.duration_ms == record.duration_ms
    assert usage.input_bytes == len('{"query":"abc"}')
    assert usage.output_bytes == len('{"answer":"ABC"}')
    assert usage.at == record.started_at


@pytest.mark.parametrize(
    ("tool_name", "arguments", "status", "billable"),
    [
        ("broken", {"query": "q"}, "error", True),
        ("slow", {"query": "q"}, "timeout", True),
        ("lookup", {}, "invalid_input", False),
        ("missing", {"query": "q"}, "unknown_tool", False),
    ],
)
def test_calls_that_used_the_server_are_billable_and_refusals_are_not(
    store: RunStore, tool_name: str, arguments: dict[str, str], status: str, billable: bool
) -> None:
    RunRecorder(store).call(EXECUTOR, ToolCall(tool=tool_name, arguments=arguments))

    [usage] = store.list_usage(*EVER)

    assert (usage.status, usage.billable) == (status, billable)
    assert (usage.status in BILLABLE) is billable


def test_usage_holds_sizes_never_arguments_or_outputs(tmp_path: Path) -> None:
    with SqliteRunStore(tmp_path / "runs.db") as store:
        _call(RunRecorder(store), "lookup", "confidential thesis")
        [usage] = store.list_usage(*EVER)

    assert "confidential" not in usage.model_dump_json().lower()
    with sqlite3.connect(tmp_path / "runs.db") as db:
        rows = db.execute("SELECT record FROM usage_events").fetchall()
    db.close()
    assert "confidential" not in rows[0][0].lower()


def test_usage_is_sized_before_redaction(store: RunStore) -> None:
    secret = "sk-live-0123456789"
    RunRecorder(store).call(
        EXECUTOR, ToolCall(tool="lookup", arguments={"query": "q", "api_key": secret})
    )

    [usage] = store.list_usage(*EVER)

    assert usage.input_bytes == len(f'{{"api_key":"{secret}","query":"q"}}')


def test_usage_is_read_by_tenant_and_half_open_window(store: RunStore) -> None:
    _call(RunRecorder(store), "lookup")
    _call(RunRecorder(store, tenant="acme"), "lookup")
    [first, second] = store.list_usage(*EVER)

    assert [event.tenant for event in store.list_usage(*EVER, tenant="acme")] == ["acme"]
    assert store.list_usage(first.at, second.at) == [first]
    assert store.list_usage(second.at, second.at + timedelta(microseconds=1)) == [second]
    assert store.list_usage(*EVER, tenant="other") == []


def test_a_call_and_its_usage_are_written_in_one_transaction(tmp_path: Path) -> None:
    with SqliteRunStore(tmp_path / "runs.db") as store:
        record = RunRecorder(store).call(
            EXECUTOR, ToolCall(tool="lookup", arguments={"query": "q"})
        )
        [usage] = store.list_usage(*EVER)
        again = record.model_copy(update={"call_id": "another"})

        with pytest.raises(RunStoreError):
            store.add_call(again, usage)  # the usage event's call id is taken

        stored = store.get_run(record.run_id or "")
        assert stored is not None
        assert [call.call_id for call in stored.calls] == [record.call_id]


def test_a_version_4_run_store_gains_usage_events(tmp_path: Path) -> None:
    path = tmp_path / "runs.db"
    SqliteRunStore(path).close()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE usage_events")
        db.execute("PRAGMA user_version = 4")
    db.close()

    with SqliteRunStore(path) as store:
        _call(RunRecorder(store), "lookup")
        assert len(store.list_usage(*EVER)) == 1
    with sqlite3.connect(path) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
    db.close()
    assert version == SCHEMA_VERSION == 5


def test_a_usage_event_round_trips_as_json() -> None:
    event = UsageEvent(
        call_id="c",
        run_id="r",
        interface="sdk",
        tool="lookup",
        status="ok",
        billable=True,
        duration_ms=1.5,
        input_bytes=2,
        output_bytes=3,
        at=datetime(2026, 11, 3, tzinfo=UTC),
    )

    assert UsageEvent.model_validate_json(event.model_dump_json()) == event


# Usage totals (the export's --by)


def _event(
    call_id: str, tenant: str, tool: str, at: datetime, *, billable: bool = True
) -> UsageEvent:
    return UsageEvent(
        call_id=call_id,
        run_id="r",
        tenant=tenant,
        interface="rest",
        tool=tool,
        toolset="research",
        status="ok" if billable else "denied",
        billable=billable,
        duration_ms=1.25,
        input_bytes=10,
        output_bytes=100,
        at=at,
    )


def test_usage_is_totalled_per_tenant_by_utc_day_and_by_tool() -> None:
    late = datetime(2026, 11, 1, 23, 30, tzinfo=UTC)
    events = [
        _event("a", "acme", "search", late),
        _event("b", "acme", "search", late + timedelta(hours=1)),  # 2 November, UTC
        _event("c", "acme", "report", late, billable=False),
        _event("d", "beta", "search", late),
    ]

    by_day = [(t.tenant, t.group, t.calls, t.billable_calls) for t in summarize(events, "day")]
    by_tool = summarize(events, "tool")

    assert by_day == [
        ("acme", "2026-11-01", 2, 1),
        ("acme", "2026-11-02", 1, 1),
        ("beta", "2026-11-01", 1, 1),
    ]
    assert [(t.tenant, t.group, t.toolset, t.calls) for t in by_tool] == [
        ("acme", "report", "research", 1),
        ("acme", "search", "research", 2),
        ("beta", "search", "research", 1),
    ]
    acme_search = by_tool[1]
    assert (acme_search.duration_ms, acme_search.input_bytes, acme_search.output_bytes) == (
        2.5,
        20,
        200,
    )
    assert summarize([], "day") == []
