"""Operator command line for Quant App Factory: preview an app, then create it with approval."""

from __future__ import annotations

import getpass
import re
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

from quant_agent_os.mcp import MCPUnavailableError, run_stdio
from quant_agent_os.runs import RunRecorder, RunStoreError, SqliteRunStore
from quant_agent_os.runtime import ToolExecutor
from quant_agent_os.sdk import Platform
from quant_agent_os.tools import READ_ONLY, PermissionPolicy, ToolRegistry
from quant_app_factory.application.factory import AppFactory, FactoryReport
from quant_app_factory.domain.connections import DATABASE_COMPONENT, Connection
from quant_app_factory.domain.planner import PlanningError
from quant_app_factory.domain.ports import ScaffoldError
from quant_app_factory.domain.spec import (
    BUILT_IN_TEMPLATES,
    RESERVED_ANSWERS,
    AppSpec,
    SpecError,
    load_spec,
)
from quant_app_factory.infrastructure.pytemplate_uv import PytemplateUvScaffolder
from quant_app_factory.infrastructure.settings_file import (
    SettingsError,
    git_identity,
    load_settings,
    remove_value,
    save_settings,
    set_value,
    settings_path,
)
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
    try:
        settings = load_settings()
    except SettingsError as error:
        raise _fail(error) from error
    return AppFactory(
        PytemplateUvScaffolder(),
        identity=lambda: settings.identity.merged(git_identity()),
        connections=lambda: settings.connections,
    )


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
        f"[bold]{spec.name}[/bold] ({spec.target[0]}/{spec.target[1]}) -> {preview.project_path}",
        soft_wrap=True,
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
    check_connection: Annotated[
        bool,
        typer.Option(
            "--check-connection",
            help="After the checks, connect to the database with the variables the spec names.",
        ),
    ] = False,
) -> None:
    """Preview, ask for approval, then create the project and run its checks.

    Your confirmation is the approval: it is recorded for this exact spec and used once.
    With --check-connection, the app's own connection check runs too, receiving only the
    environment variables the spec's settings reference.
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
    if check_connection:
        prompt += ", then connects to the database"
    if not yes and not typer.confirm(prompt, default=False):
        raise typer.Exit(code=1)
    policy = PermissionPolicy(
        granted=READ_ONLY | CREATE_PERMISSIONS, tool_allowlist=frozenset({"create_app"})
    )
    with Platform(
        factory_tools(factory), policy=policy, run_store=SqliteRunStore(runs), actor="cli"
    ) as platform:
        waiting = platform.call(
            "create_app",
            spec=app_spec.model_dump(mode="json"),
            run_checks=checks,
            check_connection=check_connection,
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


IDENTITY_QUESTIONS = frozenset({"description", "author", "email", "python_version", "license"})
_EXPRESSION = re.compile(r"^\{\{\s*(.*?)\s*\}\}$")
_IN_COMPONENTS = re.compile(r"^'([\w-]+)'\s+(not\s+)?in\s+components$")
_COMPARISON = re.compile(r"^(\w+)\s*(==|!=)\s*'([^']*)'$")


def _applies(when: str | None, components: set[str], answers: dict[str, str]) -> bool:
    """Evaluate simple conditions joined by ``and``: ``'x' in components`` and ``name == 'v'``.

    Anything subtler counts as true, so the question is asked and `check_spec` decides.
    """
    if when is None:
        return True
    expression = _EXPRESSION.match(when.strip())
    if expression is None:
        return True
    for clause in expression.group(1).split(" and "):
        clause = clause.strip()
        if member := _IN_COMPONENTS.match(clause):
            if (member.group(1) in components) == bool(member.group(2)):
                return False
        elif compared := _COMPARISON.match(clause):
            name, operator, expected = compared.groups()
            if name not in answers:
                return True
            if (answers[name] == expected) != (operator == "=="):
                return False
        else:
            return True
    return True


def _choose(label: str, options: list[str], default: str) -> str:
    while True:
        value = str(typer.prompt(label, default=default)).strip()
        if value in options:
            return value
        errors.print(f"Choose one of: {', '.join(options)}.")


@app.command()
def new(
    template: Annotated[
        str | None, typer.Option(help="quant-baseline, agent-tool, bundled, or a pack path.")
    ] = None,
    profile: Annotated[str | None, typer.Option(help="The profile to build.")] = None,
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Spec file to write.")] = None,
) -> None:
    """Answer questions until the spec is ready, write it, and offer to create the project.

    The questions come from the template itself; pytemplate-uv checks the result. Secrets are
    asked for as environment-variable names, never values.
    """
    factory = _factory()
    template = template or str(
        typer.prompt(
            "Template (quant-baseline, agent-tool, bundled, or a pack path)",
            default="quant-baseline",
        )
    )
    if template not in BUILT_IN_TEMPLATES:
        template = Path(template).expanduser().resolve().as_posix()
    try:
        catalog = factory.describe_template(template)
    except ScaffoldError as error:
        raise _fail(error) from error
    for item in catalog.profiles:
        console.print(f"  [bold]{item.id}[/bold]: {item.description}", soft_wrap=True)
    profiles = [item.id for item in catalog.profiles]
    profile = profile if profile in profiles else _choose("Profile", profiles, profiles[0])
    described = factory.describe_template(template, profile)
    chosen_profile = described.profiles[0]
    name = str(typer.prompt("Project name (a lowercase slug, such as momo-lab)"))
    description = str(typer.prompt("One-line description"))
    output_dir = Path(str(typer.prompt("Parent directory", default="."))).expanduser().resolve()
    optional = {c.id: c for c in described.components if c.id in chosen_profile.optional_components}
    for component in optional.values():
        console.print(f"  [bold]{component.id}[/bold]: {component.description}", soft_wrap=True)
    picked = str(typer.prompt("Optional components (comma-separated, blank for none)", default=""))
    components = [c.strip() for c in picked.split(",") if c.strip()]
    unknown = sorted(set(components) - set(optional))
    if unknown:
        raise _fail(f"Unknown components: {', '.join(unknown)}.")
    selected = {*chosen_profile.default_components, *components}
    spec: dict[str, Any] = {
        "name": name,
        "template": template,
        "profile": profile,
        "description": description,
        "output_dir": output_dir.as_posix(),
        "components": components,
    }
    answers: dict[str, str] = {}
    effective: dict[str, str] = {}
    connection_name = ""
    saved = load_settings().connections
    if DATABASE_COMPONENT in selected and saved:
        console.print("Saved connections: " + ", ".join(sorted(saved)), soft_wrap=True)
        connection_name = str(
            typer.prompt("Use a saved connection (name, or blank for a new one)", default="")
        ).strip()
        if connection_name and connection_name not in saved:
            raise _fail(f"No saved connection {connection_name!r}.")
        if connection_name:
            spec["connection"] = connection_name
    for question in described.questions:
        if question.name in IDENTITY_QUESTIONS:
            continue
        if connection_name and question.name.startswith("db_") and question.name != "db_query":
            continue
        if not _applies(question.when, selected, effective):
            continue
        default = question.default if question.default and "{{" not in question.default else ""
        hint = f" [{'/'.join(question.choices)}]" if question.choices else ""
        value = str(
            typer.prompt(f"{question.name}: {question.help}{hint}", default=default)
        ).strip()
        if question.choices and value and value not in question.choices:
            raise _fail(f"{question.name} must be one of {', '.join(question.choices)}.")
        effective[question.name] = value or default
        if not value or value == default:
            continue
        if question.name in RESERVED_ANSWERS:
            spec[question.name] = value
        else:
            answers[question.name] = value
    settings: dict[str, str] = {}
    for component in described.components:
        if component.id not in selected:
            continue
        if connection_name and component.id == DATABASE_COMPONENT:
            continue  # the saved connection names its variables
        for setting in component.settings:
            if setting.secret:
                variable = str(
                    typer.prompt(
                        f"{setting.name} is secret. Environment variable that will hold it "
                        "(blank: decide later)",
                        default="",
                    )
                ).strip()
                if variable:
                    settings[setting.name] = f"env:{variable}"
            else:
                value = str(
                    typer.prompt(
                        f"{setting.name}: {setting.description} (blank: later)", default=""
                    )
                ).strip()
                if value:
                    settings[setting.name] = value
    spec["answers"] = answers
    spec["settings"] = settings
    if DATABASE_COMPONENT in selected and not connection_name:
        _offer_to_save_connection(effective, settings)
    check = factory.check_spec(spec)
    if check.spec_toml is None:
        for problem in check.problems:
            errors.print(f"[red]{problem.field}: {problem.message}[/red]")
        raise typer.Exit(code=1)
    target = out or Path(f"{name}.toml")
    if target.exists() and not typer.confirm(f"{target} exists. Replace it?", default=False):
        raise typer.Exit(code=1)
    target.write_text(check.spec_toml, "utf-8")
    console.print(f"Wrote {target}.")
    if not check.ready:
        for problem in check.problems:
            errors.print(f"[yellow]{problem.field}: {problem.message}[/yellow]")
        errors.print(f"Edit {target}, then run `quant-factory plan {target}`.")
        raise typer.Exit(code=1)
    console.print(f"Ready: {len(check.files)} files.")
    if check.unbound_settings:
        console.print(
            "Set at runtime in the app's environment: " + ", ".join(check.unbound_settings),
            soft_wrap=True,
        )
    if typer.confirm(
        "Create it now? It writes files, runs uv, and downloads packages", default=False
    ):
        create(target, yes=True, checks=True, runs=DEFAULT_RUNS, check_connection=False)


def _offer_to_save_connection(answers: dict[str, str], settings: dict[str, str]) -> None:
    """Offer to save the database answers as a named connection in factory.toml."""
    name = str(
        typer.prompt("Save this connection in factory.toml as (blank: don't save)", default="")
    ).strip()
    if not name:
        return
    fields: dict[str, Any] = {"kind": answers.get("db_kind", "sqlite")}
    if fields["kind"] == "postgres":
        port = answers.get("db_port", "")
        fields |= {
            "host": answers.get("db_host") or None,
            "port": int(port) if port.isdigit() else None,
            "user": answers.get("db_user") or None,
            "schema": answers.get("db_schema") or None,
        }
    fields |= {
        "database": answers.get("db_name") or None,
        "read_only": answers.get("db_read_only", "yes") == "yes",
        "url_env": settings.get("DATABASE_URL", "").removeprefix("env:") or None,
        "password_env": settings.get("DATABASE_PASSWORD", "").removeprefix("env:") or None,
    }
    try:
        current = load_settings()
        connection = Connection.model_validate(
            {key: value for key, value in fields.items() if value is not None}
        )
        save_settings(
            current.model_copy(update={"connections": {**current.connections, name: connection}})
        )
    except (SettingsError, ValueError) as error:
        errors.print(f"[yellow]Not saved: {error}[/yellow]")
        return
    console.print(f"Saved connection {name!r} in {settings_path()}.", soft_wrap=True)


settings_app = typer.Typer(help="Show or change factory.toml (identity, connections, templates).")
app.add_typer(settings_app, name="settings")


@settings_app.command("show")
def settings_show() -> None:
    """Print the settings file. It holds no secrets, only environment-variable names."""
    path = settings_path()
    if not path.is_file():
        console.print(f"No settings at {path} yet.")
        return
    console.print(f"# {path}", soft_wrap=True)
    console.print(path.read_text("utf-8"), soft_wrap=True, markup=False)


@settings_app.command("set")
def settings_set(
    key: Annotated[str, typer.Argument(help="identity.FIELD or connections.NAME.FIELD")],
    value: Annotated[str, typer.Argument(help="The value; never a secret.")],
) -> None:
    """Set one field, such as `connections.research-pg.password_env RESEARCH_PG_PASSWORD`."""
    try:
        save_settings(set_value(load_settings(), key, value))
    except SettingsError as error:
        raise _fail(error) from error
    console.print(f"Set {key}.")


@settings_app.command("remove")
def settings_remove(
    key: Annotated[str, typer.Argument(help="A field, or a whole connection or template.")],
) -> None:
    """Remove a field, a connection, or a template."""
    try:
        save_settings(remove_value(load_settings(), key))
    except SettingsError as error:
        raise _fail(error) from error
    console.print(f"Removed {key}.")


MCP_INSTRUCTIONS = (
    "Quant App Factory guides a user to a ready app spec. Call describe_template to learn what "
    "a template asks, ask the user, and call check_spec until it is ready. Then give the user "
    "spec_toml to save and ask them to run `quant-factory create FILE`, which asks for their "
    "approval. Never put a secret in a spec: use env:NAME references."
)


@app.command("serve-mcp")
def serve_mcp(
    runs: Annotated[Path, typer.Option("--runs", help="SQLite run store.")] = DEFAULT_RUNS,
    record: Annotated[
        bool, typer.Option("--record/--no-record", help="Persist every call as a run.")
    ] = True,
) -> None:
    """Serve the read-only guiding tools (describe_template, check_spec, plan_app) over stdio.

    create_app is not served: MCP clients cannot approve it yet, so people create from the
    terminal. Messages go to stderr because stdout carries the protocol.
    """
    tools = [spec for spec in factory_tools(_factory()) if spec.name != "create_app"]
    run_store: SqliteRunStore | None = None
    try:
        if record:
            try:
                run_store = SqliteRunStore(runs)
            except RunStoreError as error:
                errors.print(f"[yellow]warning:[/yellow] {error} Calls will not be recorded.")
        run_stdio(
            ToolExecutor(ToolRegistry(tools), PermissionPolicy()),
            name="quant-factory",
            instructions=MCP_INSTRUCTIONS,
            recorder=RunRecorder(run_store, actor="mcp") if run_store else None,
        )
    except MCPUnavailableError as error:
        raise _fail(error) from error
    finally:
        if run_store is not None:
            run_store.close()


def main() -> None:
    """Run the command-line interface."""
    app()


if __name__ == "__main__":
    main()
