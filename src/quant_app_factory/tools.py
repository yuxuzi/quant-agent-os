"""The App Factory's tools.

`describe_template`, `check_spec`, and `plan_app` are read-only: a client such as Claude uses
them to guide a person to a ready spec (decision 0007). `create_app` needs an approval per call.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from quant_agent_os.tools import Permission, ResourceSpec, ToolSpec, tool
from quant_app_factory.application.factory import AppFactory, FactoryReport, SpecCheck
from quant_app_factory.domain.planner import AppPlan
from quant_app_factory.domain.ports import ScaffoldPlan, TemplateDescription
from quant_app_factory.domain.spec import AppSpec

CREATE_PERMISSIONS = frozenset(
    {Permission.WRITE_LOCAL_DATA, Permission.EXECUTE_CODE, Permission.NETWORK}
)
CHECK_PERMISSIONS = frozenset({Permission.EXECUTE_CODE, Permission.NETWORK})
SERVED_TOOLS = frozenset({"describe_template", "check_spec"})
MCP_INSTRUCTIONS = (
    "Quant App Factory guides a user to a ready app spec. Call describe_template to learn what "
    "a template asks, ask the user, and call check_spec until it is ready. Then give the user "
    "spec_toml to save and ask them to run `qaos app create FILE`, which asks for their "
    "approval. Never put a secret in a spec: use env:NAME references."
)


class DescribeTemplateInput(BaseModel):
    """A template to describe."""

    template: str = Field(
        default="quant-baseline",
        description="quant-apps, quant-baseline, agent-tool, bundled, or a pack directory",
    )
    profile: str | None = Field(default=None, description="Only what this profile can use")


class DescribeTemplateOutput(BaseModel):
    """What the template asks and offers."""

    description: TemplateDescription
    warnings: list[str] = Field(default_factory=list)


class CheckSpecInput(BaseModel):
    """A draft spec, as a mapping; problems with it are reported, not raised."""

    spec: dict[str, Any]


class CheckSpecOutput(BaseModel):
    """Whether the spec is ready, what to fix, and the spec as a file."""

    check: SpecCheck
    next_step: str
    warnings: list[str] = Field(default_factory=list)


class PlanAppInput(BaseModel):
    """An app to plan."""

    spec: AppSpec


class PlanAppOutput(BaseModel):
    """The plan, the exact files it would write, and the `create_app` input to approve."""

    plan: AppPlan
    preview: ScaffoldPlan
    create_app_input: dict[str, Any]
    next_step: str
    warnings: list[str] = Field(default_factory=list)


class CreateAppInput(BaseModel):
    """An app to create; ``run_checks`` runs the generated project's lifecycle commands."""

    spec: AppSpec
    run_checks: bool = True
    check_connection: bool = Field(
        default=False, description="Also run the app's check-connection command after its checks"
    )
    expected_pack_digest: str | None = Field(
        default=None,
        description="The template pack digest from plan_app; a changed pack is refused",
    )


class CheckAppInput(BaseModel):
    """An existing project to check again, named by the spec that created it."""

    spec: AppSpec
    check_connection: bool = False


def guide_tools(factory: AppFactory) -> list[ToolSpec[Any, Any]]:
    """Return the read-only guiding tools: `describe_template` and `check_spec`."""

    @tool(
        name="describe_template",
        version="1.0.1",
        description=(
            "Describe an App Factory template: its profiles, optional components, questions "
            "(with help, choices, defaults, and conditions), and the runtime settings each "
            "component reads, with secret ones marked. Use it to decide what to ask the user."
        ),
    )
    def describe_template(params: DescribeTemplateInput) -> DescribeTemplateOutput:
        return DescribeTemplateOutput(
            description=factory.describe_template(params.template, params.profile)
        )

    @tool(
        name="check_spec",
        version="2.0.0",
        description=(
            "Check a draft app spec. Returns ready with the exact files, or every problem to "
            "fix, plus the spec as TOML. Secrets must be env:NAME references, never values."
        ),
    )
    def check_spec(params: CheckSpecInput) -> CheckSpecOutput:
        check = factory.check_spec(params.spec)
        if check.ready:
            next_step = (
                "Save spec_toml to a file and ask the user to run `qaos app create FILE`; "
                "creating writes files and runs commands, so the user approves it there."
            )
        else:
            next_step = "Ask the user about each problem, update the spec, and check it again."
        return CheckSpecOutput(check=check, next_step=next_step)

    return [describe_template, check_spec]


def factory_tools(factory: AppFactory) -> list[ToolSpec[Any, Any]]:
    """Return the guiding tools, `plan_app`, and `create_app`, bound to ``factory``."""

    @tool(
        name="plan_app",
        description=(
            "Plan a Python application from an app spec: the profile, components, reasons, "
            "dependencies, checks, and the exact files. Writes nothing to the target and runs "
            "no programs."
        ),
    )
    def plan_app(params: PlanAppInput) -> PlanAppOutput:
        plan, preview = factory.preview(params.spec)
        warnings = [f"{preview.project_path} already exists."] if preview.target_exists else []
        return PlanAppOutput(
            plan=plan,
            preview=preview,
            warnings=warnings,
            create_app_input={
                "spec": params.spec.model_dump(mode="json"),
                "run_checks": True,
                "expected_pack_digest": factory.pack_digest(params.spec),
            },
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
        return factory.create(
            params.spec,
            run_checks=params.run_checks,
            check_connection=params.check_connection,
            expected_pack_digest=params.expected_pack_digest,
        )

    @tool(
        name="check_app",
        description=(
            "Re-run an existing project's checks (uv sync, ruff, mypy, pytest) and, if asked, "
            "its database connection check. Runs commands and uses the network, so each call "
            "needs an operator approval."
        ),
        permissions=CHECK_PERMISSIONS,
        timeout_seconds=1800.0,
        max_output_bytes=300_000,
        deterministic=False,
        idempotent=True,
    )
    def check_app(params: CheckAppInput) -> FactoryReport:
        return factory.recheck(params.spec, check_connection=params.check_connection)

    return [*guide_tools(factory), plan_app, create_app, check_app]


def factory_resources() -> list[ResourceSpec]:
    """Return the template resources, read through `describe_template`."""
    return [
        ResourceSpec(
            uri_template="qaos://template/{template}",
            name="template",
            description="What an App Factory template asks: profiles, components, questions.",
            tool="describe_template",
        ),
        ResourceSpec(
            uri_template="qaos://template/{template}/{profile}",
            name="template-profile",
            description="What one profile of an App Factory template asks.",
            tool="describe_template",
        ),
    ]


def served_tools(factory: AppFactory) -> list[ToolSpec[Any, Any]]:
    """Return the tools a server offers: guidance only, because the user creates in a terminal."""
    return [spec for spec in factory_tools(factory) if spec.name in SERVED_TOOLS]
