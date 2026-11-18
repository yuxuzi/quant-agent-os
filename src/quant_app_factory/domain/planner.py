"""`ArchitecturePlanner`: deterministic rules from an `AppSpec` to a scaffold request."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from quant_app_factory.domain.ports import ScaffoldRequest
from quant_app_factory.domain.spec import AppSpec

PACKS = Path(__file__).resolve().parents[1] / "packs"
AGENT_TOOL_PACK = PACKS / "agent-tool"
QUANT_BASELINE_PACK = PACKS / "quant-baseline"
QUANT_APPS_PACK = PACKS / "quant-apps"
BUILT_IN_PACKS = {
    "agent-tool": AGENT_TOOL_PACK,
    "quant-apps": QUANT_APPS_PACK,
    "quant-baseline": QUANT_BASELINE_PACK,
}
# One application per stage of a fund's investment process (decision 0022).
QUANT_APP_PROFILES = ("alpha-research", "strategy-backtest", "risk-monitor", "performance-report")

# Optional components each built-in profile accepts. User packs are checked by pytemplate-uv.
_QUANT_DATA = frozenset({"data-parquet", "data-database", "agent-tools"})
_COMPONENTS: dict[tuple[str, str], frozenset[str]] = {
    **{("quant-apps", profile): frozenset({"agent-tools"}) for profile in QUANT_APP_PROFILES},
    ("agent-tool", "agent-tool"): frozenset(),
    ("bundled", "python"): frozenset({"container", "devcontainer", "mcp", "release"}),
    ("bundled", "library"): frozenset({"devcontainer", "mcp", "release"}),
    ("bundled", "fastapi"): frozenset(
        {"database", "worker", "auth", "auth-api-key", "auth-oidc", "container", "devcontainer"}
    ),
    ("quant-baseline", "quant-lab"): _QUANT_DATA,
}
_AUTH_MODES = frozenset({"auth-api-key", "auth-oidc"})


def pack_location(template: str) -> str | None:
    """Return the pack directory a template name stands for (None: the bundled profiles)."""
    if template == "bundled":
        return None
    pack = BUILT_IN_PACKS.get(template)
    return pack.as_posix() if pack else template


class PlanningError(ValueError):
    """Raised when a spec asks for something its template cannot provide."""


class AppPlan(BaseModel):
    """The planner's decision: the scaffold request and why each choice was made."""

    spec: AppSpec
    request: ScaffoldRequest
    reasons: list[str] = Field(default_factory=list)
    portable: bool | None = Field(
        description="Whether the app runs without anything from Quant-Agent-OS, not even Quant "
        "Toolbox; None for a user pack"
    )


class ArchitecturePlanner:
    """Map an `AppSpec` to a scaffold request. Pure and deterministic: no I/O, no model."""

    def plan(self, spec: AppSpec) -> AppPlan:
        """Return the plan for ``spec``, or raise `PlanningError` with what to change."""
        template, profile = spec.target
        user_pack = template not in BUILT_IN_PACKS and template != "bundled"
        if not user_pack:
            self._check_components(template, profile, spec.components)
        identity = {
            "description": spec.description,
            "author": spec.author,
            "email": spec.email,
            "python_version": spec.python_version,
            "license": spec.license,
        }
        defaults = {name: value for name, value in identity.items() if value is not None}
        data = dict(spec.answers)
        reasons = [f"Template '{template}' builds profile '{profile}'."]
        portable: bool | None = True
        if template == "agent-tool":
            data["tool_name"] = spec.tool_name or spec.package_name
            data["quant_agent_os_source"] = _source(spec.quant_agent_os_source)
            portable = False
            reasons += [
                "agent-tool packages come from Quant App Factory's own template pack, which "
                "encodes the platform's tool contract and its contract tests.",
                "The package depends on quant-agent-os on purpose: its tools run on the platform.",
            ]
        elif template in {"quant-baseline", "quant-apps"}:
            if template == "quant-baseline":
                reasons.append(
                    "A quant-lab project holds study, backtest, and report, sharing data, "
                    "signals, and one config.toml, with bias tests and a LIMITATIONS.md."
                )
            else:
                reasons.append(
                    f"A {profile} project is one stage of the investment process: code that "
                    "works as generated and that the firm customizes, with known-answer tests."
                )
            data["quant_agent_os_source"] = _source(spec.quant_agent_os_source)
            portable = False
            reasons.append(
                "Its formulas, tables, reports, and run records come from Quant Toolbox "
                "(the quant-toolbox package, MIT), pinned to the release that generated it."
            )
            if "agent-tools" in spec.components:
                reasons.append("agent-tools adds tool wrappers and the quant-agent-os dependency.")
        elif template == "bundled":
            reasons.append(
                "pytemplate-uv's bundled profiles do not depend on Quant-Agent-OS, so the app "
                "stays portable."
            )
        else:
            portable = None
            reasons.append(
                "A user pack: pytemplate-uv checks its profile, components, and answers."
            )
            if "quant_agent_os_source" in spec.model_fields_set:  # a fork of a built-in pack
                data["quant_agent_os_source"] = _source(spec.quant_agent_os_source)
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
                defaults=defaults,
                template=pack_location(template),
            ),
            reasons=reasons,
            portable=portable,
        )

    @staticmethod
    def _check_components(template: str, profile: str, components: list[str]) -> None:
        allowed = _COMPONENTS.get((template, profile))
        if allowed is None:
            offered = sorted(p for t, p in _COMPONENTS if t == template)
            raise PlanningError(
                f"Template '{template}' has no profile '{profile}'; it offers {', '.join(offered)}."
            )
        unknown = sorted(set(components) - allowed)
        if unknown:
            offered_components = ", ".join(sorted(allowed)) or "none"
            raise PlanningError(
                f"Profile '{profile}' has no component(s) {', '.join(unknown)}; it offers "
                f"{offered_components}."
            )
        modes = _AUTH_MODES & set(components)
        if len(modes) > 1:
            raise PlanningError("Choose one authentication mode: auth-api-key or auth-oidc.")
        if "auth" in components and not modes:
            raise PlanningError("Component 'auth' needs a mode: add auth-api-key or auth-oidc.")


def _source(source: str) -> str:
    """Return a git URL unchanged, or a local path as an absolute path with forward slashes."""
    if source.startswith("git+"):
        return source
    return Path(source).expanduser().resolve().as_posix()
