"""`quant-platform`: serve every product's toolset from one MCP server."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from quant_agent_os.config import get_settings
from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.mcp import MCPUnavailableError, run_stdio
from quant_agent_os.runs import RunRecorder, RunStoreError, SqliteRunStore
from quant_agent_os.runtime import ToolExecutor
from quant_agent_os.tools import ToolRegistry
from quant_app_factory.cli import default_factory
from quant_platform.composition import TOOLSETS, Catalog, Toolset, compose
from quant_research import ResearchApp, open_research
from quant_research.infrastructure.sqlite_store import StoreError

app = typer.Typer(
    help="Quant-Agent-OS platform: every toolset behind one MCP server.", no_args_is_help=True
)
console = Console()
errors = Console(stderr=True, soft_wrap=True)
DEFAULT_RUNS = Path.home() / ".quant-agent-os" / "runs.db"
ALL_TOOLSETS = ",".join(TOOLSETS)

ToolsetsOption = Annotated[
    str, typer.Option(help=f"Comma-separated toolsets to serve: {', '.join(TOOLSETS)}.")
]
StoreOption = Annotated[
    str | None,
    typer.Option(help="Research knowledge store (a SQLite path or postgresql:// URL)."),
]
DatasetsOption = Annotated[
    Path | None,
    typer.Option(help="Datasets directory (default: QUANT_AGENT_OS_DATASETS_DIR)."),
]


def _toolsets(value: str) -> list[Toolset]:
    chosen = [item.strip() for item in value.split(",") if item.strip()]
    unknown = sorted(set(chosen) - set(TOOLSETS))
    if unknown or not chosen:
        errors.print(f"[red]error:[/red] choose toolsets from {', '.join(TOOLSETS)}.")
        raise typer.Exit(2)
    return [item for item in TOOLSETS if item in chosen]


def _catalog(
    toolsets: list[Toolset], store: str | None, datasets: Path | None
) -> tuple[Catalog, ResearchApp | None]:
    """Open what the toolsets need and compose them, or exit with an actionable message."""
    research: ResearchApp | None = None
    if "research" in toolsets:
        if store is None:
            errors.print("[red]error:[/red] the research toolset needs --store (ingest first).")
            raise typer.Exit(2)
        try:
            research = open_research(store, read_only=True)
        except StoreError as error:
            errors.print(f"[red]error:[/red] {error}")
            raise typer.Exit(1) from error
    settings = get_settings()
    catalog = compose(
        policy=settings.policy(),
        research=research.research if research else None,
        datasets=(
            DirectoryDatasetStore(datasets or settings.datasets_dir)
            if "analytics" in toolsets
            else None
        ),
        factory=default_factory() if "factory" in toolsets else None,
    )
    return catalog, research


@app.command("serve-mcp")
def serve_mcp(
    toolsets: ToolsetsOption = ALL_TOOLSETS,
    store: StoreOption = None,
    datasets: DatasetsOption = None,
    runs: Annotated[
        Path | None, typer.Option(help="Run store (default ~/.quant-agent-os).")
    ] = None,
    record: Annotated[
        bool, typer.Option("--record/--no-record", help="Persist every call as a run.")
    ] = True,
) -> None:
    """Serve the chosen toolsets' tools, resources, and prompts to an MCP client over stdio.

    The research store opens read-only. Permissions come from
    QUANT_AGENT_OS_GRANTED_PERMISSIONS (read-only by default). Messages go to stderr because
    stdout carries the protocol.
    """
    catalog, research = _catalog(_toolsets(toolsets), store, datasets)
    run_store: SqliteRunStore | None = None
    try:
        if record:
            try:
                run_store = SqliteRunStore(runs or DEFAULT_RUNS)
            except RunStoreError as error:
                errors.print(f"[yellow]warning:[/yellow] {error} Calls will not be recorded.")
        run_stdio(
            ToolExecutor(ToolRegistry(catalog.tools), get_settings().policy()),
            name="quant-agent-os",
            instructions=catalog.instructions,
            recorder=RunRecorder(run_store, actor="mcp") if run_store else None,
            resources=catalog.resources,
            prompts=catalog.prompts,
        )
    except MCPUnavailableError as error:
        errors.print(f"[red]error:[/red] {error}")
        raise typer.Exit(1) from error
    finally:
        if run_store is not None:
            run_store.close()
        if research is not None:
            research.close()


@app.command("catalog")
def catalog_command(
    toolsets: ToolsetsOption = ALL_TOOLSETS,
    store: StoreOption = None,
    datasets: DatasetsOption = None,
) -> None:
    """List what `serve-mcp` would offer: tools, resource templates, and prompts."""
    catalog, research = _catalog(_toolsets(toolsets), store, datasets)
    try:
        table = Table(title="Quant-Agent-OS catalog")
        for column in ("Kind", "Name", "Description"):
            table.add_column(column)
        for spec in catalog.tools:
            table.add_row("tool", spec.name, spec.description)
        for resource in catalog.resources:
            table.add_row("resource", resource.uri_template, resource.description)
        for prompt in catalog.prompts:
            table.add_row("prompt", prompt.name, prompt.description)
        console.print(table)
    finally:
        if research is not None:
            research.close()


def main() -> None:
    """Run the command-line interface."""
    app()


if __name__ == "__main__":
    main()
