"""The composition root: choose toolsets and assemble their tools, resources, and prompts.

This is the only place that imports every product (decision 0003 keeps them independent of
each other). Each toolset contributes its own tools, resources, prompts, and a paragraph of
server instructions; `research_report` is added when the research toolset is served. A firm's own
tools join through ``extra_tools``, named in its own code and never discovered (decision 0020). The
analytics and factory toolsets need extras (``quant-agent-os[analytics]``, ``[factory]``), so each
is imported only when it is served.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from quant_agent_os.data import DatasetStore, GuardedDatasetStore, dataset_resources, dataset_tools
from quant_agent_os.registration import FirmApp
from quant_agent_os.tools import AccessPolicy, PermissionPolicy, PromptSpec, ResourceSpec, ToolSpec
from quant_agent_os.tools.registry import DuplicateToolError
from quant_agent_os.tools.server_info import (
    SERVER_INFO_TOOL,
    server_info_resource,
    server_info_tool,
)
from quant_platform.prompts import (
    ANALYZE_RETURNS,
    DRAFT_APP_SPEC,
    REPORT_ON_SIGNAL,
    RESEARCH_SIGNAL,
)
from quant_platform.report import STRATEGY_STEP, research_report_tool
from quant_research import MCP_INSTRUCTIONS as RESEARCH_INSTRUCTIONS
from quant_research import research_resources, research_tools
from quant_research.application.retrieval import ResearchService

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
# The module each optional toolset needs, and the extra that brings it.
TOOLSET_NEEDS: dict[Toolset, tuple[str, str]] = {
    "analytics": ("empyrical", "install the quant-agent-os[analytics] extra"),
    "factory": ("pytemplate", "install the quant-agent-os[factory] extra"),
}
REPORT_INSTRUCTIONS = (
    "research_report gives a cited report on one signal, with metrics of a named series, in "
    "one call."
)


def platform_version() -> str:
    """Return the installed platform version; every package is released at this version."""
    from importlib.metadata import version

    return version("quant-agent-os")


def available_toolsets() -> list[Toolset]:
    """Return the toolsets this installation can serve, given the extras it has."""
    return [
        name
        for name in TOOLSETS
        if name not in TOOLSET_NEEDS or importlib.util.find_spec(TOOLSET_NEEDS[name][0]) is not None
    ]


@dataclass(frozen=True)
class Catalog:
    """Everything one server offers: tools, resources, prompts, and its instructions.

    ``toolsets`` names the toolset that serves each tool, for usage events (decision 0015), and
    ``policy`` and ``access`` are what it was composed under, which serving it applies.
    """

    tools: list[ToolSpec[Any, Any]] = field(default_factory=list)
    resources: list[ResourceSpec] = field(default_factory=list)
    prompts: list[PromptSpec] = field(default_factory=list)
    instructions: str = PLATFORM_INSTRUCTIONS
    toolsets: dict[str, str] = field(default_factory=dict)
    policy: PermissionPolicy = field(default_factory=PermissionPolicy)
    access: AccessPolicy | None = None


def compose(
    *,
    policy: PermissionPolicy,
    research: ResearchService | None = None,
    datasets: DatasetStore | None = None,
    factory: AppFactory | None = None,
    extra_tools: Sequence[ToolSpec[Any, Any]] = (),
    extra_instructions: str | None = None,
    access: AccessPolicy | None = None,
    apps: Sequence[FirmApp] = (),
) -> Catalog:
    """Return the catalog for the given products; each one given is a toolset served.

    ``research`` serves the research toolset and `research_report`, whose strategy step is not
    served on its own; ``datasets`` serves
    analytics (series by reference) and `list_datasets`; ``factory`` serves the App Factory's
    guiding tools. ``policy`` is the server's, which `research_report` applies to its steps.
    ``extra_tools`` are a firm's own, served as the toolset ``firm`` under the same executor
    checks, and ``extra_instructions`` tells clients about them. ``apps`` are a firm's
    registered functions (decision 0024), served the same way over ``datasets``, with their
    instructions. Every catalog also serves
    `server_info` (``qaos://server/info``, decision 0015). With ``access``, each caller reaches
    only the tools and datasets it allows (decision 0021); give extra tools a
    `GuardedDatasetStore` too. Raises `DuplicateToolError` when an extra tool reuses a served
    name: it never replaces a platform tool.
    """
    if (
        access is not None
        and datasets is not None
        and not isinstance(datasets, GuardedDatasetStore)
    ):
        datasets = GuardedDatasetStore(datasets, access)
    tools: list[ToolSpec[Any, Any]] = []
    resources: list[ResourceSpec] = []
    prompts: list[PromptSpec] = []
    served: list[Toolset] = []
    toolsets: dict[str, str] = {}

    def add(
        toolset: Toolset | Literal["firm", "platform"], specs: list[ToolSpec[Any, Any]]
    ) -> None:
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
        from quant_analytics.tools import analytics_tools

        add("analytics", [*analytics_tools(datasets).specs(), *dataset_tools(datasets)])
        resources += dataset_resources()
        prompts.append(ANALYZE_RETURNS)
        served.append("analytics")
    if research is not None:
        add("research", [research_report_tool([*tools, *internal], policy)])
        prompts.append(REPORT_ON_SIGNAL)
    if factory is not None:
        from quant_app_factory import factory_resources, served_tools

        add("factory", served_tools(factory))
        resources += factory_resources()
        prompts.append(DRAFT_APP_SPEC)
        served.append("factory")
    extra = [*extra_tools, *(spec for app in apps for spec in app.tools(datasets))]
    _check_extra_names(extra, {spec.name for spec in tools})
    add("firm", extra)
    add("platform", [server_info_tool(platform_version(), list(tools), access)])
    resources.append(server_info_resource())
    instructions = instructions_for(served)
    for text in [extra_instructions, *(app.instructions for app in apps)]:
        if text:
            instructions = f"{instructions}\n\n{text}"
    return Catalog(tools, resources, prompts, instructions, toolsets, policy, access)


def _check_extra_names(extra: Sequence[ToolSpec[Any, Any]], served: set[str]) -> None:
    taken = served | {SERVER_INFO_TOOL}
    for spec in extra:
        if spec.name in taken:
            raise DuplicateToolError(
                f"Tool '{spec.name}' has a name this server already serves; rename the firm's tool."
            )
        taken.add(spec.name)


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
        from quant_app_factory import MCP_INSTRUCTIONS as FACTORY_INSTRUCTIONS

        paragraphs.append(FACTORY_INSTRUCTIONS)
    return "\n\n".join(paragraphs)
