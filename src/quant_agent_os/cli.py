"""Core's commands: datasets, API keys, and the run audit. `qaos` mounts them."""

import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from typer.core import TyperGroup

from quant_agent_os.api.keys import SCOPES, ApiKeyError, Scope, add_key
from quant_agent_os.config import get_settings
from quant_agent_os.data import DatasetError, DirectoryDatasetStore
from quant_agent_os.runs import AuditEvent, RunStoreError, SqliteRunStore, audit_lines
from quant_agent_os.tenancy import DEFAULT_TENANT, TenantError, check_tenant

console = Console()
errors = Console(stderr=True, soft_wrap=True)
keys_app = typer.Typer(help="Manage API keys for the HTTP server.", no_args_is_help=True)
data_app = typer.Typer(help="Add and list datasets that tools read by id.", no_args_is_help=True)


class _RunsGroup(TyperGroup):
    """`qaos runs` and `qaos runs <id>` mean `show`, beside the named subcommands."""

    default_command = "show"

    def parse_args(self, ctx: Any, args: list[str]) -> list[str]:
        if not args or (args[0] not in self.commands and args[0] != "--help"):
            args = [self.default_command, *args]
        return super().parse_args(ctx, args)


runs_app = typer.Typer(cls=_RunsGroup, help="See, export, and prune the recorded runs.")
DatasetTenantOption = Annotated[
    str | None,
    typer.Option(
        "--tenant",
        help="A tenant on a multi-tenant server: its datasets are in <dir>/<tenant>.",
    ),
]
DatasetsOption = Annotated[
    Path | None,
    typer.Option(
        "--dir",
        help="Datasets directory (default: QUANT_AGENT_OS_DATASETS_DIR, or datasets/ in the home).",
    ),
]
RunsOption = Annotated[
    Path | None,
    typer.Option("--runs", help="SQLite run store (default: runs.db in the home)."),
]


@keys_app.command("new")
def key_new(
    principal: Annotated[
        str, typer.Option(help="Who the key belongs to; recorded on every run it makes.")
    ],
    scope: Annotated[
        list[str],
        typer.Option(help=f"Repeat for each scope: {', '.join(SCOPES)}."),
    ],
    keys: Annotated[
        Path | None,
        typer.Option(
            help="Keys file (default: QUANT_AGENT_OS_API_KEYS_FILE, or api-keys.toml in the home)."
        ),
    ] = None,
    tenant: Annotated[
        str, typer.Option(help="The tenant the key acts in; its data only (decision 0013).")
    ] = DEFAULT_TENANT,
    expires_in_days: Annotated[
        int | None,
        typer.Option(min=1, help="Stop accepting the key after this many days. Default: never."),
    ] = None,
    group: Annotated[
        list[str] | None,
        typer.Option(help="Repeat for each group an access file gives rights to (decision 0021)."),
    ] = None,
) -> None:
    """Create an API key: print it once and append only its hash to the keys file."""
    unknown = sorted(set(scope) - set(SCOPES))
    if unknown:
        errors.print(
            f"[red]error:[/red] unknown scope {', '.join(unknown)}; use {', '.join(SCOPES)}."
        )
        raise typer.Exit(2)
    path = keys or get_settings().api_keys_file
    scopes: list[Scope] = [item for item in SCOPES if item in scope]
    try:
        expires = datetime.now(UTC) + timedelta(days=expires_in_days) if expires_in_days else None
        key = add_key(path, principal, scopes, tenant=tenant, expires=expires, groups=group or [])
    except (ApiKeyError, ValueError) as error:
        errors.print(f"[red]error:[/red] {escape(str(error))}")
        raise typer.Exit(1) from error
    errors.print(
        f"Added a key for {principal} in tenant {tenant} ({', '.join(scopes)}) to {path}. "
        "It is shown once; store it now. A running server accepts it at once."
    )
    typer.echo(key)


def _datasets_dir(directory: Path | None, tenant: str | None) -> Path:
    root = directory or get_settings().datasets_dir
    return root / check_tenant(tenant) if tenant is not None else root


