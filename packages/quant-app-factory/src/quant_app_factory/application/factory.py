"""`AppFactory`: plan an app, preview it, create it, and run the generated project's checks."""

from __future__ import annotations

import os
import shlex
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from quant_agent_os.runtime.runner import CommandResult, run_command
from quant_app_factory.domain.connections import DATABASE_COMPONENT, Connection, apply_connection
from quant_app_factory.domain.planner import (
    AppPlan,
    ArchitecturePlanner,
    PlanningError,
    pack_location,
)
from quant_app_factory.domain.ports import (
    ScaffoldError,
    ScaffoldPlan,
    ScaffoldProvider,
    ScaffoldRequest,
    ScaffoldRequestError,
    SettingInfo,
    TemplateDescription,
)
from quant_app_factory.domain.secrets import is_reference, secret_problem
from quant_app_factory.domain.spec import (
    BUILT_IN_TEMPLATES,
    AppKind,
    AppSpec,
    Identity,
    dump_spec,
    is_template_path,
)

Runner = Callable[..., CommandResult]  # the signature of `run_command`

SYNC_TIMEOUT_SECONDS = 600.0  # `uv sync` may download Python and every dependency
CHECK_TIMEOUT_SECONDS = 300.0
CHECK_OUTPUT_CHARS = 1500
# Programs a generated project's lifecycle commands may run; anything else is reported, not run.
ALLOWED_PROGRAMS = frozenset({"uv"})


class CheckResult(BaseModel):
    """One lifecycle command run in the generated project."""

    command: str
    ok: bool
    exit_code: int | None
    timed_out: bool = False
    duration_ms: float
    output_tail: str = ""


class FactoryReport(BaseModel):
    """What `create` produced: the project, how it was made, and whether its checks pass."""

    name: str
    kind: AppKind | None
    template_name: str = Field(description="agent-tool, quant-baseline, bundled, or a pack path")
    project_path: str
    files: list[str]
    profile: str
    profile_version: str
    components: list[str]
    dependencies: list[str]
    scaffolder: str
    template: dict[str, Any] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    checks: list[CheckResult] = Field(default_factory=list)
    checks_passed: bool | None = Field(description="None when the checks were skipped")
    warnings: list[str] = Field(default_factory=list)


class SpecProblem(BaseModel):
    """One thing to fix before a spec is ready."""

    field: str
    message: str


class SpecCheck(BaseModel):
    """Whether a spec is ready to create, and if not, what to fix."""

    ready: bool
    problems: list[SpecProblem] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list, description="Exact files, when ready")
    settings: list[SettingInfo] = Field(
        default_factory=list, description="Runtime settings the app will read"
    )
    unbound_settings: list[str] = Field(
        default_factory=list,
        description="Settings the spec leaves to the app's environment at runtime",
    )
    spec_toml: str | None = Field(default=None, description="The spec as a file, when valid")


class ProfileCheck(BaseModel):
    """Whether one profile of a template plans with its default answers."""

    profile: str
    ok: bool
    files: int = 0
    problem: str | None = None


class TemplateCheck(BaseModel):
    """Whether a template pack loads, and whether each of its profiles plans."""

    ok: bool
    problem: str | None = None
    profiles: list[ProfileCheck] = Field(default_factory=list)


