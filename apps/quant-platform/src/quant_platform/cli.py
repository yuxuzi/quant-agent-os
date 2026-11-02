"""`quant-platform`: serve every product's toolset from one MCP server."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from quant_agent_os.api import ApiKeyError, ApiUnavailableError, KeyRing, load_keys
from quant_agent_os.api.server import DEFAULT_HOST, DEFAULT_PORT, is_loopback, run_http
from quant_agent_os.config import get_settings
from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.mcp import MCPUnavailableError, run_stdio
from quant_agent_os.runs import RunRecorder, RunStoreError, SqliteRunStore
from quant_agent_os.runtime import ToolExecutor
from quant_agent_os.tools import ToolRegistry
from quant_platform.composition import (
    FACTORY_EXTRA,
    TOOLSETS,
    Catalog,
    Toolset,
    available_toolsets,
    compose,
)
from quant_platform.http import build_http_app
from quant_research import ResearchApp, open_research
from quant_research.infrastructure.sqlite_store import StoreError

if TYPE_CHECKING:
    from quant_app_factory import AppFactory

app = typer.Typer(
    help="Quant-Agent-OS platform: every toolset behind one MCP server (stdio or HTTP).",
    no_args_is_help=True,
)
console = Console()
errors = Console(stderr=True, soft_wrap=True)
DEFAULT_RUNS = Path.home() / ".quant-agent-os" / "runs.db"

ToolsetsOption = Annotated[
    str | None,
    typer.Option(
        help=f"Comma-separated toolsets to serve: {', '.join(TOOLSETS)} (default: every "
        "installed one)."
    ),
]
StoreOption = Annotated[
    str | None,
    typer.Option(help="Research knowledge store (a SQLite path or postgresql:// URL)."),
]
DatasetsOption = Annotated[
    Path | None,
    typer.Option(help="Datasets directory (default: QUANT_AGENT_OS_DATASETS_DIR)."),
]


def _toolsets(value: str | None) -> list[Toolset]:
    if value is None:
        return available_toolsets()
    chosen = [item.strip() for item in value.split(",") if item.strip()]
    unknown = sorted(set(chosen) - set(TOOLSETS))
    if unknown or not chosen:
        errors.print(f"[red]error:[/red] choose toolsets from {', '.join(TOOLSETS)}.")
        raise typer.Exit(2)
    if "factory" in chosen and "factory" not in available_toolsets():
        errors.print(
            f"[red]error:[/red] the factory toolset is not installed; {escape(FACTORY_EXTRA)}."
        )
        raise typer.Exit(2)
    return [item for item in TOOLSETS if item in chosen]


def _factory() -> AppFactory:
    from quant_app_factory.cli import default_factory

    return default_factory()


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
        factory=_factory() if "factory" in toolsets else None,
    )
    return catalog, research


@app.command("serve-mcp")
def serve_mcp(
    toolsets: ToolsetsOption = None,
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


@app.command("serve-http")
def serve_http(
    toolsets: ToolsetsOption = None,
    store: StoreOption = None,
    datasets: DatasetsOption = None,
    keys: Annotated[
        Path | None, typer.Option(help="API keys file (default: QUANT_AGENT_OS_API_KEYS_FILE).")
    ] = None,
    host: Annotated[str, typer.Option(help="Address to bind; loopback by default.")] = (
        DEFAULT_HOST
    ),
    port: Annotated[int, typer.Option(help="Port to listen on.")] = DEFAULT_PORT,
    allowed_host: Annotated[
        list[str] | None,
        typer.Option(help="A public host name clients use (repeat); loopback is always allowed."),
    ] = None,
    runs: Annotated[
        Path | None, typer.Option(help="Run store (default ~/.quant-agent-os).")
    ] = None,
) -> None:
    """Serve the toolsets over HTTP: MCP at /mcp and REST at /v1, for callers with an API key.

    Create keys with `quant-agent-os api-key new`. Every call is recorded with the key's
    principal; the server does not start without a key or a run store. Permissions come from
    QUANT_AGENT_OS_GRANTED_PERMISSIONS (read-only by default). Put TLS and rate limiting in a
    reverse proxy before binding anything but loopback (decision 0012).
    """
    settings = get_settings()
    try:
        ring = KeyRing(load_keys(keys or settings.api_keys_file))
    except ApiKeyError as error:
        errors.print(f"[red]error:[/red] {error}")
        raise typer.Exit(1) from error
    if not len(ring):
        errors.print(
            "[red]error:[/red] the keys file has no enabled key; add one with "
            "`quant-agent-os api-key new`."
        )
        raise typer.Exit(1)
    catalog, research = _catalog(_toolsets(toolsets), store, datasets)
    run_store: SqliteRunStore | None = None
    try:
        try:
            run_store = SqliteRunStore(runs or DEFAULT_RUNS)
        except RunStoreError as error:
            errors.print(f"[red]error:[/red] {error}")
            raise typer.Exit(1) from error
        if not is_loopback(host):
            errors.print(
                f"[yellow]warning:[/yellow] binding {host}, which other machines can reach; "
                "serve it behind a reverse proxy that terminates TLS and limits request rates."
            )
        http_app = build_http_app(
            catalog, ring, run_store, policy=settings.policy(), extra_hosts=allowed_host or []
        )
        plural = "" if len(ring) == 1 else "s"
        errors.print(
            f"Serving {len(catalog.tools)} tools at http://{host}:{port}/mcp (MCP) and /v1 "
            f"(REST) to {len(ring)} key{plural}."
        )
        run_http(http_app, host=host, port=port)
    except (ApiUnavailableError, MCPUnavailableError) as error:
        errors.print(f"[red]error:[/red] {error}")
        raise typer.Exit(1) from error
    finally:
        if run_store is not None:
            run_store.close()
        if research is not None:
            research.close()


@app.command("catalog")
def catalog_command(
    toolsets: ToolsetsOption = None,
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
