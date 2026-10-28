"""Command-line interface for quant-agent-os."""

import json
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from quant_agent_os import __version__
from quant_agent_os.config import get_settings
from quant_agent_os.runtime.plan import Plan

app = typer.Typer(
    help="QuantAgentOS: typed tools, a bounded orchestrator, and evidence-linked reports.",
    no_args_is_help=True,
)
console = Console()


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


def main() -> None:
    """Run the command-line interface."""
    app()


if __name__ == "__main__":
    main()
