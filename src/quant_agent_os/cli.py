"""Command-line interface for quant-agent-os."""

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from quant_agent_os import __version__
from quant_agent_os.api.keys import SCOPES, ApiKeyError, Scope, add_key
from quant_agent_os.config import get_settings
from quant_agent_os.data import DatasetError, DirectoryDatasetStore
from quant_agent_os.runtime.plan import Plan
from quant_agent_os.tenancy import DEFAULT_TENANT, TenantError, check_tenant

app = typer.Typer(
    help="QuantAgentOS: typed tools, a bounded orchestrator, and evidence-linked reports.",
    no_args_is_help=True,
)
console = Console()
errors = Console(stderr=True, soft_wrap=True)
api_key_app = typer.Typer(help="Manage API keys for the REST interface.", no_args_is_help=True)
app.add_typer(api_key_app, name="api-key")
dataset_app = typer.Typer(help="Add and list datasets that tools read by id.", no_args_is_help=True)
app.add_typer(dataset_app, name="dataset")
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
        help="Datasets directory (default: QUANT_AGENT_OS_DATASETS_DIR or ~/.quant-agent-os).",
    ),
]


def _show_version(value: bool) -> None:
    if value:
        console.print(f"quant-agent-os {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            help="Show the version and exit.",
            callback=_show_version,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """QuantAgentOS command-line interface."""


@app.command()
def info() -> None:
    """Show the active configuration (secrets are reported only as set or unset)."""
    table = Table(title=f"quant-agent-os {__version__}")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="magenta")
    for key, value in get_settings().public_summary().items():
        table.add_row(key, value)
    console.print(table)


@app.command("plan-schema")
def plan_schema() -> None:
    """Print the JSON schema that provider adapters must return for a plan."""
    typer.echo(json.dumps(Plan.model_json_schema(), indent=2))


@api_key_app.command("new")
def api_key_new(
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
            help="Keys file (default: QUANT_AGENT_OS_API_KEYS_FILE or ~/.quant-agent-os)."
        ),
    ] = None,
    tenant: Annotated[
        str, typer.Option(help="The tenant the key acts in; its data only (decision 0013).")
    ] = DEFAULT_TENANT,
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
        key = add_key(path, principal, scopes, tenant=tenant)
    except (ApiKeyError, ValueError) as error:
        errors.print(f"[red]error:[/red] {error}")
        raise typer.Exit(1) from error
    errors.print(
        f"Added a key for {principal} in tenant {tenant} ({', '.join(scopes)}) to {path}. "
        "It is shown once; "
        "store it now. Restart the server to use it."
    )
    typer.echo(key)


def _datasets_dir(directory: Path | None, tenant: str | None) -> Path:
    root = directory or get_settings().datasets_dir
    return root / check_tenant(tenant) if tenant is not None else root


@dataset_app.command("push")
def dataset_push(
    source: Annotated[Path, typer.Argument(help="A CSV file: a header row, then one row each.")],
    dataset_id: Annotated[str, typer.Option("--id", help="The id tools will use.")],
    directory: DatasetsOption = None,
    tenant: DatasetTenantOption = None,
) -> None:
    """Validate a CSV file and add it as a dataset, replacing an earlier one with the same id.

    The first column is an index (dates, say) when its values are not all numbers; every other
    column must be numeric.
    """
    try:
        store = DirectoryDatasetStore(_datasets_dir(directory, tenant))
        info = store.put(dataset_id, source)
    except (DatasetError, TenantError, OSError) as error:
        errors.print(f"[red]error:[/red] {error}")
        raise typer.Exit(1) from error
    console.print(
        f"Added dataset {info.id}: {info.rows} rows, columns {', '.join(info.columns)} "
        f"(sha256 {info.fingerprint[:16]})."
    )


@dataset_app.command("list")
def dataset_list(directory: DatasetsOption = None, tenant: DatasetTenantOption = None) -> None:
    """List the datasets with their rows, columns, and fingerprints."""
    try:
        store = DirectoryDatasetStore(_datasets_dir(directory, tenant))
    except TenantError as error:
        errors.print(f"[red]error:[/red] {error}")
        raise typer.Exit(1) from error
    table = Table(title=f"Datasets in {store.root}")
    for column in ("Id", "Rows", "Columns", "Index range", "sha256"):
        table.add_column(column)
    for dataset_id in store.ids():
        try:
            info = store.get(dataset_id).info()
        except DatasetError as error:
            errors.print(f"[yellow]warning:[/yellow] {error}")
            continue
        span = f"{info.first_index} to {info.last_index}" if info.first_index else ""
        table.add_row(info.id, str(info.rows), ", ".join(info.columns), span, info.fingerprint[:16])
    console.print(table)


def main() -> None:
    """Run the command-line interface."""
    app()


if __name__ == "__main__":
    main()
