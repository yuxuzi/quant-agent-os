"""Quant Research tools: thin `@tool` adapters over `ResearchService` (no logic here).

Both tools are read-only. Ingestion is deliberately not a tool: writes to the knowledge store are
operator commands.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from quant_agent_os.tools import Permission, ToolSpec, tool
from quant_research.application.retrieval import (
    EvidencePacket,
    ResearchQuery,
    ResearchService,
    SourcePassage,
)


class PassageRequest(BaseModel):
    """Which passage to return in full."""

    chunk_id: str = Field(min_length=1, description="A chunk id from find_related_signals")


def research_tools(service: ResearchService) -> list[ToolSpec[Any, Any]]:
    """Return the Quant Research tools bound to ``service``, ready to register."""

    @tool(
        name="find_related_signals",
        description=(
            "Find signals, factors, and strategies related to a research question in the "
            "user's corpus. Returns ranked passages with source locations and knowledge-graph "
            "links, each citing the passage that supports it. Cite sources when answering."
        ),
        permissions=[Permission.READ_LOCAL_DATA],
        max_output_bytes=200_000,
    )
    def find_related_signals(params: ResearchQuery) -> EvidencePacket:
        return service.find_related(params)

    @tool(
        name="get_source_passage",
        description="Return the full text and source location of one passage by chunk id.",
        permissions=[Permission.READ_LOCAL_DATA],
    )
    def get_source_passage(params: PassageRequest) -> SourcePassage:
        return service.get_passage(params.chunk_id)

    return [find_related_signals, get_source_passage]