class AppFactory:
    """Turn an `AppSpec` into a scaffolded, checked project through a `ScaffoldProvider`."""

    def __init__(
        self,
        scaffolder: ScaffoldProvider,
        *,
        planner: ArchitecturePlanner | None = None,
        runner: Runner = run_command,
        scaffolder_version: str | None = None,
        identity: Callable[[], Identity] | None = None,
        connections: Callable[[], Mapping[str, Connection]] | None = None,
        templates: Callable[[], Mapping[str, str]] | None = None,
        environ: Mapping[str, str] = os.environ,
    ) -> None:
        self.scaffolder = scaffolder
        self.planner = planner or ArchitecturePlanner()
        self._runner = runner
        self._scaffolder_version = scaffolder_version or getattr(scaffolder, "version", None)
        self._identity_source = identity
        self._identity: Identity | None = None
        self._connections = connections
        self._templates = templates
        self._environ = environ

    def plan(self, spec: AppSpec) -> AppPlan:
        """Return the planner's decision for ``spec`` completed with identity and connection."""
        return self.planner.plan(self.complete(spec))

    def template_location(self, template: str) -> str | None:
        """Return the pack directory for a built-in name, a registered name, or a path."""
        if template in BUILT_IN_TEMPLATES or is_template_path(template):
            return pack_location(template)
        registered = dict(self._templates()) if self._templates else {}
        if template not in registered:
            known = ", ".join(sorted(BUILT_IN_TEMPLATES | set(registered)))
            raise PlanningError(f"No template {template!r}; known templates: {known}.")
        return registered[template]

    def check_template(self, template: str) -> TemplateCheck:
        """Load a template and plan every profile with its default answers, writing nothing."""
        try:
            location = self.template_location(template)
            described = self.scaffolder.describe(location)
        except (PlanningError, ScaffoldError) as error:
            return TemplateCheck(ok=False, problem=str(error))
        profiles = []
        with tempfile.TemporaryDirectory(prefix="quant-factory-check-") as scratch:
            for profile in described.profiles:
                request = ScaffoldRequest(
                    project_name="template-check",
                    profile=profile.id,
                    output_dir=scratch,
                    template=location,
                )
                try:
                    preview = self.scaffolder.plan(request)
                except ScaffoldError as error:
                    profiles.append(ProfileCheck(profile=profile.id, ok=False, problem=str(error)))
                else:
                    profiles.append(
                        ProfileCheck(profile=profile.id, ok=True, files=len(preview.files))
                    )
        return TemplateCheck(ok=all(item.ok for item in profiles), profiles=profiles)

    def complete(self, spec: AppSpec) -> AppSpec:
        """Fill identity defaults, resolve a registered template, and apply a connection."""
        spec = self._with_identity(spec)
        if (
            spec.template is not None
            and spec.template not in BUILT_IN_TEMPLATES
            and not is_template_path(spec.template)
        ):
            spec = spec.model_copy(update={"template": self.template_location(spec.template)})
        if spec.connection is None:
            return spec
        saved = dict(self._connections()) if self._connections else {}
        if spec.connection not in saved:
            known = ", ".join(sorted(saved)) or "none saved"
            raise PlanningError(
                f"No connection {spec.connection!r} in factory.toml ({known}); add it with "
                f"`quant-factory settings set connections.{spec.connection}.kind ...`."
            )
        return apply_connection(spec, saved[spec.connection])

    def describe_template(self, template: str, profile: str | None = None) -> TemplateDescription:
        """Describe a template; with ``profile``, only what that profile can use."""
        try:
            location = self.template_location(template)
        except PlanningError as error:
            raise ScaffoldRequestError(str(error)) from error
        description = self.scaffolder.describe(location)
        if profile is None:
            return description
        chosen = [p for p in description.profiles if p.id == profile]
        if not chosen:
            offered = ", ".join(p.id for p in description.profiles)
            raise ScaffoldRequestError(f"Template has no profile {profile!r}; it offers {offered}.")
        components = [c for c in description.components if profile in c.compatible_profiles]
        asked = set(chosen[0].questions) | {q for c in components for q in c.questions}
        return TemplateDescription(
            profiles=chosen,
            components=components,
            questions=[q for q in description.questions if q.name in asked],
        )

    def check_spec(self, raw: dict[str, Any]) -> SpecCheck:
        """Return whether ``raw`` is a spec ready to create, or every problem found.

        pytemplate-uv is the judge of the template's own rules; this adds the spec's fields,
        secrets given as values, unknown settings, and an existing target directory.
        """
        try:
            spec = AppSpec.model_validate(raw)
        except ValidationError as error:
            return SpecCheck(
                ready=False,
                problems=[
                    SpecProblem(
                        field=".".join(str(part) for part in item["loc"]) or "spec",
                        message=item["msg"],
                    )
                    for item in error.errors()
                ],
            )
        problems: list[SpecProblem] = []
        try:
            plan, preview = self.preview(spec)
        except (PlanningError, ScaffoldError) as error:
            problems.append(SpecProblem(field="template", message=str(error)))
            return SpecCheck(ready=False, problems=problems, spec_toml=dump_spec(spec))
        declared = {setting.name: setting for setting in preview.settings}
        for name, value in plan.spec.settings.items():
            if name not in declared:
                problems.append(
                    SpecProblem(
                        field=f"settings.{name}",
                        message=f"no selected component reads {name}; it offers "
                        f"{', '.join(sorted(declared)) or 'no settings'}",
                    )
                )
            elif problem := secret_problem(name, value, declared_secret=declared[name].secret):
                problems.append(SpecProblem(field=f"settings.{name}", message=problem))
        if preview.target_exists:
            problems.append(
                SpecProblem(
                    field="name",
                    message=f"{preview.project_path} already exists; choose another name or "
                    "output_dir",
                )
            )
        return SpecCheck(
            ready=not problems,
            problems=problems,
            files=preview.files if not problems else [],
            settings=preview.settings,
            unbound_settings=sorted(set(declared) - set(plan.spec.settings)),
            spec_toml=dump_spec(spec),
        )

    def _check_connection(self, project: Path, spec: AppSpec) -> tuple[CheckResult, list[str]]:
        """Run ``uv run <app> --check-connection`` with only the referenced variables."""
        extra: dict[str, str] = {}
        notes: list[str] = []
        for name, value in spec.settings.items():
            if not is_reference(value):
                extra[name] = value
                continue
            variable = value.removeprefix("env:")
            if variable in self._environ:
                extra[name] = self._environ[variable]
            else:
                notes.append(f"{variable} is not set, so {name} was not passed to the check.")
        command = ["uv", "run", spec.name, "--check-connection"]
        result = self._runner(command, cwd=project, timeout=CHECK_TIMEOUT_SECONDS, extra_env=extra)
        output = (result.stdout_tail + result.stderr_tail).strip()
        for secret in {value for value in extra.values() if value}:
            output = output.replace(secret, "[REDACTED]")
        return (
            CheckResult(
                command=" ".join(command),
                ok=result.ok,
                exit_code=result.exit_code,
                timed_out=result.timed_out,
                duration_ms=result.duration_ms,
                output_tail=output[-CHECK_OUTPUT_CHARS:],
            ),
            notes,
        )

    def _with_identity(self, spec: AppSpec) -> AppSpec:
        if self._identity_source is None or (spec.author and spec.email and spec.license):
            return spec
        if self._identity is None:
            self._identity = self._identity_source()
        identity = self._identity
        return spec.model_copy(
            update={
                "author": spec.author or identity.author,
                "email": spec.email or identity.email,
                "license": spec.license or identity.license,
            }
        )

    def preview(self, spec: AppSpec) -> tuple[AppPlan, ScaffoldPlan]:
        """Return the plan and the scaffolder's exact file list; nothing is written."""
        plan = self.plan(spec)
        return plan, self.scaffolder.plan(plan.request)

    def create(
        self, spec: AppSpec, *, run_checks: bool = True, check_connection: bool = False
    ) -> FactoryReport:
        """Create the project and, unless ``run_checks`` is false, run its lifecycle commands.

        ``check_connection`` also runs the app's ``--check-connection`` once its checks pass,
        passing only the variables the spec's settings reference.
        """
        plan, preview = self.preview(spec)
        if preview.target_exists:
            raise ScaffoldRequestError(
                f"{preview.project_path} already exists; choose another name or output_dir."
            )
        result = self.scaffolder.create(plan.request)
        warnings: list[str] = []
        checks: list[CheckResult] = []
        if run_checks:
            checks, warnings = self.check(
                Path(result.project_path), result.lifecycle_commands, preview.package_name
            )
        completed = plan.spec
        if check_connection:
            if DATABASE_COMPONENT not in result.components:
                warnings.append("No database component, so there is no connection to check.")
            elif checks and not all(check.ok for check in checks):
                warnings.append("The project's checks failed, so the connection was not checked.")
            else:
                connection_check, notes = self._check_connection(
                    Path(result.project_path), completed
                )
                checks.append(connection_check)
                warnings += notes
        return FactoryReport(
            name=spec.name,
            kind=spec.kind,
            template_name=spec.target[0],
            project_path=result.project_path,
            files=preview.files,
            profile=result.profile,
            profile_version=result.profile_version,
            components=result.components,
            dependencies=preview.dependencies,
            scaffolder=self._scaffolder_version or self.scaffolder.name,
            template=preview.template,
            reasons=plan.reasons,
            checks=checks,
            checks_passed=all(check.ok for check in checks) if checks else None,
            warnings=warnings,
        )

    def check(
        self, project: Path, commands: list[str], package: str
    ) -> tuple[list[CheckResult], list[str]]:
        """Run ``commands`` in ``project`` in order; stop after a failed dependency install."""
        checks: list[CheckResult] = []
        warnings: list[str] = []
        for command in commands:
            text = command.replace("{package}", package)
            argv = shlex.split(text)
            if not argv or argv[0] not in ALLOWED_PROGRAMS:
                warnings.append(f"Skipped '{text}': only {', '.join(ALLOWED_PROGRAMS)} may run.")
                continue
            installing = argv[:2] == ["uv", "sync"]
            result = self._runner(
                argv,
                cwd=project,
                timeout=SYNC_TIMEOUT_SECONDS if installing else CHECK_TIMEOUT_SECONDS,
            )
            output = (result.stdout_tail + result.stderr_tail).strip()
            checks.append(
                CheckResult(
                    command=text,
                    ok=result.ok,
                    exit_code=result.exit_code,
                    timed_out=result.timed_out,
                    duration_ms=result.duration_ms,
                    output_tail=output[-CHECK_OUTPUT_CHARS:],
                )
            )
            if installing and not result.ok:
                warnings.append("Dependency installation failed, so the other checks did not run.")
                break
        return checks, warnings
