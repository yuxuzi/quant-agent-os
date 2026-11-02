"""`quant-platform`: serve every product's toolset from one MCP server."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from quant_agent_os.api import ApiKeyError, ApiUnavailableError, KeyRing, load_keys
from quant_agent_os.api.server import DEFAULT_HOST, DEFAULT_PORT, is_loopback, run_http
from quant_agent_os.approval_desk import ApprovalDesk
from quant_agent_os.config import get_settings
from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.mcp import MCPUnavailableError, run_stdio
from quant_agent_os.mcp.http import KeyVerifier
from quant_agent_os.mcp.oauth import FirstOf, IntrospectionVerifier, protected_resource_routes
from quant_agent_os.runs import RunRecorder, RunStoreError, SqliteRunStore
from quant_agent_os.runtime import ToolExecutor
from quant_agent_os.serving import Tenancy
from quant_agent_os.tenancy import check_tenant
from quant_agent_os.tools import READ_ONLY, Permission, ToolRegistry
from quant_platform.composition import (
    FACTORY_EXTRA,
    TOOLSETS,
    Catalog,
    Toolset,
    available_toolsets,
    compose,
    instructions_for,
)
from quant_platform.http import build_http_app, single_tenant
from quant_platform.tenants import (
    TenantCatalogs,
    TenantConfig,
    TenantsFile,
    TenantsFileError,
    describe,
    load_tenants_file,
    save_tenants,
    separate_approver,
)
from quant_research import ResearchApp, open_research
from quant_research.infrastructure.sqlite_store import StoreError

if TYPE_CHECKING:
    from quant_app_factory import AppFactory

app = typer.Typer(
    help="Quant-Agent-OS platform: every toolset behind one MCP server (stdio or HTTP).",
    no_args_is_help=True,
)
tenant_app = typer.Typer(
    help="Manage the tenants a multi-tenant server serves.", no_args_is_help=True
)
app.add_typer(tenant_app, name="tenant")
console = Console()
errors = Console(stderr=True, soft_wrap=True)
DEFAULT_RUNS = Path.home() / ".quant-agent-os" / "runs.db"
DEFAULT_TENANTS = Path.home() / ".quant-agent-os" / "tenants.toml"

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
TenantsOption = Annotated[
    Path | None,
    typer.Option(
        "--tenants",
        envvar="QUANT_AGENT_OS_TENANTS_FILE",
        help="Tenants file: one catalog per tenant (decision 0013).",
    ),
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
    tenants: TenantsOption = None,
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
    approval_url: Annotated[
        str | None,
        typer.Option(
            envvar="QUANT_AGENT_OS_APPROVAL_URL",
            help="The server's public base URL; turns on the approval page at "
            "/approve/<id>, where an approver key approves a waiting call (decision 0014).",
        ),
    ] = None,
    resource_url: Annotated[
        str | None,
        typer.Option(
            envvar="QUANT_AGENT_OS_RESOURCE_URL",
            help="The public URL of /mcp; needed when the tenants file lists authorization "
            "servers (OAuth tokens must name it as their audience).",
        ),
    ] = None,
) -> None:
    """Serve the toolsets over HTTP: MCP at /mcp and REST at /v1, for callers with an API key.

    Create keys with `quant-agent-os api-key new`. Every call is recorded with the key's
    principal and tenant; the server does not start without a key or a run store. Without
    --tenants it serves one tenant, from --store and --datasets; with a tenants file, each key
    is served its own tenant (decision 0013), and --store, --toolsets, and --datasets are not
    used (--datasets is then the root of the tenants' dataset directories). Permissions come
    from QUANT_AGENT_OS_GRANTED_PERMISSIONS, or per tenant from the tenants file (read-only by
    default). Put TLS and rate limiting in a reverse proxy before binding anything but loopback
    (decision 0012). When the tenants file lists authorization_servers, MCP also accepts their
    OAuth tokens, checked by introspection, and publishes the Protected Resource Metadata.
    With --approval-url, a call that waits for an approval sends the client to the approval
    page; issue approver keys with `api-key new --scope runs:approve`.
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
    whole = _load_tenants_file(tenants) if tenants is not None else None
    configured = whole.tenants if whole is not None else None
    oauth = _oauth(whole, ring, resource_url) if whole is not None else None
    run_store: SqliteRunStore | None = None
    research: ResearchApp | None = None
    catalogs: TenantCatalogs | None = None
    try:
        try:
            run_store = SqliteRunStore(runs or DEFAULT_RUNS)
        except RunStoreError as error:
            errors.print(f"[red]error:[/red] {error}")
            raise typer.Exit(1) from error
        if configured is None:
            catalog, research = _catalog(_toolsets(toolsets), store, datasets)
            tenancy: Tenancy = single_tenant(catalog, run_store, settings.policy())
            instructions, served = catalog.instructions, f"{len(catalog.tools)} tools"
        else:
            wants_factory = any("factory" in (c.toolsets or []) for c in configured.values())
            catalogs = TenantCatalogs(
                configured,
                run_store=run_store,
                datasets_root=datasets or settings.datasets_dir,
                factory=_factory() if wants_factory else None,
            )
            tenancy = catalogs
            instructions = instructions_for(available_toolsets())
            served = f"{sum(not c.disabled for c in configured.values())} tenants"
        if not is_loopback(host):
            errors.print(
                f"[yellow]warning:[/yellow] binding {host}, which other machines can reach; "
                "serve it behind a reverse proxy that terminates TLS and limits request rates."
            )
        desk = (
            ApprovalDesk(
                run_store,
                ring,
                base_url=approval_url,
                separate_approver=separate_approver(configured or {}),
            )
            if approval_url is not None
            else None
        )
        http_app = build_http_app(
            tenancy,
            ring,
            instructions=instructions,
            extra_hosts=allowed_host or [],
            desk=desk,
            **(oauth or {}),
        )
        plural = "" if len(ring) == 1 else "s"
        errors.print(
            f"Serving {served} at http://{host}:{port}/mcp (MCP) and /v1 (REST) to "
            f"{len(ring)} key{plural}."
        )
        run_http(http_app, host=host, port=port)
    except (ApiUnavailableError, MCPUnavailableError) as error:
        errors.print(f"[red]error:[/red] {error}")
        raise typer.Exit(1) from error
    finally:
        if catalogs is not None:
            catalogs.close()
        if run_store is not None:
            run_store.close()
        if research is not None:
            research.close()


def _load_tenants(path: Path) -> dict[str, TenantConfig]:
    return _load_tenants_file(path).tenants


def _load_tenants_file(path: Path) -> TenantsFile:
    try:
        return load_tenants_file(path)
    except TenantsFileError as error:
        errors.print(f"[red]error:[/red] {escape(str(error))}")
        raise typer.Exit(1) from error


def _oauth(whole: TenantsFile, keys: KeyRing, resource_url: str | None) -> dict[str, Any] | None:
    """Return build_http_app's OAuth arguments for the file's authorization servers, if any."""
    if not whole.authorization_servers:
        return None
    if resource_url is None:
        errors.print(
            "[red]error:[/red] the tenants file lists authorization servers; give the public "
            "URL of /mcp with --resource-url."
        )
        raise typer.Exit(2)
    try:
        servers = [entry.resolve() for entry in whole.authorization_servers]
    except TenantsFileError as error:
        errors.print(f"[red]error:[/red] {escape(str(error))}")
        raise typer.Exit(1) from error
    routes, metadata_url = protected_resource_routes(
        resource_url, [server.issuer for server in servers]
    )
    introspection = IntrospectionVerifier(
        servers,
        resource=resource_url,
        tenants_by_issuer=whole.tenants_by_issuer(),
        tenant_claim=whole.tenant_claim,
    )
    return {
        "verifier": FirstOf(KeyVerifier(keys), introspection),
        "resource_metadata_url": metadata_url,
        "metadata_routes": routes,
    }


def _tenants_path(path: Path | None) -> Path:
    return path or DEFAULT_TENANTS


@tenant_app.command("add")
def tenant_add(
    name: Annotated[str, typer.Argument(help="Tenant id: lowercase letters, digits, '-'.")],
    store: StoreOption = None,
    schema: Annotated[
        str | None, typer.Option(help="PostgreSQL schema (default qr_<tenant>).")
    ] = None,
    datasets: Annotated[
        Path | None, typer.Option(help="Datasets directory (default <root>/<tenant>).")
    ] = None,
    toolsets: ToolsetsOption = None,
    grant: Annotated[
        list[Permission] | None,
        typer.Option(help="A permission to grant this tenant (repeat); read-only by default."),
    ] = None,
    issuer: Annotated[
        str | None, typer.Option(help="The OAuth issuer pinned for this tenant's tokens.")
    ] = None,
    file: TenantsOption = None,
) -> None:
    """Add a tenant to the tenants file, or replace its settings.

    On PostgreSQL, ingest its corpus into its own schema:
    `quant-research ingest DIR --store URL --schema qr_<tenant>`.
    """
    path = _tenants_path(file)
    try:
        check_tenant(name)
        whole = load_tenants_file(path) if path.exists() else TenantsFile()
        configured = whole.tenants
        configured[name] = TenantConfig(
            store=store,
            schema_name=schema,
            datasets=datasets,
            toolsets=_toolsets(toolsets) if toolsets else None,
            granted_permissions=READ_ONLY | frozenset(grant or []),
            issuer=issuer,
        )
        save_tenants(path, whole)
    except (TenantsFileError, ValueError) as error:
        errors.print(f"[red]error:[/red] {escape(str(error))}")
        raise typer.Exit(1) from error
    shown = describe(name, configured[name])
    errors.print(f"Tenant {name} saved to {path}; restart serve-http to serve it.")
    if shown["schema"]:
        errors.print(
            f"Ingest its corpus with: quant-research ingest DIR --store URL --schema "
            f"{shown['schema']}"
        )


@tenant_app.command("list")
def tenant_list(file: TenantsOption = None) -> None:
    """List the tenants with their stores, toolsets, and issuers (passwords hidden)."""
    configured = _load_tenants(_tenants_path(file))
    table = Table(title="Tenants")
    for column in ("Tenant", "Store", "Schema", "Toolsets", "Issuer", "Disabled"):
        table.add_column(column)
    for name, config in sorted(configured.items()):
        table.add_row(*describe(name, config).values())
    console.print(table)


@tenant_app.command("disable")
def tenant_disable(
    name: Annotated[str, typer.Argument(help="The tenant to stop serving.")],
    file: TenantsOption = None,
) -> None:
    """Stop serving a tenant at the next restart; its data is kept."""
    path = _tenants_path(file)
    whole = _load_tenants_file(path)
    if name not in whole.tenants:
        errors.print(f"[red]error:[/red] no tenant {name} in {path}.")
        raise typer.Exit(1)
    whole.tenants[name] = whole.tenants[name].model_copy(update={"disabled": True})
    save_tenants(path, whole)
    errors.print(f"Tenant {name} disabled in {path}; restart serve-http to apply it.")


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
