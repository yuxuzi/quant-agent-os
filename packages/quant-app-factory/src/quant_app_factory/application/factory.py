"""`AppFactory`: plan an app, preview it, create it, and run the generated project's checks."""

from __future__ import annotations

import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from quant_agent_os.runtime.runner import CommandResult, run_command
from quant_app_factory.domain.planner import AppPlan, ArchitecturePlanner
from quant_app_factory.domain.ports import (
    ScaffoldPlan,
    ScaffoldProvider,
    ScaffoldRequestError,
)
from quant_app_factory.domain.spec import AppKind, AppSpec

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
    kind: AppKind
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


class AppFactory:
    """Turn an `AppSpec` into a scaffolded, checked project through a `ScaffoldProvider`."""

    def __init__(
        self,
        scaffolder: ScaffoldProvider,
        *,
        planner: ArchitecturePlanner | None = None,
        runner: Runner = run_command,
        scaffolder_version: str | None = None,
    ) -> None:
        self.scaffolder = scaffolder
        self.planner = planner or ArchitecturePlanner()
        self._runner = runner
        self._scaffolder_version = scaffolder_version or getattr(scaffolder, "version", None)

    def plan(self, spec: AppSpec) -> AppPlan:
        """Return the planner's decision; deterministic, no I/O."""
        return self.planner.plan(spec)

    def preview(self, spec: AppSpec) -> tuple[AppPlan, ScaffoldPlan]:
        """Return the plan and the scaffolder's exact file list; nothing is written."""
        plan = self.plan(spec)
        return plan, self.scaffolder.plan(plan.request)

    def create(self, spec: AppSpec, *, run_checks: bool = True) -> FactoryReport:
        """Create the project and, unless ``run_checks`` is false, run its lifecycle commands."""
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
        return FactoryReport(
            name=spec.name,
            kind=spec.kind,
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
            checks_passed=all(check.ok for check in checks) if run_checks else None,
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
