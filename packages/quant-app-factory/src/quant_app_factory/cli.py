"""Operator command line for Quant App Factory: preview an app, then create it with approval."""

from __future__ import annotations

import getpass
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from quant_agent_os.runs import SqliteRunStore
from quant_agent_os.sdk import Platform
from quant_agent_os.tools import READ_ONLY, PermissionPolicy
from quant_app_factory.application.factory import AppFactory, FactoryReport
from quant_app_factory.domain.planner import PlanningError
from quant_app_factory.domain.ports import ScaffoldError
from quant_app_factory.domain.spec import AppSpec, SpecError, load_spec
from quant_app_factory.infrastructure.pytemplate_uv import PytemplateUvScaffolder
from quant_app_factory.tools import CREATE_PERMISSIONS, factory_tools

app = typer.Typer(
    help="Quant App Factory: generate tested, packaged Python applications.", no_args_is_help=True
)
console = Console()
errors = Console(stderr=True, soft_wrap=True)
DEFAULT_RUNS = Path.home() / ".quant-agent-os" / "factory-runs.db"

SpecArgument = Annotated[
    Path, typer.Argument(help="An app spec (TOML): name, kind, description, output_dir, ...")
]


def _fail(error: Exception | str) -> typer.Exit:
    errors.print(f"[red]{error}[/red]")
    return typer.Exit(code=1)


def _factory() -> AppFactory:
    return AppFactory(PytemplateUvScaffolder())


def _load(path: Path) -> AppSpec:
    try:
        return load_spec(path)
    except SpecError as error:
        raise _fail(error) from error


def _preview(factory: AppFactory, spec: AppSpec) -> bool:
    """Print the plan and the exact files; return whether the target directory already exists."""
    try:
        plan, preview = factory.preview(spec)
    except (PlanningError, ScaffoldError) as error:
        raise _fail(error) from error
    console.print(
        f"[bold]{spec.name}[/bold] ({spec.kind}) -> {preview.project_path}", soft_wrap=True
    )
    for reason in plan.reasons:
        console.print(f"  - {reason}", soft_wrap=True)
    components = ", ".join(preview.components) or "none"
    console.print(f"Profile {preview.profile} v{preview.profile_version}; components: {components}")
    console.print(f"Dependencies: {', '.join(preview.dependencies) or 'none'}", soft_wrap=True)
    console.print(f"Files ({len(preview.files)}):")
    for name in preview.files:
        console.print(f"  {name}", soft_wrap=True)
    console.print("Checks: " + "; ".join(preview.lifecycle_commands), soft_wrap=True)
    for note in preview.security_notes:
        console.print(f"[yellow]security:[/yellow] {note}", soft_wrap=True)
    if preview.target_exists:
        errors.print(f"[red]{preview.project_path} already exists.[/red]")
    return preview.target_exists


@app.command()
def plan(spec: SpecArgument) -> None:
    """Preview the project: profile, reasons, dependencies, and the exact files. Writes nothing."""
    if _preview(_factory(), _load(spec)):
        raise typer.Exit(code=1)


@app.command()
def create(
    spec: SpecArgument,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Approve without asking.")] = False,
    checks: Annotated[
        bool, typer.Option("--checks/--no-checks", help="Run the generated project's checks.")
    ] = True,
    runs: Annotated[Path, typer.Option("--runs", help="SQLite run store for the audit.")] = (
        DEFAULT_RUNS
    ),
) -> None:
    """Preview, ask for approval, then create the project and run its checks.

    Your confirmation is the approval: it is recorded for this exact spec and used once.
    """
    factory = _factory()
    app_spec = _load(spec)
    if _preview(factory, app_spec):
        raise typer.Exit(code=1)
    prompt = (
        "Create this project? It writes files, runs uv, and downloads packages"
        if checks
        else "Create this project? It writes files"
    )
    if not yes and not typer.confirm(prompt, default=False):
        raise typer.Exit(code=1)
    policy = PermissionPolicy(
        granted=READ_ONLY | CREATE_PERMISSIONS, tool_allowlist=frozenset({"create_app"})
    )
    with Platform(
        factory_tools(factory), policy=policy, run_store=SqliteRunStore(runs), actor="cli"
    ) as platform:
        waiting = platform.call(
            "create_app", spec=app_spec.model_dump(mode="json"), run_checks=checks
        )
        run_id = waiting.run_id or ""
        if waiting.status != "approval_required":
            raise _fail(f"Expected an approval request, got {waiting.status}: {waiting.error}")
        platform.approve(run_id, approved_by=f"cli:{getpass.getuser()}")
        (record,) = platform.resume(run_id)
    if record.status != "ok" or record.output is None:
        raise _fail(f"create_app {record.status}: {record.error} (run {run_id})")
    report = FactoryReport.model_validate(record.output)
    _print_report(report, run_id, runs)
    if report.checks_passed is False:
        raise typer.Exit(code=1)


def _print_report(report: FactoryReport, run_id: str, runs: Path) -> None:
    console.print(f"Created {report.project_path} with {report.scaffolder}.", soft_wrap=True)
    for check in report.checks:
        mark = "[green]ok[/green]" if check.ok else "[red]failed[/red]"
        console.print(f"  {mark}  {check.command}  ({check.duration_ms / 1000:.1f} s)")
        if not check.ok:
            console.print(check.output_tail, soft_wrap=True, markup=False)
    for warning in report.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}", soft_wrap=True)
    if report.checks_passed is None:
        console.print("Checks skipped.")
    console.print(f"Run {run_id} recorded in {runs}.", soft_wrap=True)


def main() -> None:
    """Run the command-line interface."""
    app()


if __name__ == "__main__":
    main()
