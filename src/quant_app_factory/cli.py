"""Quant App Factory's commands, mounted as `qaos app`: preview an app, then create it."""

from __future__ import annotations

import getpass
import re
import shutil
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape

from quant_agent_os.config import get_settings
from quant_agent_os.runs import SqliteRunStore
from quant_agent_os.sdk import Platform
from quant_agent_os.tools import READ_ONLY, Permission, PermissionPolicy
from quant_app_factory.application.factory import AppFactory, FactoryReport
from quant_app_factory.bootstrap import open_factory
from quant_app_factory.domain.connections import (
    DATABASE_COMPONENT,
    DATABASE_SECRETS,
    Connection,
)
from quant_app_factory.domain.planner import BUILT_IN_PACKS, PlanningError
from quant_app_factory.domain.ports import ComponentInfo, ScaffoldError
from quant_app_factory.domain.spec import (
    KIND_TARGETS,
    RESERVED_ANSWERS,
    TEMPLATE_NAME,
    AppSpec,
    SpecError,
    is_template_path,
    load_spec,
    resolve_alias,
)
from quant_app_factory.infrastructure.pack_files import PackError, clone_pack, fork_pack
from quant_app_factory.infrastructure.settings_file import (
    RegisteredTemplate,
    SettingsError,
    load_settings,
    remove_value,
    set_value,
    settings_path,
    update_settings,
)
from quant_app_factory.tools import CHECK_PERMISSIONS, CREATE_PERMISSIONS, factory_tools

app = typer.Typer(
    help="Quant App Factory: generate tested, packaged Python applications.", no_args_is_help=True
)
console = Console()
errors = Console(stderr=True, soft_wrap=True)
RUNS_HELP = "SQLite run store for the audit (default: runs.db in the home)."


def _runs_path(runs: Path | None) -> Path:
    """Return the run store: ``--runs``, or the platform's one run store."""
    return runs if runs is not None else get_settings().runs_file


SpecArgument = Annotated[
    Path, typer.Argument(help="An app spec (TOML): name, kind, description, output_dir, ...")
]


def _fail(error: Exception | str) -> typer.Exit:
    errors.print(f"[red]{escape(str(error))}[/red]")
    return typer.Exit(code=1)


def default_factory() -> AppFactory:
    """Return the factory configured by the settings file, or exit with the settings error."""
    try:
        return open_factory()
    except (ScaffoldError, SettingsError) as error:
        raise _fail(error) from error


def _load(path: Path) -> AppSpec:
    try:
        return load_spec(path)
    except SpecError as error:
        raise _fail(error) from error


def _preview(factory: AppFactory, spec: AppSpec, *, verbose: bool = False) -> bool:
    """Print what would be built; return whether the target directory already exists.

    The summary is the target, file count, components, and checks; ``verbose`` adds the
    planner's reasons, the dependencies, and every file.
    """
    try:
        plan, preview = factory.preview(spec)
    except (PlanningError, ScaffoldError) as error:
        raise _fail(error) from error
    console.print(
        f"[bold]{spec.name}[/bold] ({spec.target[0]}/{spec.target[1]}) -> {preview.project_path}",
        soft_wrap=True,
    )
    if spec.alias_note:
        errors.print(f"[yellow]{spec.alias_note}[/yellow]", soft_wrap=True)
    components = ", ".join(preview.components) or "none"
    checks = ", ".join(_short_command(command) for command in preview.lifecycle_commands)
    console.print(
        f"  {len(preview.files)} files; components: {components}; checks: {checks}",
        soft_wrap=True,
    )
    if verbose:
        for reason in plan.reasons:
            if reason != spec.alias_note:
                console.print(f"  - {reason}", soft_wrap=True)
        console.print(f"  Profile {preview.profile} v{preview.profile_version}")
        console.print(
            f"  Dependencies: {', '.join(preview.dependencies) or 'none'}", soft_wrap=True
        )
        for name in preview.files:
            console.print(f"    {name}", soft_wrap=True)
    for note in preview.security_notes:
        console.print(f"[yellow]security:[/yellow] {note}", soft_wrap=True)
    if preview.target_exists:
        errors.print(f"[red]{preview.project_path} already exists.[/red]")
    return preview.target_exists


