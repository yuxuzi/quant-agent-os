"""OpenTelemetry for tool calls (decision 0015, P7.4): a span and two metrics per call.

The executor wraps every call in `call_span`, which emits:
- a span named ``execute_tool <tool>``, with status ERROR when the call ends ``error`` or
  ``timeout``;
- ``qaos.tool.calls``, a counter of calls;
- ``qaos.tool.duration``, a histogram of call durations in seconds.

Both carry ``qaos.tenant``, ``qaos.tool``, and ``qaos.call.status``; the span also carries
``qaos.tool.version``. Nothing else is attached: never arguments, outputs, principals, or keys.

Only the OpenTelemetry API is used (the ``mcp`` and ``api`` extras bring it). Without an SDK
the API does nothing, and without the API installed `call_span` does nothing either. To export
telemetry, an operator installs an SDK and an exporter and configures them, for example with
``opentelemetry-instrument``.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any

from quant_agent_os import __version__

try:
    from opentelemetry import metrics, trace
    from opentelemetry.trace import Status, StatusCode
except ImportError:  # pragma: no cover - a core install without the mcp or api extra
    ENABLED = False
else:
    ENABLED = True

SCOPE = "quant_agent_os"
CALLS = "qaos.tool.calls"
DURATION = "qaos.tool.duration"
FAILED = frozenset({"error", "timeout"})

# Finishes a call's telemetry with its status, tool version, and duration in milliseconds.
Finish = Callable[[str, str | None, float], None]


class _Instruments:
    """The tracer and metric instruments, made once from the global (proxy) providers."""

    def __init__(self) -> None:
        self.tracer: Any = trace.get_tracer(SCOPE, __version__)
        meter = metrics.get_meter(SCOPE, __version__)
        self.calls: Any = meter.create_counter(CALLS, unit="{call}", description="Tool calls.")
        self.duration: Any = meter.create_histogram(
            DURATION, unit="s", description="Tool call duration."
        )


_instruments: _Instruments | None = None


def instruments() -> _Instruments:
    """Return the shared instruments, creating them on first use."""
    global _instruments
    if _instruments is None:
        _instruments = _Instruments()
    return _instruments


def _ignore(status: str, version: str | None, duration_ms: float) -> None:
    return None


@contextmanager
def call_span(tool: str, tenant: str) -> Generator[Finish, None, None]:
    """Trace one call of ``tool`` for ``tenant``; the caller reports how it ended."""
    if not ENABLED:
        yield _ignore
        return
    used = instruments()
    with used.tracer.start_as_current_span(
        f"execute_tool {tool}", attributes={"qaos.tenant": tenant, "qaos.tool": tool}
    ) as span:

        def finish(status: str, version: str | None, duration_ms: float) -> None:
            span.set_attribute("qaos.call.status", status)
            if version is not None:
                span.set_attribute("qaos.tool.version", version)
            if status in FAILED:
                span.set_status(Status(StatusCode.ERROR, status))
            attributes = {"qaos.tenant": tenant, "qaos.tool": tool, "qaos.call.status": status}
            used.calls.add(1, attributes)
            used.duration.record(duration_ms / 1000, attributes)

        yield finish
