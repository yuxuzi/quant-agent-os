"""Unit tests for bounded tool execution."""

import time
from typing import get_args

import pytest
from pydantic import BaseModel

from quant_agent_os.runtime import ToolCall, ToolCallError, ToolExecutor, failure_details
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


RAISED: list[Exception] = [
    ValueError("returns must be finite"),
    LookupError("no dataset 'fund'"),
    RuntimeError("cannot open /srv/data/kb.db"),
    OSError("disk I/O error at /srv/data"),
    KeyError("internal_field"),
    IndexError("list index out of range"),
]


@tool(name="raising", description="Raise the error numbered by value.")
def raising(params: Value) -> Value:
    raise RAISED[params.value]


@tool(name="fetch", description="Needs the network.", permissions=[Permission.NETWORK])
def fetch(params: Value) -> Value:
    return params


EXECUTOR = ToolExecutor(
    ToolRegistry([double, slow, huge, broken, raising, fetch]), PermissionPolicy()
)


def run(tool_name: str, **arguments: object) -> str:
    return EXECUTOR.execute(ToolCall(tool=tool_name, arguments=arguments)).status


def test_successful_call_records_output_and_version() -> None:
    record = EXECUTOR.execute(ToolCall(tool="double", arguments={"value": 21}))

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


@pytest.mark.parametrize("index", [0, 1])
def test_a_caller_error_returns_its_message(index: int) -> None:
    record = EXECUTOR.execute(ToolCall(tool="raising", arguments={"value": index}))

    raised = RAISED[index]
    assert record.error == f"{type(raised).__name__}: {raised}"
    assert record.error_detail is None


@pytest.mark.parametrize("index", [2, 3, 4, 5])
def test_an_internal_error_returns_a_generic_message_and_keeps_the_detail(index: int) -> None:
    record = EXECUTOR.execute(ToolCall(tool="raising", arguments={"value": index}))

    raised = RAISED[index]
    assert record.status == "error"
    assert record.error == (
        f"Tool 'raising' failed with an internal error (call {record.call_id}). The operator "
        "can find the details in the run record."
    )
    assert str(raised) not in record.error and "/srv" not in record.error
    assert record.error_detail == f"{type(raised).__name__}: {raised}"


def test_records_carry_provenance_ids_and_a_stable_input_hash() -> None:
    first = EXECUTOR.execute(ToolCall(tool="double", arguments={"value": 2}), run_id="run-1")
    second = EXECUTOR.execute(ToolCall(tool="double", arguments={"value": 2}))

    assert first.run_id == "run-1"
    assert first.call_id != second.call_id
    assert first.input_sha256 == second.input_sha256
    assert first.started_at.tzinfo is not None


def test_invalid_input_is_hashed_from_the_raw_arguments() -> None:
    record = EXECUTOR.execute(ToolCall(tool="double", arguments={"value": "x"}))

    assert record.status == "invalid_input"
    assert record.input_sha256 is not None


def test_every_failure_status_has_a_hint() -> None:
    assert set(FAILURE_HINTS) == set(get_args(CallStatus)) - {"ok"}


def test_failure_details_say_what_to_do_next() -> None:
    def details(tool_name: str, **arguments: object) -> dict[str, object]:
        record = EXECUTOR.execute(ToolCall(tool=tool_name, arguments=arguments))
        return failure_details(record, ["double", "slow"])

    assert details("missing")["hint"] == "Call one of the available tools: double, slow."
    assert details("double", value="x")["retryable"] is True
    assert details("slow", value=1)["retryable"] is True
    assert details("fetch", value=1)["retryable"] is False
    assert details("raising", value=0) == {
        "status": "error",
        "error": "ValueError: returns must be finite",
        "hint": FAILURE_HINTS["error"][0],
        "retryable": False,
    }


def test_failure_details_refuse_a_successful_call() -> None:
    record = EXECUTOR.execute(ToolCall(tool="double", arguments={"value": 1}))

    with pytest.raises(ValueError, match="failed call"):
        failure_details(record, [])


def test_result_returns_the_output_of_a_successful_call() -> None:
    record = EXECUTOR.execute(ToolCall(tool="double", arguments={"value": 4}))

    assert record.result() == {"value": 8}


def test_result_raises_the_status_and_hint_of_a_failed_call() -> None:
    record = EXECUTOR.execute(ToolCall(tool="double", arguments={"value": "many"}))

    with pytest.raises(ToolCallError, match="invalid_input") as caught:
        record.result()

    assert caught.value.status == "invalid_input"
    assert caught.value.error == record.error
    assert caught.value.hint == FAILURE_HINTS["invalid_input"][0]
    assert caught.value.retryable is True
    assert caught.value.record is record


@pytest.mark.parametrize("tool_name", ["missing", "broken", "slow"])
def test_every_failed_call_raises_with_a_finished_hint(tool_name: str) -> None:
    record = EXECUTOR.execute(ToolCall(tool=tool_name, arguments={"value": 1}))

    with pytest.raises(ToolCallError) as caught:
        record.result()

    assert record.status != "ok"
    assert "{" not in caught.value.hint
    assert record.tool in str(caught.value)


def test_a_failed_call_is_recorded_not_raised_until_result_is_asked_for() -> None:
    record = EXECUTOR.execute(ToolCall(tool="broken", arguments={"value": 1}))

    assert record.status == "error"
    assert record.output is None
