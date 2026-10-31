"""`ArchitecturePlanner`: deterministic rules from an `AppSpec` to a scaffold request."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from quant_app_factory.domain.ports import ScaffoldRequest
from quant_app_factory.domain.spec import AppSpec

PACKS = Path(__file__).resolve().parents[1] / "packs"
AGENT_TOOL_PACK = PACKS / "agent-tool"

# Which pytemplate-uv profile builds each kind, and which optional components it accepts.
_PROFILES = {"agent-tool": "agent-tool", "python-cli": "python", "fastapi-service": "fastapi"}
_COMPONENTS = {
    "agent-tool": frozenset[str](),
    "python-cli": frozenset({"container"}),
    "fastapi-service": frozenset(
        {"database", "worker", "auth", "auth-api-key", "auth-oidc", "container"}
    ),
}
_AUTH_MODES = frozenset({"auth-api-key", "auth-oidc"})


class PlanningError(ValueError):
    """Raised when a spec asks for something its kind cannot provide."""


class AppPlan(BaseModel):
    """The planner's decision: the scaffold request and why each choice was made."""

    spec: AppSpec
    request: ScaffoldRequest
    reasons: list[str] = Field(default_factory=list)
    portable: bool = Field(description="Whether the generated app runs without Quant-Agent-OS")


class ArchitecturePlanner:
    """Map an `AppSpec` to a scaffold request. Pure and deterministic: no I/O, no model."""

    def plan(self, spec: AppSpec) -> AppPlan:
        """Return the plan for ``spec``, or raise `PlanningError` with what to change."""
        allowed = _COMPONENTS[spec.kind]
        unknown = sorted(set(spec.components) - allowed)
        if unknown:
            offered = ", ".join(sorted(allowed)) or "none"
            raise PlanningError(
                f"Kind '{spec.kind}' has no component(s) {', '.join(unknown)}; it offers {offered}."
            )
        modes = _AUTH_MODES & set(spec.components)
        if len(modes) > 1:
            raise PlanningError("Choose one authentication mode: auth-api-key or auth-oidc.")
        if "auth" in spec.components and not modes:
            raise PlanningError("Component 'auth' needs a mode: add auth-api-key or auth-oidc.")
        profile = _PROFILES[spec.kind]
        data = {
            "description": spec.description,
            "author": spec.author,
            "email": spec.email,
            "python_version": spec.python_version,
            "license": spec.license,
        }
        reasons = [f"Kind '{spec.kind}' uses the pytemplate-uv profile '{profile}'."]
        template: str | None = None
        if spec.kind == "agent-tool":
            template = AGENT_TOOL_PACK.as_posix()
            data["tool_name"] = spec.tool_name or spec.package_name
            data["quant_agent_os_source"] = _source(spec.quant_agent_os_source)
            reasons += [
                "agent-tool packages come from Quant App Factory's own template pack, which "
                "encodes the platform's tool contract and its contract tests.",
                "The package depends on quant-agent-os on purpose: its tools run on the platform.",
            ]
        else:
            reasons.append(
                f"'{spec.kind}' apps use pytemplate-uv's bundled profile and do not depend on "
                "Quant-Agent-OS, so they stay portable."
            )
        if spec.components:
            reasons.append(f"Optional components requested: {', '.join(spec.components)}.")
        return AppPlan(
            spec=spec,
            request=ScaffoldRequest(
                project_name=spec.name,
                profile=profile,
                output_dir=spec.output_dir,
                components=sorted(spec.components),
                data=data,
                template=template,
            ),
            reasons=reasons,
            portable=spec.kind != "agent-tool",
        )


def _source(source: str) -> str:
    """Return a git URL unchanged, or a local path as an absolute path with forward slashes."""
    if source.startswith("git+"):
        return source
    return Path(source).expanduser().resolve().as_posix()
