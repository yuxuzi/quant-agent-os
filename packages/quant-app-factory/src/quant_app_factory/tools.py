"""The App Factory's tools: `plan_app` (read-only) and `create_app` (approval per call)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from quant_agent_os.tools import Permission, ToolSpec, tool
from quant_app_factory.application.factory import AppFactory, FactoryReport
from quant_app_factory.domain.planner import AppPlan
from quant_app_factory.domain.spec import AppSpec

CREATE_PERMISSIONS = frozenset(
    {Permission.WRITE_LOCAL_DATA, Permission.EXECUTE_CODE, Permission.NETWORK}
)


class PlanAppInput(BaseModel):
    """An app to plan."""

    spec: AppSpec


class PlanAppOutput(BaseModel):
    """The plan, and the exact `create_app` input an operator would approve."""

    plan: AppPlan
    create_app_input: dict[str, Any]
    next_step: str
    warnings: list[str] = Field(default_factory=list)


class CreateAppInput(BaseModel):
    """An app to create; ``run_checks`` runs the generated project's lifecycle commands."""

    spec: AppSpec
    run_checks: bool = True


def factory_tools(factory: AppFactory) -> list[ToolSpec[Any, Any]]:
    """Return `plan_app` and `create_app` bound to ``factory``."""

    @tool(
        name="plan_app",
        description=(
            "Plan a Python application from an app spec: the scaffold profile, components, "
            "template answers, and the reasons. Writes nothing and runs nothing."
        ),
    )
    def plan_app(params: PlanAppInput) -> PlanAppOutput:
        plan = factory.plan(params.spec)
        return PlanAppOutput(
            plan=plan,
            create_app_input={"spec": params.spec.model_dump(mode="json"), "run_checks": True},
            next_step=(
                "Call create_app with create_app_input. It writes files, runs commands, and "
                "downloads packages, so it waits for an operator approval of that exact input."
            ),
        )

    @tool(
        name="create_app",
        description=(
            "Create the planned application with pytemplate-uv, then run its checks (uv sync, "
            "ruff, mypy, pytest). Writes files, runs commands, and uses the network, so each "
            "call needs an operator approval."
        ),
        permissions=CREATE_PERMISSIONS,
        timeout_seconds=1800.0,
        max_output_bytes=300_000,
        deterministic=False,
        idempotent=False,
    )
    def create_app(params: CreateAppInput) -> FactoryReport:
        return factory.create(params.spec, run_checks=params.run_checks)

    return [plan_app, create_app]
