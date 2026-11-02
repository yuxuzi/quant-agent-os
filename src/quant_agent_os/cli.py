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
from quant_agent_os.runtime.plan import Plan

app = typer.Typer(
    help="QuantAgentOS: typed tools, a bounded orchestrator, and evidence-linked reports.",
    no_args_is_help=True,
)
console = Console()
errors = Console(stderr=True, soft_wrap=True)
api_key_app = typer.Typer(help="Manage API keys for the REST interface.", no_args_is_help=True)
app.add_typer(api_key_app, name="api-key")


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
        key = add_key(path, principal, scopes)
    except (ApiKeyError, ValueError) as error:
        errors.print(f"[red]error:[/red] {error}")
        raise typer.Exit(1) from error
    errors.print(
        f"Added a key for {principal} ({', '.join(scopes)}) to {path}. It is shown once; "
        "store it now. Restart the server to use it."
    )
    typer.echo(key)


def main() -> None:
    """Run the command-line interface."""
    app()


if __name__ == "__main__":
    main()