@data_app.command("add")
def data_add(
    source: Annotated[Path, typer.Argument(help="A CSV or Parquet file with one row per date.")],
    dataset_id: Annotated[
        str | None, typer.Option("--id", help="The id tools will use (default: the file's name).")
    ] = None,
    index: Annotated[
        str | None, typer.Option("--index", help="The column holding the dates or row labels.")
    ] = None,
    directory: DatasetsOption = None,
    tenant: DatasetTenantOption = None,
) -> None:
    """Validate a file and add it as a dataset, replacing an earlier one with the same id.

    The first column is the index when it is named date, datetime, time, or period, holds dates
    like 20240102 or 202401, or is not all numbers; --index names any other column. Every other
    column must be numeric; blank and NaN cells are kept as missing values. A Parquet file needs
    the parquet extra, and is stored as CSV.
    """
    try:
        store = DirectoryDatasetStore(_datasets_dir(directory, tenant))
        info = store.put(dataset_id or source.stem, source, index_column=index)
    except (DatasetError, TenantError, OSError) as error:
        errors.print(f"[red]error:[/red] {escape(str(error))}")
        raise typer.Exit(1) from error
    console.print(
        f"Added dataset {info.id}: {info.rows} rows, columns {', '.join(info.columns)} "
        f"(sha256 {info.fingerprint[:16]})."
    )
    if info.periods_per_year is not None:
        console.print(f"Its dates show {info.periods_per_year} periods per year.")
    else:
        errors.print(
            "[yellow]warning:[/yellow] no dates showing a frequency, so analytics tools need "
            "periods_per_year, and this dataset cannot be aligned with others. Name the date "
            "column with --index if it has one."
        )
    if info.missing_cells:
        console.print(f"{info.missing_cells} cells are missing; tools leave those rows out.")


@data_app.command("list")
def data_list(directory: DatasetsOption = None, tenant: DatasetTenantOption = None) -> None:
    """List the datasets with their rows, columns, and fingerprints."""
    try:
        store = DirectoryDatasetStore(_datasets_dir(directory, tenant))
    except TenantError as error:
        errors.print(f"[red]error:[/red] {escape(str(error))}")
        raise typer.Exit(1) from error
    table = Table(title=f"Datasets in {store.root}")
    for column in ("Id", "Rows", "Columns", "Index range", "sha256"):
        table.add_column(column)
    for dataset_id in store.ids():
        try:
            info = store.get(dataset_id).info()
        except DatasetError as error:
            errors.print(f"[yellow]warning:[/yellow] {escape(str(error))}")
            continue
        span = f"{info.first_index} to {info.last_index}" if info.first_index else ""
        table.add_row(info.id, str(info.rows), ", ".join(info.columns), span, info.fingerprint[:16])
    console.print(table)


@runs_app.command("show")
def show_runs(
    run_id: Annotated[str | None, typer.Argument(help="A run id to show in full.")] = None,
    runs: RunsOption = None,
    limit: Annotated[int, typer.Option(help="How many recent runs to list.")] = 20,
) -> None:
    """List recent runs, or show one run's tool calls and audit trail."""
    path = runs or get_settings().runs_file
    if not path.is_file():
        console.print(f"No runs recorded at {path} yet.")
        return
    try:
        run_store = SqliteRunStore(path)
    except RunStoreError as error:
        errors.print(f"[red]error:[/red] {escape(str(error))}")
        raise typer.Exit(1) from error
    try:
        if run_id is None:
            for run in run_store.list_runs(limit):
                snapshot = run.data_versions.get("corpus_snapshot", "-")
                console.print(
                    f"{run.run_id}  {run.started_at:%Y-%m-%d %H:%M:%S}  {run.actor:<4}  "
                    f"{run.status:<9}  {snapshot}  {run.goal}",
                    soft_wrap=True,  # run ids must stay whole to be copied
                )
            return
        stored = run_store.get_run(run_id)
        if stored is None:
            errors.print(f"[red]error:[/red] no run {run_id} in {path}.")
            raise typer.Exit(1)
        run = stored.run
        console.print(
            f"Run {run.run_id} ({run.kind}, {run.status}) by {run.actor}"
            f"{f' ({run.principal})' if run.principal else ''}: {run.goal}\n"
            f"Platform {run.platform_version}, registry {run.registry_digest[:16]}, "
            f"data {run.data_versions or 'none'}.",
            soft_wrap=True,
        )
        for call in stored.calls:
            console.print(
                f"  call {call.call_id}  {call.tool}  {call.status}  "
                f"input {(call.input_sha256 or '-')[:16]}  {call.duration_ms:.1f} ms",
                soft_wrap=True,
            )
        for event in stored.events:
            console.print(
                f"  {event.at:%H:%M:%S}  {event.event}  {event.subject}  {event.details}",
                soft_wrap=True,
            )
    finally:
        run_store.close()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _existing_run_store(runs: Path | None) -> SqliteRunStore:
    path = runs or get_settings().runs_file
    if not path.is_file():
        errors.print(f"[red]error:[/red] no run store at {path}.")
        raise typer.Exit(1)
    try:
        return SqliteRunStore(path)
    except RunStoreError as error:
        errors.print(f"[red]error:[/red] {escape(str(error))}")
        raise typer.Exit(1) from error


