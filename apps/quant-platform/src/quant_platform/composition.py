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
from quant_agent_os.tools.server_info import server_info_resource, server_info_tool
from quant_analytics.tools import analytics_tools
from quant_platform.prompts import (
    ANALYZE_RETURNS,
    DRAFT_APP_SPEC,
    REPORT_ON_SIGNAL,
    RESEARCH_SIGNAL,
)
from quant_platform.report import STRATEGY_STEP, research_report_tool
from quant_research import research_resources, research_tools
from quant_research.application.retrieval import ResearchService
from quant_research.cli import MCP_INSTRUCTIONS as RESEARCH_INSTRUCTIONS

if TYPE_CHECKING:
    from quant_app_factory import AppFactory

Toolset = Literal["research", "analytics", "factory"]
TOOLSETS: tuple[Toolset, ...] = ("research", "analytics", "factory")
# App Factory is asked for by name: it costs every research question context, and it cannot create.
DEFAULT_TOOLSETS: tuple[Toolset, ...] = ("research", "analytics")
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


def platform_version() -> str:
    """Return the installed platform version; every package is released at this version."""
    from importlib.metadata import version

    return version("quant-agent-os-platform")


def available_toolsets() -> list[Toolset]:
    """Return the toolsets this installation can serve (factory only with its extra)."""
    has_factory = importlib.util.find_spec("quant_app_factory") is not None
    return [name for name in TOOLSETS if name != "factory" or has_factory]


@dataclass(frozen=True)
class Catalog:
    """Everything one server offers: tools, resources, prompts, and its instructions.

    ``toolsets`` names the toolset that serves each tool, for usage events (decision 0015).
    """

    tools: list[ToolSpec[Any, Any]] = field(default_factory=list)
    resources: list[ResourceSpec] = field(default_factory=list)
    prompts: list[PromptSpec] = field(default_factory=list)
    instructions: str = PLATFORM_INSTRUCTIONS
    toolsets: dict[str, str] = field(default_factory=dict)


def compose(
    *,
    policy: PermissionPolicy,
    research: ResearchService | None = None,
    datasets: DatasetStore | None = None,
    factory: AppFactory | None = None,
) -> Catalog:
    """Return the catalog for the given products; each one given is a toolset served.

    ``research`` serves the research toolset and `research_report`, whose strategy step is not
    served on its own; ``datasets`` serves
    analytics (series by reference) and `list_datasets`; ``factory`` serves the App Factory's
    guiding tools. ``policy`` is the server's, which `research_report` applies to its steps.
    Every catalog also serves `server_info` (``qaos://server/info``, decision 0015).
    """
    tools: list[ToolSpec[Any, Any]] = []
    resources: list[ResourceSpec] = []
    prompts: list[PromptSpec] = []
    served: list[Toolset] = []
    toolsets: dict[str, str] = {}

    def add(toolset: Toolset | Literal["platform"], specs: list[ToolSpec[Any, Any]]) -> None:
        tools.extend(specs)
        toolsets.update((spec.name, toolset) for spec in specs)

    internal: list[ToolSpec[Any, Any]] = []
    if research is not None:
        specs = research_tools(research)
        internal = [spec for spec in specs if spec.name == STRATEGY_STEP]
        add("research", [spec for spec in specs if spec.name != STRATEGY_STEP])
        resources += [spec for spec in research_resources() if spec.tool != STRATEGY_STEP]
        prompts.append(RESEARCH_SIGNAL)
        served.append("research")
    if datasets is not None:
        add("analytics", [*analytics_tools(datasets).specs(), *dataset_tools(datasets)])
        resources += dataset_resources()
        prompts.append(ANALYZE_RETURNS)
        served.append("analytics")
    if research is not None:
        add("research", [research_report_tool([*tools, *internal], policy)])
        prompts.append(REPORT_ON_SIGNAL)
    if factory is not None:
        from quant_app_factory import factory_resources
        from quant_app_factory.cli import served_tools as factory_served_tools

        add("factory", factory_served_tools(factory))
        resources += factory_resources()
        prompts.append(DRAFT_APP_SPEC)
        served.append("factory")
    add("platform", [server_info_tool(platform_version(), list(tools))])
    resources.append(server_info_resource())
    return Catalog(tools, resources, prompts, instructions_for(served), toolsets)


def instructions_for(toolsets: list[Toolset]) -> str:
    """Return the server instructions for ``toolsets``, without opening any store.

    A server with many tenants announces the instructions for every toolset it may serve.
    """
    paragraphs = [PLATFORM_INSTRUCTIONS]
    if "research" in toolsets:
        paragraphs.append(RESEARCH_INSTRUCTIONS)
    if "analytics" in toolsets:
        paragraphs.append(ANALYTICS_INSTRUCTIONS)
    if "research" in toolsets:
        paragraphs.append(REPORT_INSTRUCTIONS)
    if "factory" in toolsets:
        from quant_app_factory.cli import MCP_INSTRUCTIONS as FACTORY_INSTRUCTIONS

        paragraphs.append(FACTORY_INSTRUCTIONS)
    return "\n\n".join(paragraphs)
