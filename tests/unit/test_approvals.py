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
    RunStoreError,
    SqliteRunStore,
)
from quant_agent_os.runtime import Approval, MemoryApprovalLedger, ToolCall, ToolExecutor
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
    step = ToolCall(tool="write_note", arguments={"text": "approved"})
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
        ToolCall(tool="write_note", arguments={"text": "changed"}), run_id="run-1"
    )
    other_run = executor.execute(step, run_id="run-2")
    no_run = executor.execute(step)
    first = executor.execute(step, run_id="run-1")
    replay = executor.execute(step, run_id="run-1")

    assert [r.status for r in (other_input, other_run, no_run)] == ["approval_required"] * 3
    assert (first.status, replay.status) == ("ok", "approval_required")
    assert WRITES == ["approved"]


def test_a_ledger_that_fails_approves_nothing_and_is_recorded_not_raised() -> None:
    class BrokenLedger(MemoryApprovalLedger):
        def consume(self, run_id: str, tool: str, input_sha256: str) -> Approval | None:
            raise RunStoreError("disk I/O error at /secret/path/runs.db")

    registry = ToolRegistry([write_note, read_note])
    executor = ToolExecutor(registry, GRANTED, approvals=BrokenLedger(), max_concurrent_calls=1)

    record = executor.execute(ToolCall(tool="write_note", arguments={"text": "x"}), run_id="r")

    assert record.status == "error"
    assert "could not check its approval" in (record.error or "")
    assert "/secret/path" not in (record.error or "")
    assert "disk I/O error" in (record.error_detail or "")
    assert WRITES == []
    # Its slot is free again: the next call is not refused as busy.
    assert executor.execute(ToolCall(tool="read_note", arguments={"text": "y"})).status == "ok"


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


# Per-tool standing approvals (decision 0030)


@tool(
    name="write_other_note",
    description="Append a note to another file.",
    permissions=[Permission.WRITE_LOCAL_DATA],
    idempotent=False,
)
def write_other_note(params: Note) -> Written:
    WRITES.append(params.text)
    return Written(written=params.text)


def test_a_per_tool_standing_approval_covers_only_the_named_tool() -> None:
    policy = PermissionPolicy(
        granted=READ_ONLY | {Permission.WRITE_LOCAL_DATA},
        preapproved_tools={Permission.WRITE_LOCAL_DATA: frozenset({"write_note"})},
    )
    with Platform([write_note, write_other_note], policy=policy) as platform:
        named = platform.call("write_note", text="x")
        other = platform.call("write_other_note", text="y")
        stored = platform.get_run(named.run_id or "")

    assert (named.status, named.approval) == ("ok", "standing:write_local_data")
    assert stored is not None
    assert stored.events[1].details["approval"] == "standing:write_local_data"
    # Another tool with the same permission still needs its own approval for each call.
    assert (other.status, other.approval) == ("approval_required", None)
    assert WRITES == ["x"]
    assert policy.needs_approval([Permission.WRITE_LOCAL_DATA], "write_note") == frozenset()
    assert policy.needs_approval([Permission.WRITE_LOCAL_DATA], "write_other_note") == {
        Permission.WRITE_LOCAL_DATA
    }
    assert policy.needs_approval([Permission.WRITE_LOCAL_DATA]) == {Permission.WRITE_LOCAL_DATA}


def test_a_per_tool_standing_approval_grants_nothing() -> None:
    policy = PermissionPolicy(
        preapproved_tools={Permission.WRITE_LOCAL_DATA: frozenset({"write_note"})}
    )
    with Platform([write_note], policy=policy) as platform:
        record = platform.call("write_note", text="x")

    assert record.status == "denied"
    assert WRITES == []


def test_settings_read_per_tool_standing_approvals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUANT_AGENT_OS_GRANTED_PERMISSIONS", '["read_local_data", "network"]')
    monkeypatch.setenv(
        "QUANT_AGENT_OS_PREAPPROVED_TOOLS", '{"network": ["risk_desk_var", "risk_stress"]}'
    )

    settings = Settings()
    policy = settings.policy()

    assert policy.preapproved == frozenset()
    assert policy.preapproved_tools == {
        Permission.NETWORK: frozenset({"risk_desk_var", "risk_stress"})
    }
    assert policy.needs_approval([Permission.NETWORK], "risk_stress") == frozenset()
    assert policy.needs_approval([Permission.NETWORK], "embed_query") == {Permission.NETWORK}
    assert settings.public_summary()["preapproved_tools"] == "network: risk_desk_var, risk_stress"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ('{"teleport": ["risk_desk_var"]}', "teleport"),
        ('{"read_local_data": ["risk_desk_var"]}', "needs no approval"),
        ('{"network": ["risk_*"]}', "exact tool names"),
        ('{"network": [""]}', "exact tool names"),
        ('{"network": "risk_desk_var"}', "preapproved_tools"),
    ],
)
def test_settings_refuse_per_tool_standing_approvals_that_are_not_exact(
    monkeypatch: pytest.MonkeyPatch, value: str, message: str
) -> None:
    from pydantic import ValidationError

    monkeypatch.setenv("QUANT_AGENT_OS_PREAPPROVED_TOOLS", value)

    with pytest.raises(ValidationError, match=message):
        Settings()


def test_a_policy_refuses_a_per_tool_approval_of_a_permission_that_needs_none() -> None:
    with pytest.raises(ValueError, match="needs no approval"):
        PermissionPolicy(preapproved_tools={Permission.READ_LOCAL_DATA: frozenset({"read_note"})})


def test_the_config_file_cannot_pre_approve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quant_agent_os.config.settings import ConfigFileError

    monkeypatch.setenv("QUANT_AGENT_OS_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text('[preapproved_tools]\nnetwork = ["risk_desk_var"]\n')

    with pytest.raises(ConfigFileError, match="preapproved_tools"):
        Settings()


def test_start_up_warns_about_server_wide_and_unserved_standing_approvals(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from quant_platform.serve import warn_about_permissions

    policy = PermissionPolicy(
        granted=READ_ONLY | {Permission.WRITE_LOCAL_DATA},
        preapproved=frozenset({Permission.NETWORK}),
        preapproved_tools={
            Permission.WRITE_LOCAL_DATA: frozenset({"write_note", "wirte_note", "read_note"})
        },
    )

    warn_about_permissions([write_note, write_other_note, read_note], policy)
    err = " ".join(capsys.readouterr().err.split())

    assert "approves network for every tool this server serves" in err
    assert "write_local_data for wirte_note, which this server does not serve" in err
    assert "write_local_data for read_note, which does not declare it" in err
    assert "write_other_note needs write_local_data, which needs an approval" in err
    assert "write_note needs" not in err.replace("write_other_note needs", "")


def test_start_up_is_quiet_about_exact_standing_approvals(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from quant_platform.serve import warn_about_permissions

    policy = PermissionPolicy(
        granted=READ_ONLY | {Permission.WRITE_LOCAL_DATA},
        preapproved_tools={Permission.WRITE_LOCAL_DATA: frozenset({"write_note"})},
    )

    warn_about_permissions([write_note, read_note], policy)

    assert capsys.readouterr().err == ""