VERBOSE_HELP = "Also show the planner's reasons, dependencies, and every file."


def _short_command(command: str) -> str:
    """``uv run ruff format --check .`` -> ``ruff format``: the tool and subcommand only."""
    words: list[str] = []
    for word in command.removeprefix("uv run ").split():
        if word.startswith(("-", ".", "{")) or len(words) == 2:
            break
        words.append(word)
    return " ".join(words) or command


@app.command()
def plan(
    spec: SpecArgument,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help=VERBOSE_HELP)] = False,
) -> None:
    """Preview the project: what it builds, its components, and its checks. Writes nothing."""
    if _preview(default_factory(), _load(spec), verbose=verbose):
        raise typer.Exit(code=1)


@app.command()
def create(
    spec: SpecArgument,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Approve without asking.")] = False,
    checks: Annotated[
        bool, typer.Option("--checks/--no-checks", help="Run the generated project's checks.")
    ] = True,
    runs: Annotated[Path | None, typer.Option("--runs", help=RUNS_HELP)] = None,
    check_connection: Annotated[
        bool,
        typer.Option(
            "--check-connection",
            help="After the checks, connect to the database with the variables the spec names.",
        ),
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help=VERBOSE_HELP)] = False,
) -> None:
    """Preview, ask for approval, then create the project and run its checks.

    Your confirmation is the approval: it is recorded for this exact spec and used once.
    With --check-connection, the app's own connection check runs too, receiving only the
    environment variables the spec's settings reference.
    """
    factory = default_factory()
    app_spec = _load(spec)
    if _preview(factory, app_spec, verbose=verbose):
        raise typer.Exit(code=1)
    pack_digest = factory.pack_digest(app_spec)  # the approval covers the pack just shown
    prompt = "Create it? It writes files" + (", runs uv, and downloads packages" if checks else "")
    _confirm(prompt, yes=yes, check_connection=check_connection)
    report, run_id = _approved_call(
        factory,
        "create_app",
        CREATE_PERMISSIONS,
        runs,
        spec=app_spec.model_dump(mode="json"),
        run_checks=checks,
        check_connection=check_connection,
        expected_pack_digest=pack_digest,
    )
    _print_report(report, run_id, _runs_path(runs))
    if report.checks_passed is False:
        raise typer.Exit(code=1)


@app.command("check")
def check_project(
    spec: SpecArgument,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Approve without asking.")] = False,
    runs: Annotated[Path | None, typer.Option("--runs", help=RUNS_HELP)] = None,
    check_connection: Annotated[
        bool,
        typer.Option("--check-connection", help="Also connect to the database (built-in only)."),
    ] = False,
) -> None:
    """Re-run the checks of the project a spec created, for example after a failed check."""
    factory = default_factory()
    app_spec = _load(spec)
    _confirm(
        "Re-run this project's checks? It runs uv and may download packages",
        yes=yes,
        check_connection=check_connection,
    )
    report, run_id = _approved_call(
        factory,
        "check_app",
        CHECK_PERMISSIONS,
        runs,
        spec=app_spec.model_dump(mode="json"),
        check_connection=check_connection,
    )
    _print_report(report, run_id, _runs_path(runs), verb="Checked")
    if report.checks_passed is False:
        raise typer.Exit(code=1)


def _confirm(prompt: str, *, yes: bool, check_connection: bool) -> None:
    """Ask the operator; their yes (or --yes) becomes the call's single-use approval."""
    if check_connection:
        prompt += ", then connects to the database"
    if not yes and not typer.confirm(prompt, default=False):
        raise typer.Exit(code=1)


def _approved_call(
    factory: AppFactory,
    tool_name: str,
    permissions: frozenset[Permission],
    runs: Path | None,
    **arguments: Any,
) -> tuple[FactoryReport, str]:
    """Call ``tool_name`` once with the operator's recorded approval.

    The call goes through the same approval gate an agent's call does: it waits for an
    approval, the operator's confirmation is recorded as that approval, and the run resumes.
    """
    policy = PermissionPolicy(
        granted=READ_ONLY | permissions, tool_allowlist=frozenset({tool_name})
    )
    with Platform(
        factory_tools(factory),
        policy=policy,
        run_store=SqliteRunStore(_runs_path(runs)),
        actor="cli",
    ) as platform:
        waiting = platform.call(tool_name, **arguments)
        run_id = waiting.run_id or ""
        if waiting.status != "approval_required":
            raise _fail(f"Expected an approval request, got {waiting.status}: {waiting.error}")
        platform.approve(run_id, approved_by=f"cli:{getpass.getuser()}")
        (record,) = platform.resume(run_id)
    if record.status != "ok" or record.output is None:
        raise _fail(f"{tool_name} {record.status}: {record.error} (run {run_id})")
    return FactoryReport.model_validate(record.output), run_id


