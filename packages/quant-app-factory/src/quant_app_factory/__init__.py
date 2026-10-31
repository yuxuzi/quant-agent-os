"""Quant App Factory: generate tested, packaged Python applications with Quant-Agent-OS.

An `AppSpec` goes through the deterministic `ArchitecturePlanner` to a `ScaffoldProvider`
(the pytemplate-uv library by default), and the generated project's own checks run through the
platform's subprocess runner. `create_app` needs an operator approval per call.
"""

from importlib.metadata import PackageNotFoundError, version

from quant_app_factory.application.factory import AppFactory, CheckResult, FactoryReport
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
from quant_app_factory.domain.spec import AppSpec, SpecError, load_spec
from quant_app_factory.infrastructure.pytemplate_uv import PytemplateUvScaffolder
from quant_app_factory.tools import factory_tools

try:
    __version__ = version("quant-agent-os-factory")
except PackageNotFoundError:  # pragma: no cover - source checkout without installation
    __version__ = "0.0.0"

__all__ = [
    "AppFactory",
    "AppPlan",
    "AppSpec",
    "ArchitecturePlanner",
    "CheckResult",
    "FactoryReport",
    "PlanningError",
    "PytemplateUvScaffolder",
    "ScaffoldError",
    "ScaffoldPlan",
    "ScaffoldProvider",
    "ScaffoldRenderError",
    "ScaffoldRequest",
    "ScaffoldRequestError",
    "ScaffoldResult",
    "SpecError",
    "factory_tools",
    "load_spec",
]
