"""Quant App Factory: generate tested, packaged Python applications with Quant-Agent-OS.

An `AppSpec` goes through the deterministic `ArchitecturePlanner` to a `ScaffoldProvider`
(the pytemplate-uv library by default), and the generated project's own checks run through the
platform's subprocess runner. `create_app` needs an operator approval per call.
"""

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

from quant_app_factory.application.factory import (
    AppFactory,
    CheckResult,
    FactoryReport,
    ProfileCheck,
    SpecCheck,
    SpecProblem,
    TemplateCheck,
)
from quant_app_factory.domain.planner import AppPlan, ArchitecturePlanner, PlanningError
from quant_app_factory.domain.ports import (
    ScaffoldError,
    ScaffoldPlan,
    ScaffoldProvider,
    ScaffoldRenderError,
    ScaffoldRequest,
    ScaffoldRequestError,
    ScaffoldResult,
)
from quant_app_factory.domain.spec import AppSpec, Identity, SpecError, dump_spec, load_spec
from quant_app_factory.tools import (
    MCP_INSTRUCTIONS,
    factory_resources,
    factory_tools,
    guide_tools,
    served_tools,
)

if TYPE_CHECKING:
    from quant_app_factory.infrastructure.pytemplate_uv import PytemplateUvScaffolder

try:
    __version__ = version("quant-agent-os")
except PackageNotFoundError:  # pragma: no cover - source checkout without installation
    __version__ = "0.0.0"


def __getattr__(name: str) -> Any:
    """Import the scaffolder on first use: it needs the ``factory`` extra, and loads Copier."""
    if name == "PytemplateUvScaffolder":
        from quant_app_factory.infrastructure.pytemplate_uv import PytemplateUvScaffolder

        return PytemplateUvScaffolder
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "MCP_INSTRUCTIONS",
    "AppFactory",
    "AppPlan",
    "AppSpec",
    "ArchitecturePlanner",
    "CheckResult",
    "FactoryReport",
    "Identity",
    "PlanningError",
    "ProfileCheck",
    "PytemplateUvScaffolder",
    "ScaffoldError",
    "ScaffoldPlan",
    "ScaffoldProvider",
    "ScaffoldRenderError",
    "ScaffoldRequest",
    "ScaffoldRequestError",
    "ScaffoldResult",
    "SpecCheck",
    "SpecError",
    "SpecProblem",
    "TemplateCheck",
    "dump_spec",
    "factory_resources",
    "factory_tools",
    "guide_tools",
    "load_spec",
    "served_tools",
]