def _print_report(report: FactoryReport, run_id: str, runs: Path, verb: str = "Created") -> None:
    console.print(f"{verb} {report.project_path} with {report.scaffolder}.", soft_wrap=True)
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


def _resolve_target(
    target: str | None, template: str | None, profile: str | None
) -> tuple[str | None, str | None]:
    """Read ``quant-research`` (a built-in shortcut), ``TEMPLATE/PROFILE``, or a template."""
    if target is None:
        return template, profile
    if target in KIND_TARGETS:
        return KIND_TARGETS[target]
    head, separator, tail = target.partition("/")
    if separator and TEMPLATE_NAME.match(head) and TEMPLATE_NAME.match(tail):
        return head, tail
    return target, profile


def _pick_components(optional: list[ComponentInfo]) -> list[str]:
    """A numbered menu; the answer is numbers or names, comma-separated."""
    if not optional:
        return []
    for number, component in enumerate(optional, 1):
        console.print(
            f"  {number}. [bold]{component.id}[/bold]: {component.description}", soft_wrap=True
        )
    picked = str(typer.prompt("Optional components (numbers or names, blank for none)", default=""))
    chosen = []
    for item in (part.strip() for part in picked.split(",")):
        if item.isdigit() and 1 <= int(item) <= len(optional):
            chosen.append(optional[int(item) - 1].id)
        elif item in {component.id for component in optional}:
            chosen.append(item)
        elif item:
            raise _fail(f"Unknown component {item!r}; choose a number from 1 to {len(optional)}.")
    return list(dict.fromkeys(chosen))


