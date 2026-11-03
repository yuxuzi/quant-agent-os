"""Usage reports from usage events (decision 0015): totals per day or per tool, for export."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel

from quant_agent_os.runs.models import UsageEvent

UsageGrouping = Literal["day", "tool"]


class UsageTotal(BaseModel):
    """The calls of one tenant in one group (a UTC day or a tool), and what they used.

    ``calls`` counts every recorded call, ``billable_calls`` only those that used the server.
    """

    tenant: str
    group: str
    toolset: str | None = None
    calls: int = 0
    billable_calls: int = 0
    duration_ms: float = 0.0
    input_bytes: int = 0
    output_bytes: int = 0


def summarize(events: Iterable[UsageEvent], by: UsageGrouping) -> list[UsageTotal]:
    """Return per-tenant totals of ``events`` by UTC day or by tool, sorted by tenant and group."""
    totals: dict[tuple[str, str], UsageTotal] = {}
    for event in events:
        group = event.at.date().isoformat() if by == "day" else event.tool
        key = (event.tenant, group)
        total = totals.get(key)
        if total is None:
            total = totals[key] = UsageTotal(
                tenant=event.tenant,
                group=group,
                toolset=event.toolset if by == "tool" else None,
            )
        total.calls += 1
        total.billable_calls += int(event.billable)
        total.duration_ms = round(total.duration_ms + event.duration_ms, 3)
        total.input_bytes += event.input_bytes
        total.output_bytes += event.output_bytes
    return [totals[key] for key in sorted(totals)]
