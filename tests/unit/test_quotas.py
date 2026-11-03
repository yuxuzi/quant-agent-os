"""Plan limits (B-36 P7.2, decision 0015): checked before a call starts, never during it."""

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
    PlanLimits,
    PlanStep,
    Refusal,
    TokenBucket,
    ToolCallRecord,
    ToolExecutor,
    failure_details,
)
from quant_agent_os.runtime.executor import FAILURE_HINTS, canonical_sha256
from quant_agent_os.runtime.quotas import month_window
from quant_agent_os.tools import Permission, PermissionPolicy, ToolRegistry, tool

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


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


class Clock:
    def __init__(self) -> None:
        self.at = 0.0

    def __call__(self) -> float:
        return self.at


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


def test_a_token_bucket_allows_bursts_up_to_its_rate_then_refills() -> None:
    clock = Clock()
    bucket = TokenBucket(2, clock=clock)

    assert [bucket.take(), bucket.take()] == [0.0, 0.0]
    assert bucket.take() == pytest.approx(30.0)
    clock.at = 30.0
    assert bucket.take() == 0.0
    with pytest.raises(ValueError, match="at least one call"):
        TokenBucket(0)


def test_a_month_window_is_a_half_open_utc_calendar_month() -> None:
    assert month_window(NOW) == (
        datetime(2026, 11, 1, tzinfo=UTC),
        datetime(2026, 12, 1, tzinfo=UTC),
    )
    assert month_window(datetime(2026, 12, 31, 23, 59, tzinfo=UTC))[1] == datetime(
        2027, 1, 1, tzinfo=UTC
    )


def test_the_monthly_quota_counts_billable_calls_and_says_when_it_resets() -> None:
    counted: list[tuple[datetime, datetime]] = []

    def count(start: datetime, end: datetime) -> int:
        counted.append((start, end))
        return 2

    limits = PlanLimits("acme", monthly_calls=3, count_billable=count, now=lambda: NOW)

    assert limits.admit("double") is None
    refusal = limits.admit("double")

    assert refusal is not None
    assert refusal.message == (
        "Tenant acme used its 3 calls for November 2026; the quota resets 2026-12-01T00:00Z."
    )
    assert refusal.retry_after_seconds == int(
        (datetime(2026, 12, 1, tzinfo=UTC) - NOW).total_seconds()
    )
    assert counted == [month_window(NOW)]  # read once, then counted locally


def test_the_monthly_count_is_reread_after_the_refresh_and_in_a_new_month() -> None:
    clock, now, used = Clock(), [NOW], [5]
    limits = PlanLimits(
        "acme",
        monthly_calls=5,
        count_billable=lambda start, end: used[0],
        refresh_seconds=30,
        now=lambda: now[0],
        clock=clock,
    )

    assert limits.admit("double") is not None
    used[0] = 4  # a refused or non-billable call left the shared count lower
    assert limits.admit("double") is not None  # still the cached count
    clock.at = 30.0
    assert limits.admit("double") is None
    now[0], used[0] = datetime(2026, 12, 1, tzinfo=UTC), 0
    assert limits.admit("double") is None


def test_a_rate_refusal_does_not_use_the_monthly_quota() -> None:
    clock = Clock()
    limits = PlanLimits(
        "acme",
        calls_per_minute=1,
        monthly_calls=2,
        count_billable=lambda start, end: 0,
        now=lambda: NOW,
        clock=clock,
    )

    assert limits.admit("double") is None
    refused = limits.admit("double")
    clock.at = 60.0
    admitted = limits.admit("double")

    assert refused == Refusal("Tenant acme is over its 1 calls a minute; retry in 60 s.", 60)
    assert admitted is None  # the refused call did not count toward the 2 a month
    with pytest.raises(ValueError, match="counter"):
        PlanLimits("acme", monthly_calls=1)


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

    refused = executor.execute(PlanStep(tool="fetch", arguments={"value": 1}), run_id="run-1")

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
        executor.execute(PlanStep(tool="nope", arguments={})).status,
        executor.execute(PlanStep(tool="fetch", arguments={"value": 1})).status,
        executor.execute(PlanStep(tool="double", arguments={"value": "x"})).status,
        executor.execute(PlanStep(tool="double", arguments={"value": 2})).status,
        executor.execute(PlanStep(tool="double", arguments={"value": 3})).status,
    ]

    assert statuses == ["unknown_tool", "denied", "invalid_input", "ok", "quota_exceeded"]
    assert gate.asked == ["double", "double"] and RAN == [2]


def test_a_plans_output_cap_lowers_every_tools_limit() -> None:
    executor = ToolExecutor(ToolRegistry([text]), PermissionPolicy(), max_output_bytes=50)

    capped = executor.execute(PlanStep(tool="text", arguments={"value": 100}))
    small = executor.execute(PlanStep(tool="text", arguments={"value": 10}))

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
            executor, PlanStep(tool="double", arguments={"value": value})
        )
    start, end = month_window(datetime.now(UTC))

    assert store.count_billable("acme", start, end) == 2  # invalid input is not billable
    assert store.count_billable("beta", start, end) == 1
    assert store.count_billable("acme", end, end + timedelta(days=31)) == 0


def test_a_quota_refusal_leaves_a_waiting_call_waiting_and_fails_any_other() -> None:
    waiting = ToolCallRecord(
        tool="fetch", tool_version="1", arguments={}, status="approval_required", input_sha256="h"
    )
    refused = waiting.model_copy(update={"status": "quota_exceeded"})
    done = waiting.model_copy(update={"status": "ok"})

    assert outcome([waiting, refused]) == "awaiting_approval"
    assert outcome([done, refused]) == "failed"
    assert outcome([refused]) == "failed"
