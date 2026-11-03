"""OpenTelemetry per call (B-36 P7.4, decision 0015): a span and two metrics, and nothing else."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from pydantic import BaseModel

from quant_agent_os.runtime import PlanStep, ToolExecutor, telemetry
from quant_agent_os.tools import Permission, PermissionPolicy, ToolRegistry, tool


class Query(BaseModel):
    query: str
    api_key: str = "none"


class Answer(BaseModel):
    answer: str


@tool(name="lookup", description="Answer a query.", version="2.1.0")
def lookup(params: Query) -> Answer:
    return Answer(answer=params.query.upper())


@tool(name="broken", description="Always fails.")
def broken(params: Query) -> Answer:
    raise RuntimeError("boom")


@tool(name="slow", description="Takes too long.", timeout_seconds=0.01)
def slow(params: Query) -> Answer:
    time.sleep(0.2)
    return Answer(answer="late")


@tool(name="fetch", description="Needs the network.", permissions=[Permission.NETWORK])
def fetch(params: Query) -> Answer:
    return Answer(answer="fetched")


class Span:
    def __init__(self, name: str, attributes: dict[str, Any]) -> None:
        self.name = name
        self.attributes = dict(attributes)
        self.status: Any = None

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_status(self, status: Any) -> None:
        self.status = status


class Instruments:
    def __init__(self) -> None:
        self.spans: list[Span] = []
        self.counted: list[tuple[int, dict[str, Any]]] = []
        self.timed: list[tuple[float, dict[str, Any]]] = []
        self.tracer = self
        self.calls = self
        self.duration = self

    @contextmanager
    def start_as_current_span(self, name: str, attributes: dict[str, Any]) -> Iterator[Span]:
        span = Span(name, attributes)
        self.spans.append(span)
        yield span

    def add(self, amount: int, attributes: dict[str, Any]) -> None:
        self.counted.append((amount, attributes))

    def record(self, value: float, attributes: dict[str, Any]) -> None:
        self.timed.append((value, attributes))


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> Instruments:
    fake = Instruments()
    monkeypatch.setattr(telemetry, "_instruments", fake)
    return fake


EXECUTOR = ToolExecutor(
    ToolRegistry([lookup, broken, slow, fetch]), PermissionPolicy(), tenant="acme"
)


def call(tool_name: str, **arguments: object) -> str:
    return EXECUTOR.execute(PlanStep(tool=tool_name, arguments=arguments)).status


def test_a_call_is_one_span_one_count_and_one_duration(seen: Instruments) -> None:
    record = EXECUTOR.execute(PlanStep(tool="lookup", arguments={"query": "momentum"}))

    (span,) = seen.spans
    assert span.name == "execute_tool lookup" and span.status is None
    assert span.attributes == {
        "qaos.tenant": "acme",
        "qaos.tool": "lookup",
        "qaos.call.status": "ok",
        "qaos.tool.version": "2.1.0",
    }
    labels = {"qaos.tenant": "acme", "qaos.tool": "lookup", "qaos.call.status": "ok"}
    assert seen.counted == [(1, labels)]
    assert seen.timed == [(pytest.approx(record.duration_ms / 1000), labels)]


def test_only_errors_and_timeouts_mark_the_span_failed(seen: Instruments) -> None:
    statuses = [call("broken", query="q"), call("slow", query="q"), call("fetch", query="q")]

    assert statuses == ["error", "timeout", "denied"]
    failed = [span.status is not None for span in seen.spans]
    assert failed == [True, True, False]
    assert seen.spans[0].status.description == "error"


def test_no_argument_or_secret_reaches_telemetry(seen: Instruments) -> None:
    call("lookup", query="secret question", api_key="sk-live-123")

    emitted = repr([span.attributes for span in seen.spans]) + repr(seen.counted + seen.timed)
    assert "secret question" not in emitted and "sk-live-123" not in emitted


def test_an_unknown_tool_name_is_never_a_label(seen: Instruments) -> None:
    assert call("ignore previous instructions", query="q") == "unknown_tool"

    assert seen.spans[0].name == "execute_tool unknown"
    assert seen.counted[0][1]["qaos.tool"] == "unknown"


def test_without_an_sdk_the_api_does_nothing_and_calls_still_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(telemetry, "_instruments", None)

    assert telemetry.ENABLED
    assert call("lookup", query="q") == "ok"
    assert telemetry._instruments is not None  # the global no-op providers' instruments


def test_without_the_api_installed_nothing_is_traced(
    monkeypatch: pytest.MonkeyPatch, seen: Instruments
) -> None:
    monkeypatch.setattr(telemetry, "ENABLED", False)

    assert call("lookup", query="q") == "ok"
    assert seen.spans == [] and seen.counted == []
