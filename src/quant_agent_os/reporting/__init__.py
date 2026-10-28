"""Structured reports."""

from quant_agent_os.reporting.generator import build_report, render_markdown
from quant_agent_os.reporting.models import Report, RunMetadata

__all__ = ["Report", "RunMetadata", "build_report", "render_markdown"]