@runs_app.command("export")
def export_runs(
    start: Annotated[
        datetime | None,
        typer.Option(
            "--from",
            formats=["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"],
            help="First instant, UTC (default: the first record).",
        ),
    ] = None,
    end: Annotated[
        datetime | None,
        typer.Option(
            "--to",
            formats=["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"],
            help="End instant, UTC and not included (default: now).",
        ),
    ] = None,
    output_format: Annotated[
        str, typer.Option("--format", help="Output format: 'jsonl', the only one so far.")
    ] = "jsonl",
    runs: RunsOption = None,
) -> None:
    """Write the audit trail to stdout: who called what, on which data, and with what result.

    It holds hashes and statuses, never a call's arguments or output.
    """
    if output_format != "jsonl":
        errors.print(f"[red]error:[/red] --format is 'jsonl', not {output_format!r}.")
        raise typer.Exit(2)
    first = _utc(start) if start else datetime.min.replace(tzinfo=UTC)
    last = _utc(end) if end else datetime.now(UTC)
    with _existing_run_store(runs) as store:
        count = 0
        for line in audit_lines(store, first, last):
            sys.stdout.write(json.dumps(line, sort_keys=True) + "\n")
            count += 1
    errors.print(f"{count} records.")


@runs_app.command("prune")
def prune_runs(
    older_than: Annotated[
        str, typer.Option("--older-than", help="Remove runs started this long ago, such as 90d.")
    ],
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask for confirmation.")] = False,
    runs: RunsOption = None,
) -> None:
    """Remove ended runs, with their calls and retrieved text. Nothing else ever removes them.

    Audit events and usage events stay, and the removal adds an audit event.
    """
    match = re.fullmatch(r"([1-9]\d*)d", older_than)
    if match is None:
        errors.print(f"[red]error:[/red] --older-than is a number of days, like 90d: {older_than}.")
        raise typer.Exit(2)
    before = datetime.now(UTC) - timedelta(days=int(match.group(1)))
    with _existing_run_store(runs) as store:
        count = store.prunable_runs(before)
        if count == 0:
            console.print(f"No ended runs started before {before:%Y-%m-%d}.")
            return
        if not yes:
            typer.confirm(
                f"Permanently remove {count} runs started before {before:%Y-%m-%d}?", abort=True
            )
        pruned = store.prune_runs(
            before,
            AuditEvent(
                run_id="",
                actor="cli",
                event="runs.pruned",
                subject=before.isoformat(),
                details={"older_than": older_than, "runs": count},
            ),
        )
    console.print(f"Removed {pruned.runs} runs and {pruned.calls} calls. Audit events stay.")
    if not pruned.settled:
        errors.print(
            "[yellow]warning:[/yellow] the server is busy, so removed text can stay in the "
            "write-ahead log until it next checkpoints. Stop the server to be sure."
        )
