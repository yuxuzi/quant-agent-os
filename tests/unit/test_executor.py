"""Unit tests for bounded tool execution."""

import time
from typing import get_args

import pytest
from pydantic import BaseModel

from quant_agent_os.runtime import PlanStep, ToolExecutor, failure_details
from quant_agent_os.runtime.executor import FAILURE_HINTS, CallStatus
from quant_agent_os.tools import Permission, PermissionPolicy, ToolRegistry, tool


class Value(BaseModel):
    value: int


class Text(BaseModel):
    text: str


@tool(name="double", description="Double a number.")
def double(params: Value) -> Value:
    return Value(value=params.value * 2)


@tool(name="slow", description="Sleep too long.", timeout_seconds=0.05)
def slow(params: Value) -> Value:
    time.sleep(0.5)
    return params


@tool(name="huge", description="Return too much text.", max_output_bytes=100)
def huge(params: Value) -> Text:
    return Text(text="x" * params.value)


@tool(name="broken", description="Always fails.")
def broken(params: Value) -> Value:
    raise RuntimeError("boom")


@tool(name="fetch", description="Needs the network.", permissions=[Permission.NETWORK])
def fetch(params: Value) -> Value:
    return params


EXECUTOR = ToolExecutor(ToolRegistry([double, slow, huge, broken, fetch]), PermissionPolicy())


def run(tool_name: str, **arguments: object) -> str:
    return EXECUTOR.execute(PlanStep(tool=tool_name, arguments=arguments)).status


def test_successful_call_records_output_and_version() -> None:
    record = EXECUTOR.execute(PlanStep(tool="double", arguments={"value": 21}))

    assert record.status == "ok"
    assert record.output == {"value": 42}
    assert record.tool_version == "1.0.0"
    assert record.duration_ms >= 0


def test_failures_are_recorded_not_raised() -> None:
    assert run("double", value="not a number") == "invalid_input"
    assert run("missing") == "unknown_tool"
    assert run("fetch", value=1) == "denied"
    assert run("slow", value=1) == "timeout"
    assert run("huge", value=500) == "error"
    assert run("broken", value=1) == "error"


def test_error_messages_are_actionable() -> None:
    record = EXECUTOR.execute(PlanStep(tool="broken", arguments={"value": 1}))

    assert record.error == "RuntimeError: boom"


def test_records_carry_provenance_ids_and_a_stable_input_hash() -> None:
    first = EXECUTOR.execute(PlanStep(tool="double", arguments={"value": 2}), run_id="run-1")
    second = EXECUTOR.execute(PlanStep(tool="double", arguments={"value": 2}))

    assert first.run_id == "run-1"
    assert first.call_id != second.call_id
    assert first.input_sha256 == second.input_sha256
    assert first.started_at.tzinfo is not None


def test_invalid_input_is_hashed_from_the_raw_arguments() -> None:
    record = EXECUTOR.execute(PlanStep(tool="double", arguments={"value": "x"}))

    assert record.status == "invalid_input"
    assert record.input_sha256 is not None


def test_every_failure_status_has_a_hint() -> None:
    assert set(FAILURE_HINTS) == set(get_args(CallStatus)) - {"ok"}


def test_failure_details_say_what_to_do_next() -> None:
    def details(tool_name: str, **arguments: object) -> dict[str, object]:
        record = EXECUTOR.execute(PlanStep(tool=tool_name, arguments=arguments))
        return failure_details(record, ["double", "slow"])

    assert details("missing")["hint"] == "Call one of the available tools: double, slow."
    assert details("double", value="x")["retryable"] is True
    assert details("slow", value=1)["retryable"] is True
    assert details("fetch", value=1)["retryable"] is False
    assert details("broken", value=1) == {
        "status": "error",
        "error": "RuntimeError: boom",
        "hint": FAILURE_HINTS["error"][0],
        "retryable": False,
    }


def test_failure_details_refuse_a_successful_call() -> None:
    record = EXECUTOR.execute(PlanStep(tool="double", arguments={"value": 1}))

    with pytest.raises(ValueError, match="failed call"):
        failure_details(record, [])
