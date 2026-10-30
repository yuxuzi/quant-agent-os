"""Quant Research: a Graph RAG knowledge system with source provenance for Quant-Agent-OS."""

from importlib.metadata import PackageNotFoundError, version

from quant_research.application.retrieval import EvidencePacket, ResearchQuery, ResearchService
from quant_research.bootstrap import ResearchApp, open_research
from quant_research.tools import research_tools

try:
    __version__ = version("quant-agent-os-research")
except PackageNotFoundError:  # pragma: no cover - source checkout without installation
    __version__ = "0.0.0"

__all__ = [
    "EvidencePacket",
    "ResearchApp",
    "ResearchQuery",
    "ResearchService",
    "open_research",
    "research_tools",
]
