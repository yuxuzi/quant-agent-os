"""The structured report of a run: what it found, from what, and how it was produced."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from quant_agent_os.evidence.models import Evidence
from quant_agent_os.runtime.executor import ToolCallRecord


class RunMetadata(BaseModel):
    """What is needed to understand and reproduce a run."""

    run_id: str
    platform_version: str
    registry_digest: str
    started_at: datetime
    finished_at: datetime
    data_versions: dict[str, str] = Field(default_factory=dict)


class Report(BaseModel):
    """Findings with their evidence, plus the tool calls, warnings, and limitations."""

    goal: str
    tool_calls: list[ToolCallRecord]
    findings: list[Evidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    metadata: RunMetadata

    @property
    def succeeded(self) -> bool:
        """Return whether every tool call completed."""
        return all(call.status == "ok" for call in self.tool_calls)
