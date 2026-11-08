"""The usage gate (decision 0015): a refusal happens before a call starts, never during it."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel

from quant_agent_os.runs import MemoryRunStore, RunRecorder, RunStore, SqliteRunStore
from quant_agent_os.runs.recorder import outcome
from quant_agent_os.runtime import (
    Approval,
    MemoryApprovalLedger,
    Refusal,
    ToolCall,
    ToolCallRecord,
    ToolExecutor,
    failure_details,
)
from quant_agent_os.runtime.executor import FAILURE_HINTS, canonical_sha256
from quant_agent_os.tools import Permission, PermissionPolicy, ToolRegistry, tool


class Value(BaseModel):
    value: int


class Text(BaseModel):
    text: str


RAN: list[int] = []


@tool(name="double", description="Double a number.")
def double(params: Value) -> Value:
    RAN.append(params.value)
    return Value(value=params.value * 2)


@tool(name="fetch", description="Needs the network.", permissions=[Permission.NETWORK])
def fetch(params: Value) -> Value:
    RAN.append(params.value)
    return params


@tool(name="text", description="Return some text.")
def text(params: Value) -> Text:
    return Text(text="x" * params.value)


class Gate:
    """Admits ``allowed`` calls, then refuses; records what it was asked."""

    def __init__(self, allowed: int) -> None:
        self.allowed = allowed
        self.asked: list[str] = []

    def admit(self, tool: str) -> Refusal | None:
        self.asked.append(tool)
        if len(self.asked) > self.allowed:
            return Refusal("Tenant acme is over its 1 calls a minute; retry in 42 s.", 42)
        return None


def test_a_refused_call_never_starts_and_keeps_its_approval() -> None:
    RAN.clear()
    ledger = MemoryApprovalLedger()
    digest = canonical_sha256({"value": 1})
    ledger.grant(Approval(run_id="run-1", tool="fetch", input_sha256=digest, approved_by="pm"))
    gate = Gate(allowed=0)
    executor = ToolExecutor(
        ToolRegistry([double, fetch]),
        PermissionPolicy(granted=frozenset({Permission.READ_LOCAL_DATA, Permission.NETWORK})),
        approvals=ledger,
        usage=gate,
    )

    refused = executor.execute(ToolCall(tool="fetch", arguments={"value": 1}), run_id="run-1")

    assert refused.status == "quota_exceeded" and refused.retry_after_seconds == 42
    assert RAN == [] and gate.asked == ["fetch"]
    assert ledger.consume("run-1", "fetch", digest) is not None  # still unused
    assert failure_details(refused, ["double"]) == {
        "status": "quota_exceeded",
        "error": "Tenant acme is over its 1 calls a minute; retry in 42 s.",
        "hint": FAILURE_HINTS["quota_exceeded"][0],
        "retryable": True,
        "retry_after_seconds": 42,
    }


def test_only_valid_permitted_calls_are_put_to_the_gate() -> None:
    RAN.clear()
    gate = Gate(allowed=1)
    executor = ToolExecutor(ToolRegistry([double, fetch]), PermissionPolicy(), usage=gate)

    statuses = [
        executor.execute(ToolCall(tool="nope", arguments={})).status,
        executor.execute(ToolCall(tool="fetch", arguments={"value": 1})).status,
        executor.execute(ToolCall(tool="double", arguments={"value": "x"})).status,
        executor.execute(ToolCall(tool="double", arguments={"value": 2})).status,
        executor.execute(ToolCall(tool="double", arguments={"value": 3})).status,
    ]

    assert statuses == ["unknown_tool", "denied", "invalid_input", "ok", "quota_exceeded"]
    assert gate.asked == ["double", "double"] and RAN == [2]


def test_a_plans_output_cap_lowers_every_tools_limit() -> None:
    executor = ToolExecutor(ToolRegistry([text]), PermissionPolicy(), max_output_bytes=50)

    capped = executor.execute(ToolCall(tool="text", arguments={"value": 100}))
    small = executor.execute(ToolCall(tool="text", arguments={"value": 10}))

    assert capped.status == "error" and "50-byte limit" in (capped.error or "")
    assert small.status == "ok"


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[RunStore]:
    run_store: RunStore = (
        MemoryRunStore() if request.param == "memory" else SqliteRunStore(tmp_path / "runs.db")
    )
    yield run_store
    run_store.close()


def test_stores_count_a_tenants_billable_calls_in_a_window(store: RunStore) -> None:
    executor = ToolExecutor(ToolRegistry([double]), PermissionPolicy())
    for tenant, value in [("acme", 1), ("acme", "bad"), ("acme", 2), ("beta", 3)]:
        RunRecorder(store, tenant=tenant).call(
            executor, ToolCall(tool="double", arguments={"value": value})
        )
    start = datetime.now(UTC) - timedelta(days=1)
    end = start + timedelta(days=2)

    assert store.count_billable("acme", start, end) == 2  # invalid input is not billable
    assert store.count_billable("beta", start, end) == 1
    assert store.count_billable("acme", end, end + timedelta(days=31)) == 0


def test_a_quota_refusal_leaves_a_waiting_call_waiting_and_fails_any_other() -> None:
    waiting = ToolCallRecord(
        tool="fetch",
        tool_version="1.0.0",
        arguments={},
        status="approval_required",
        input_sha256="h",
    )
    refused = waiting.model_copy(update={"status": "quota_exceeded"})
    done = waiting.model_copy(update={"status": "ok"})

    assert outcome([waiting, refused]) == "awaiting_approval"
    assert outcome([done, refused]) == "failed"
    assert outcome([refused]) == "failed"
