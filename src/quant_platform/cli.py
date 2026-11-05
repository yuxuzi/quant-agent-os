"""`qaos`: the one command. It mounts each product's commands and serves their toolsets."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.table import Table

from quant_agent_os import __version__
from quant_agent_os.api import ApiKeyError, ApiUnavailableError, KeyRing, load_keys
from quant_agent_os.api.server import DEFAULT_HOST, DEFAULT_PORT, is_loopback, run_http
from quant_agent_os.cli import data_app, keys_app, show_runs
from quant_agent_os.config import ConfigFileError, get_settings
from quant_agent_os.mcp import MCPUnavailableError, run_stdio
from quant_agent_os.runs import RunRecorder, RunStoreError, SqliteRunStore
from quant_agent_os.runtime import ToolExecutor
from quant_agent_os.tools import ToolRegistry
from quant_app_factory.cli import app as factory_app
from quant_platform.cli_common import (
    DatasetsOption,
    StoreOption,
    ToolsetsOption,
    console,
    errors,
    open_catalog,
    parse_toolsets,
    warn_about_permissions,
)
from quant_platform.hosted.cli import hosted_app
from quant_platform.http import build_http_app, single_tenant
from quant_platform.onboarding import connect, doctor, init
from quant_research import ResearchApp
from quant_research.cli import EmbedderOption, ModelOption
from quant_research.cli import app as research_app

app = typer.Typer(
    help="Quant-Agent-OS: cited research and computed metrics for AI clients, over MCP.",
    no_args_is_help=True,
)
app.add_typer(research_app)
app.add_typer(data_app, name="data")
app.add_typer(keys_app, name="keys")
app.add_typer(factory_app, name="app", help="App Factory: generate tested Python applications.")
app.add_typer(hosted_app, name="hosted")
app.command("runs")(show_runs)
app.command()(connect)
app.command()(doctor)
app.command()(init)


def _show_version(value: bool) -> None:
    if value:
        console.print(f"qaos {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    _version: Annotated[
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
    """Quant-Agent-OS: cited research and computed metrics for AI clients, over MCP."""
    try:
        get_settings()
    except (ConfigFileError, ValidationError) as error:
        errors.print(f"[red]error:[/red] {error}")
        raise typer.Exit(1) from error


def _serve_stdio(
    toolsets: str | None,
    store: str | None,
    datasets: Path | None,
    runs: Path | None,
    record: bool,
    embedder: EmbedderOption,
    embedding_model: str | None,
) -> None:
    settings = get_settings()
    catalog, research = open_catalog(
        parse_toolsets(toolsets),
        store,
        datasets,
        chosen=toolsets is not None,
        embedder=embedder,
        embedding_model=embedding_model,
    )
    run_store: SqliteRunStore | None = None
    try:
        if record:
            try:
                run_store = SqliteRunStore(runs or settings.runs_file)
            except RunStoreError as error:
                errors.print(f"[yellow]warning:[/yellow] {error} Calls will not be recorded.")
        warn_about_permissions(catalog.tools, settings.policy())
        run_stdio(
            ToolExecutor(ToolRegistry(catalog.tools), settings.policy()),
            name="quant-agent-os",
            instructions=catalog.instructions,
            recorder=(
                RunRecorder(run_store, actor="mcp", toolsets=catalog.toolsets)
                if run_store
                else None
            ),
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


def _serve_http(
    toolsets: str | None,
    store: str | None,
    datasets: Path | None,
    runs: Path | None,
    keys: Path | None,
    host: str,
    port: int,
    allowed_host: list[str],
    embedder: EmbedderOption,
    embedding_model: str | None,
) -> None:
    settings = get_settings()
    try:
        ring = KeyRing(load_keys(keys or settings.api_keys_file))
    except ApiKeyError as error:
        errors.print(f"[red]error:[/red] {error}")
        raise typer.Exit(1) from error
    if not len(ring):
        errors.print(
            "[red]error:[/red] the keys file has no enabled key; add one with `qaos keys new`."
        )
        raise typer.Exit(1)
    run_store: SqliteRunStore | None = None
    research: ResearchApp | None = None
    try:
        try:
            run_store = SqliteRunStore(runs or settings.runs_file)
        except RunStoreError as error:
            errors.print(f"[red]error:[/red] {error}")
            raise typer.Exit(1) from error
        catalog, research = open_catalog(
            parse_toolsets(toolsets),
            store,
            datasets,
            chosen=toolsets is not None,
            embedder=embedder,
            embedding_model=embedding_model,
        )
        warn_about_permissions(catalog.tools, settings.policy())
        if not is_loopback(host):
            errors.print(
                f"[yellow]warning:[/yellow] binding {host}, which other machines can reach; "
                "serve it behind a reverse proxy that terminates TLS and limits request rates."
            )
        http_app = build_http_app(
            single_tenant(catalog, run_store, settings.policy()),
            ring,
            instructions=catalog.instructions,
            extra_hosts=allowed_host,
        )
        plural = "" if len(ring) == 1 else "s"
        errors.print(
            f"Serving {len(catalog.tools)} tools at http://{host}:{port}/mcp (MCP) and /v1 (REST) "
            f"to {len(ring)} key{plural}."
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


@app.command()
def serve(
    http: Annotated[
        bool,
        typer.Option("--http", help="Serve over HTTP, for callers with an API key, not stdio."),
    ] = False,
    toolsets: ToolsetsOption = None,
    store: StoreOption = None,
    datasets: DatasetsOption = None,
    runs: Annotated[
        Path | None, typer.Option(help="Run store (default: runs.db in the home).")
    ] = None,
    embedder: EmbedderOption = None,
    embedding_model: ModelOption = None,
    record: Annotated[
        bool,
        typer.Option("--record/--no-record", help="Persist every call as a run (stdio only)."),
    ] = True,
    keys: Annotated[
        Path | None,
        typer.Option(help="With --http: API keys file (default: api-keys.toml in the home)."),
    ] = None,
    host: Annotated[
        str, typer.Option(help="With --http: address to bind; loopback by default.")
    ] = DEFAULT_HOST,
    port: Annotated[int, typer.Option(help="With --http: port to listen on.")] = DEFAULT_PORT,
    allowed_host: Annotated[
        list[str] | None,
        typer.Option(
            help="With --http: a public host name clients use (repeat); loopback is always allowed."
        ),
    ] = None,
) -> None:
    """Serve the toolsets' tools, resources, and prompts to an AI client.

    Over stdio by default, for a client that launches the server; messages go to stderr because
    stdout carries the protocol. With --http, it serves MCP at /mcp and REST at /v1 to callers
    with a key from `qaos keys new`. Every call is recorded with the key's principal, and the
    server does not start without a key or a run store. Put TLS and rate limiting in a reverse
    proxy before binding anything but loopback (decision 0012). Serving many tenants is `qaos
    hosted serve`. The research store opens read-only. Permissions come from
    QUANT_AGENT_OS_GRANTED_PERMISSIONS (read-only by default).
    """
    if http:
        if not record:
            errors.print("[red]error:[/red] --http always records; remove --no-record.")
            raise typer.Exit(2)
        _serve_http(
            toolsets,
            store,
            datasets,
            runs,
            keys,
            host,
            port,
            allowed_host or [],
            embedder,
            embedding_model,
        )
        return
    if keys is not None or host != DEFAULT_HOST or port != DEFAULT_PORT or allowed_host:
        errors.print("[red]error:[/red] --keys, --host, --port, and --allowed-host need --http.")
        raise typer.Exit(2)
    _serve_stdio(toolsets, store, datasets, runs, record, embedder, embedding_model)


@app.command("catalog")
def catalog_command(
    toolsets: ToolsetsOption = None,
    store: StoreOption = None,
    datasets: DatasetsOption = None,
) -> None:
    """List what `qaos serve` would offer: tools, resource templates, and prompts."""
    catalog, research = open_catalog(
        parse_toolsets(toolsets), store, datasets, chosen=toolsets is not None
    )
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
