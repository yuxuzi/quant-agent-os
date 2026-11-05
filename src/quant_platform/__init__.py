"""Quant-Agent-OS platform server: every product's toolset behind one MCP server.

`compose` assembles the chosen toolsets' tools, resources, and prompts (`Catalog`), adding the
deterministic `research_report`; `qaos serve` serves them.
"""

from quant_platform.composition import TOOLSETS, Catalog, Toolset, compose
from quant_platform.report import ReportRequest, ResearchReport, research_report_tool

__all__ = [
    "TOOLSETS",
    "Catalog",
    "ReportRequest",
    "ResearchReport",
    "Toolset",
    "compose",
    "research_report_tool",
]
