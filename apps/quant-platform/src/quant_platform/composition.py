"""The composition root: choose toolsets and assemble their tools, resources, and prompts.

This is the only place that imports every product (decision 0003 keeps them independent of
each other). Each toolset contributes its own tools, resources, prompts, and a paragraph of
server instructions; `research_report` is added when the research toolset is served. The
factory toolset is an optional extra (``quant-agent-os-platform[factory]``, decision 0012), so
App Factory is imported only when it is served.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from quant_agent_os.data import DatasetStore, dataset_resources, dataset_tools
from quant_agent_os.tools import PermissionPolicy, PromptSpec, ResourceSpec, ToolSpec
from quant_analytics.tools import analytics_tools
from quant_platform.prompts import (
    ANALYZE_RETURNS,
    DRAFT_APP_SPEC,
    REPORT_ON_SIGNAL,
    RESEARCH_SIGNAL,
)
from quant_platform.report import research_report_tool
from quant_research import research_resources, research_tools
from quant_research.application.retrieval import ResearchService
from quant_research.cli import MCP_INSTRUCTIONS as RESEARCH_INSTRUCTIONS

if TYPE_CHECKING:
    from quant_app_factory import AppFactory

Toolset = Literal["research", "analytics", "factory"]
TOOLSETS: tuple[Toolset, ...] = ("research", "analytics", "factory")
ANALYTICS_INSTRUCTIONS = (
    "Quant Analytics computes performance and risk metrics with stated conventions. Call "
    "list_datasets and pass series as {dataset, column} rather than copying values; report "
    "the numbers as computed, never recomputed."
)
PLATFORM_INSTRUCTIONS = "Quant-Agent-OS: quantitative research tools with cited, computed evidence."
FACTORY_EXTRA = "install the factory extra: uv add 'quant-agent-os-platform[factory]'"
REPORT_INSTRUCTIONS = (
    "research_report gives a cited report on one signal, with metrics of a named series, in "
    "one call."
)


def available_toolsets() -> list[Toolset]:
    """Return the toolsets this installation can serve (factory only with its extra)."""
    has_factory = importlib.util.find_spec("quant_app_factory") is not None
    return [name for name in TOOLSETS if name != "factory" or has_factory]


@dataclass(frozen=True)
class Catalog:
    """Everything one server offers: tools, resources, prompts, and its instructions."""

    tools: list[ToolSpec[Any, Any]] = field(default_factory=list)
    resources: list[ResourceSpec] = field(default_factory=list)
    prompts: list[PromptSpec] = field(default_factory=list)
    instructions: str = PLATFORM_INSTRUCTIONS


def compose(
    *,
    policy: PermissionPolicy,
    research: ResearchService | None = None,
    datasets: DatasetStore | None = None,
    factory: AppFactory | None = None,
) -> Catalog:
    """Return the catalog for the given products; each one given is a toolset served.

    ``research`` serves the research toolset and `research_report`; ``datasets`` serves
    analytics (series by reference) and `list_datasets`; ``factory`` serves the App Factory's
    guiding tools. ``policy`` is the server's, which `research_report` applies to its steps.
    """
    tools: list[ToolSpec[Any, Any]] = []
    resources: list[ResourceSpec] = []
    prompts: list[PromptSpec] = []
    paragraphs = [PLATFORM_INSTRUCTIONS]
    if research is not None:
        tools += research_tools(research)
        resources += research_resources()
        prompts.append(RESEARCH_SIGNAL)
        paragraphs.append(RESEARCH_INSTRUCTIONS)
    if datasets is not None:
        tools += [*analytics_tools(datasets).specs(), *dataset_tools(datasets)]
        resources += dataset_resources()
        prompts.append(ANALYZE_RETURNS)
        paragraphs.append(ANALYTICS_INSTRUCTIONS)
    if research is not None:
        tools.append(research_report_tool(list(tools), policy))
        prompts.append(REPORT_ON_SIGNAL)
        paragraphs.append(REPORT_INSTRUCTIONS)
    if factory is not None:
        from quant_app_factory import factory_resources
        from quant_app_factory.cli import MCP_INSTRUCTIONS as FACTORY_INSTRUCTIONS
        from quant_app_factory.cli import served_tools as factory_served_tools

        tools += factory_served_tools(factory)
        resources += factory_resources()
        prompts.append(DRAFT_APP_SPEC)
        paragraphs.append(FACTORY_INSTRUCTIONS)
    return Catalog(tools, resources, prompts, "\n\n".join(paragraphs))
