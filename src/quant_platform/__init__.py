"""Quant-Agent-OS platform server: every product's toolset behind one MCP server.

`compose` assembles the chosen toolsets' tools, resources, and prompts (`Catalog`), adding the
deterministic `research_report`; `qaos serve` serves them, and `open_platform` opens them from a
notebook.
"""

from quant_platform.composition import TOOLSETS, Catalog, Toolset, compose
from quant_platform.notebook import NotebookPlatform, open_platform
from quant_platform.report import ReportRequest, ResearchReport, research_report_tool

__all__ = [
    "TOOLSETS",
    "Catalog",
    "NotebookPlatform",
    "ReportRequest",
    "ResearchReport",
    "Toolset",
    "compose",
    "open_platform",
    "research_report_tool",
]
