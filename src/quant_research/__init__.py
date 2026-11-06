"""Quant Research: cited keyword search with entity linking and a small graph of extracted facts."""

from importlib.metadata import PackageNotFoundError, version

from quant_research.application.retrieval import EvidencePacket, ResearchQuery, ResearchService
from quant_research.bootstrap import ResearchApp, open_research
from quant_research.tools import MCP_INSTRUCTIONS, research_resources, research_tools

try:
    __version__ = version("quant-agent-os")
except PackageNotFoundError:  # pragma: no cover - source checkout without installation
    __version__ = "0.0.0"

__all__ = [
    "MCP_INSTRUCTIONS",
    "EvidencePacket",
    "ResearchApp",
    "ResearchQuery",
    "ResearchService",
    "open_research",
    "research_resources",
    "research_tools",
]
