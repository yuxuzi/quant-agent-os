"""The approval gate (decision 0005, B-09): consequential calls need a single-use approval."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel

from quant_agent_os.config import Settings
from quant_agent_os.mcp import call_tool
from quant_agent_os.runs import (
    MemoryRunStore,
    NotAwaitingApprovalError,
    RunNotFoundError,
    RunStore,
    SqliteRunStore,
)
from quant_agent_os.runtime import Approval, MemoryApprovalLedger, PlanStep, ToolExecutor
from quant_agent_os.sdk import Platform
from quant_agent_os.tools import (
    CONSEQUENTIAL,
    READ_ONLY,
    Permission,
    PermissionPolicy,
    ToolRegistry,
    tool,
)

WRITES: list[str] = []


class Note(BaseModel):
    text: str


class Written(BaseModel):
    written: str


@tool(
    name="write_note",
    description="Append a note to a file.",
    permissions=[Permission.WRITE_LOCAL_DATA],
    idempotent=False,
)
def write_note(params: Note) -> Written:
    WRITES.append(params.text)
    return Written(written=params.text)


@tool(name="read_note", description="Echo a note.")
def read_note(params: Note) -> Written:
    return Written(written=params.text)


GRANTED = PermissionPolicy(granted=READ_ONLY | {Permission.WRITE_LOCAL_DATA})


@pytest.fixture(autouse=True)
def _reset_writes() -> None:
    WRITES.clear()


@pytest.fixture(params=["memory", "sqlite"])
def platform(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Platform]:
    store: RunStore = (
        MemoryRunStore() if request.param == "memory" else SqliteRunStore(tmp_path / "runs.db")
    )
    with Platform([write_note, read_note], policy=GRANTED, run_store=store) as instance:
        yield instance


def test_the_defaults_stay_read_only_and_consequential_permissions_are_listed() -> None:
    assert {Permission.READ_LOCAL_DATA} == READ_ONLY
    assert Permission.READ_LOCAL_DATA not in CONSEQUENTIAL
    assert PermissionPolicy().preapproved == frozenset()
    assert Settings().policy() == PermissionPolicy()


def test_an_ungranted_permission_is_still_denied_before_any_approval() -> None:
    with Platform([write_note]) as platform:
        record = platform.call("write_note", text="x")

    assert record.status == "denied"
    assert WRITES == []


def test_a_consequential_call_waits_for_an_approval_without_running(platform: Platform) -> None:
    record = platform.call("write_note", text="hello")

    stored = platform.get_run(record.run_id or "")
    assert record.status == "approval_required"
    assert record.input_sha256 and record.input_sha256[:16] in (record.error or "")
    assert WRITES == []
    assert stored is not None
    assert (stored.run.status, stored.run.finished_at) == ("awaiting_approval", None)
    assert stored.events[-1].event == "approval.required"


def test_approve_then_resume_runs_the_exact_call_once(platform: Platform) -> None:
    waiting = platform.call("write_note", text="hello")
    run_id = waiting.run_id or ""

    (approval,) = platform.approve(run_id, approved_by="alice")
    (resumed,) = platform.resume(run_id)

    stored = platform.get_run(run_id)
    assert (approval.run_id, approval.tool, approval.input_sha256) == (
        run_id,
        "write_note",
        waiting.input_sha256,
    )
    assert resumed.status == "ok"
    assert resumed.approval == approval.approval_id
    assert WRITES == ["hello"]
    assert stored is not None
    assert stored.run.status == "completed"
    assert [event.event for event in stored.events] == [
        "run.started",
        "tool.called",
        "approval.required",
        "approval.granted",
        "tool.called",
        "run.finished",
    ]
    assert stored.events[3].details["approved_by"] == "alice"
    with pytest.raises(NotAwaitingApprovalError):
        platform.resume(run_id)


def test_resuming_without_an_approval_changes_nothing(platform: Platform) -> None:
    waiting = platform.call("write_note", text="hello")

    (again,) = platform.resume(waiting.run_id or "")

    assert again.status == "approval_required"
    assert WRITES == []


def test_an_approval_is_bound_to_its_run_tool_and_input() -> None:
    ledger = MemoryApprovalLedger()
    executor = ToolExecutor(ToolRegistry([write_note]), GRANTED, approvals=ledger)
    step = PlanStep(tool="write_note", arguments={"text": "approved"})
    waiting = executor.execute(step, run_id="run-1")
    ledger.grant(
        Approval(
            run_id="run-1",
            tool="write_note",
            input_sha256=waiting.input_sha256 or "",
            approved_by="op",
        )
    )

    other_input = executor.execute(
        PlanStep(tool="write_note", arguments={"text": "changed"}), run_id="run-1"
    )
    other_run = executor.execute(step, run_id="run-2")
    no_run = executor.execute(step)
    first = executor.execute(step, run_id="run-1")
    replay = executor.execute(step, run_id="run-1")

    assert [r.status for r in (other_input, other_run, no_run)] == ["approval_required"] * 3
    assert (first.status, replay.status) == ("ok", "approval_required")
    assert WRITES == ["approved"]


def test_a_standing_approval_runs_without_a_per_call_approval_and_is_recorded() -> None:
    policy = PermissionPolicy(
        granted=READ_ONLY | {Permission.WRITE_LOCAL_DATA},
        preapproved=frozenset({Permission.WRITE_LOCAL_DATA}),
    )
    with Platform([write_note], policy=policy) as platform:
        record = platform.call("write_note", text="x")
        stored = platform.get_run(record.run_id or "")

    assert (record.status, record.approval) == ("ok", "standing:write_local_data")
    assert stored is not None
    assert stored.events[1].details["approval"] == "standing:write_local_data"


def test_read_only_tools_need_no_approval(platform: Platform) -> None:
    record = platform.call("read_note", text="x")

    assert (record.status, record.approval) == ("ok", None)


def test_approving_an_unknown_or_finished_run_is_refused(platform: Platform) -> None:
    done = platform.call("read_note", text="x")

    with pytest.raises(RunNotFoundError):
        platform.approve("missing")
    with pytest.raises(NotAwaitingApprovalError):
        platform.approve(done.run_id or "")


def test_mcp_clients_get_approval_required_and_cannot_approve() -> None:
    executor = ToolExecutor(ToolRegistry([write_note]), GRANTED, approvals=MemoryApprovalLedger())

    result = call_tool(executor, "write_note", {"text": "x"})

    assert result["isError"] is True
    assert result["content"][0]["text"].startswith("approval_required:")
    assert WRITES == []


def test_settings_read_preapproved_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUANT_AGENT_OS_GRANTED_PERMISSIONS", '["read_local_data", "network"]')
    monkeypatch.setenv("QUANT_AGENT_OS_PREAPPROVED_PERMISSIONS", '["network"]')

    policy = Settings().policy()

    assert policy.preapproved == {Permission.NETWORK}
    assert policy.needs_approval([Permission.NETWORK, Permission.READ_LOCAL_DATA]) == frozenset()