@app.command()
def new(
    target: Annotated[
        str | None,
        typer.Argument(
            help="What to build: quant-research, agent-tool, TEMPLATE/PROFILE, or a template."
        ),
    ] = None,
    name: Annotated[str | None, typer.Argument(help="Project name, a lowercase slug.")] = None,
    template: Annotated[
        str | None, typer.Option(help="quant-baseline, agent-tool, bundled, or a pack path.")
    ] = None,
    profile: Annotated[str | None, typer.Option(help="The profile to build.")] = None,
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Spec file to write.")] = None,
    defaults: Annotated[
        bool,
        typer.Option("--defaults", help="Ask nothing: take every default, then create it."),
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="With --defaults, create without asking.")
    ] = False,
) -> None:
    """Answer the template's questions (or take its defaults), write the spec, and create it.

    `qaos app new quant-research my-lab --defaults` is the one-command start. The
    questions come from the template itself, only those that apply are asked, and
    pytemplate-uv checks the result. Secrets are asked for as variable names, never values.
    """
    factory = default_factory()
    template, profile = _resolve_target(target, template, profile)
    if template is None:
        template = (
            "quant-baseline"
            if defaults
            else str(
                typer.prompt(
                    "Template (quant-baseline, agent-tool, bundled, or a pack path)",
                    default="quant-baseline",
                )
            )
        )
    if is_template_path(template):
        template = Path(template).expanduser().resolve().as_posix()
    try:
        catalog = factory.describe_template(template)
    except ScaffoldError as error:
        raise _fail(error) from error
    profiles = [item.id for item in catalog.profiles]
    if profile is not None:
        profile = resolve_alias(template, profile)[1]
    if profile not in profiles and len(profiles) == 1:
        profile = profiles[0]
    elif profile not in profiles and defaults:
        raise _fail(f"Choose a profile of {template}: {', '.join(profiles)}.")
    elif profile not in profiles:
        for item in catalog.profiles:
            console.print(f"  [bold]{item.id}[/bold]: {item.description}", soft_wrap=True)
        profile = _choose("Profile", profiles, profiles[0])
    described = factory.describe_template(template, profile)
    chosen_profile = described.profiles[0]
    console.print(f"{template}/{profile}: {chosen_profile.description}", soft_wrap=True)
    if name is None and defaults:
        raise _fail("Name the project: qaos app new TARGET NAME --defaults.")
    name = name or str(typer.prompt("Project name (a lowercase slug, such as momo-lab)"))
    question_defaults = {q.name: q.default for q in described.questions if q.default}
    if defaults:
        description = question_defaults.get("description") or name
        output_dir = Path.cwd()
        components: list[str] = []
    else:
        description = str(
            typer.prompt("One-line description", default=question_defaults.get("description", ""))
        )
        output_dir = Path(str(typer.prompt("Parent directory", default="."))).expanduser().resolve()
        components = _pick_components(
            [c for c in described.components if c.id in chosen_profile.optional_components]
        )
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
    for question in [] if defaults else described.questions:
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
    for component in [] if defaults else described.components:
        if component.id not in selected:
            continue
        if connection_name and component.id == DATABASE_COMPONENT:
            continue  # the saved connection names its variables
        for setting in component.settings:
            if component.id == DATABASE_COMPONENT and setting.name not in DATABASE_SECRETS.get(
                effective.get("db_kind", ""), (setting.name,)
            ):
                continue  # for example, no password for a SQLite or DuckDB file
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
    target_file = out or Path(f"{name}.toml")
    if target_file.exists() and (
        defaults or not typer.confirm(f"{target_file} exists. Replace it?", default=False)
    ):
        raise _fail(f"{target_file} exists; choose another name or --out.")
    target_file.write_text(check.spec_toml, "utf-8")
    console.print(f"Wrote {target_file}.")
    if not check.ready:
        for problem in check.problems:
            errors.print(f"[yellow]{problem.field}: {problem.message}[/yellow]")
        errors.print(f"Edit {target_file}, then run `qaos app plan {target_file}`.")
        raise typer.Exit(code=1)
    if not defaults:  # with --defaults, create's summary follows at once
        console.print(f"Ready: {len(check.files)} files.")
    for setting_name, value in sorted(settings.items()):
        if value.startswith("env:"):
            console.print(f"The app reads {setting_name} from ${value.removeprefix('env:')}.")
    if defaults or typer.confirm(
        "Create it now? It writes files, runs uv, and downloads packages", default=False
    ):
        create(target_file, yes=yes or not defaults, checks=True, runs=None, check_connection=False)


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
            "sslmode": answers.get("db_sslmode") or None,
        }
    fields |= {
        "database": answers.get("db_name") or None,
        "read_only": answers.get("db_read_only", "yes") == "yes",
        "url_env": settings.get("DATABASE_URL", "").removeprefix("env:") or None,
        "password_env": settings.get("DATABASE_PASSWORD", "").removeprefix("env:") or None,
    }
    try:
        connection = Connection.model_validate(
            {key: value for key, value in fields.items() if value is not None}
        )
        update_settings(
            lambda current: current.model_copy(
                update={"connections": {**current.connections, name: connection}}
            )
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
        update_settings(lambda settings: set_value(settings, key, value))
    except SettingsError as error:
        raise _fail(error) from error
    console.print(f"Set {key}.")


@settings_app.command("remove")
def settings_remove(
    key: Annotated[str, typer.Argument(help="A field, or a whole connection or template.")],
) -> None:
    """Remove a field, a connection, or a template."""
    try:
        update_settings(lambda settings: remove_value(settings, key))
    except SettingsError as error:
        raise _fail(error) from error
    console.print(f"Removed {key}.")


template_app = typer.Typer(help="List, fork, check, and register template packs.")
app.add_typer(template_app, name="template")


def _register(name: str, entry: RegisteredTemplate) -> None:
    try:
        update_settings(
            lambda current: current.model_copy(
                update={"templates": {**current.templates, name: entry}}
            )
        )
    except SettingsError as error:
        raise _fail(error) from error
    console.print(f'Registered template {name!r}: use template = "{name}" in a spec.')


def _print_check(factory: AppFactory, template: str) -> bool:
    result = factory.check_template(template)
    if result.problem:
        errors.print(f"[red]{result.problem}[/red]")
    for item in result.profiles:
        if item.ok:
            console.print(f"  [green]ok[/green]  {item.profile} ({item.files} files)")
        else:
            console.print(f"  [red]failed[/red]  {item.profile}: {item.problem}", soft_wrap=True)
    return result.ok


@template_app.command("list")
def template_list() -> None:
    """List the built-in templates and the ones registered in factory.toml."""
    factory = default_factory()
    for name in [*sorted(BUILT_IN_PACKS), "bundled"]:
        profiles = ", ".join(p.id for p in factory.describe_template(name).profiles)
        console.print(f"[bold]{name}[/bold] (built-in): {profiles}", soft_wrap=True)
    for name, entry in sorted(load_settings().templates.items()):
        pinned = f" at {entry.ref}" if entry.ref else ""
        console.print(
            f"[bold]{name}[/bold]: {entry.source}{pinned} -> {entry.path}", soft_wrap=True
        )


@template_app.command("fork")
def template_fork(
    source: Annotated[
        str, typer.Argument(help="A built-in profile, such as quant-baseline/quant-research.")
    ],
    destination: Annotated[Path, typer.Argument(help="New pack directory.")],
    name: Annotated[str | None, typer.Option(help="Pack id and template name.")] = None,
    profile_id: Annotated[str | None, typer.Option(help="Rename the profile.")] = None,
    register: Annotated[
        bool, typer.Option("--register/--no-register", help="Register it in factory.toml.")
    ] = True,
) -> None:
    """Copy a built-in profile into your own pack, check it, and register it.

    Edit the copy by hand or with an AI assistant: add questions to questions.yml, files to the
    template, or components; then run `qaos app template check`.
    """
    pack_name, _, profile = source.partition("/")
    if pack_name not in BUILT_IN_PACKS or not profile:
        raise _fail(f"Fork a built-in profile: {', '.join(sorted(BUILT_IN_PACKS))}/<profile>.")
    if resolve_alias(pack_name, profile)[1] != profile:
        profile = resolve_alias(pack_name, profile)[1]
        errors.print(f"[yellow]{source} is now {pack_name}/{profile}; forking that.[/yellow]")
        source = f"{pack_name}/{profile}"
    pack_id = name or destination.name
    try:
        forked = fork_pack(
            BUILT_IN_PACKS[pack_name], profile, destination, pack_id=pack_id, profile_id=profile_id
        )
    except PackError as error:
        raise _fail(error) from error
    console.print(f"Forked {source} into {forked}.", soft_wrap=True)
    factory = default_factory()
    if not _print_check(factory, forked.resolve().as_posix()):
        raise typer.Exit(code=1)
    if register:
        _register(
            pack_id,
            RegisteredTemplate(source=f"fork of {source}", path=forked.resolve().as_posix()),
        )


@template_app.command("check")
def template_check(
    template: Annotated[str, typer.Argument(help="A pack directory or a template name.")],
) -> None:
    """Load a pack and plan each of its profiles with default answers. Writes nothing."""
    if is_template_path(template) or Path(template).is_dir():
        template = Path(template).expanduser().resolve().as_posix()
    if not _print_check(default_factory(), template):
        raise typer.Exit(code=1)


@template_app.command("add")
def template_add(
    source: Annotated[str, typer.Argument(help="A pack directory, gh:org/repo, or a git URL.")],
    name: Annotated[str, typer.Option(help="The name specs will use.")],
    ref: Annotated[
        str | None, typer.Option(help="Git tag or branch to pin (required for git sources).")
    ] = None,
) -> None:
    """Register a pack. A git pack is fetched once at --ref and cached, so plans stay offline."""
    local = Path(source).expanduser()
    if local.is_dir() and ref is None:
        path = local.resolve()
        cached = False
    else:
        if ref is None:
            raise _fail("Pin a git pack with --ref (a tag or branch).")
        path = settings_path().parent / "packs" / name / ref
        try:
            clone_pack(source, ref, path)
        except PackError as error:
            raise _fail(error) from error
        cached = True
    factory = default_factory()
    if not _print_check(factory, path.as_posix()):
        if cached:
            shutil.rmtree(path, ignore_errors=True)
        raise typer.Exit(code=1)
    _register(name, RegisteredTemplate(source=source, ref=ref, path=path.as_posix()))
