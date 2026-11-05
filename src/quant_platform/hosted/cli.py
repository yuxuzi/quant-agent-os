"""`qaos hosted`: many tenants, plans, OAuth, and usage (decisions 0013 to 0015, 0017).

Importing this builds only the commands. Each one imports the hosted modules it uses, so the
default `serve` never loads them.
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.markup import escape
from rich.table import Table

from quant_agent_os.api import ApiKeyError, ApiUnavailableError, KeyRing
from quant_agent_os.api.server import DEFAULT_HOST, DEFAULT_PORT, is_loopback, run_http
from quant_agent_os.config import get_settings, home_dir
from quant_agent_os.mcp import MCPUnavailableError
from quant_agent_os.runs import RunStoreError, SqliteRunStore, UsageGrouping, summarize
from quant_agent_os.serving import Tenancy
from quant_agent_os.tenancy import check_tenant
from quant_agent_os.tools import READ_ONLY, Permission
from quant_platform.cli_common import (
    DatasetsOption,
    StoreOption,
    ToolsetsOption,
    _factory,
    console,
    errors,
    open_catalog,
    parse_toolsets,
)
from quant_platform.composition import available_toolsets, instructions_for
from quant_platform.http import build_http_app, single_tenant
from quant_research import ResearchApp

if TYPE_CHECKING:
    from quant_platform.hosted.tenants import TenantCatalogs, TenantsFile

hosted_app = typer.Typer(
    help="For a vendor hosting many customers: tenants, plans, OAuth, and usage.",
    no_args_is_help=True,
)
tenant_app = typer.Typer(
    help="Manage the tenants a multi-tenant server serves.", no_args_is_help=True
)
hosted_app.add_typer(tenant_app, name="tenant")
usage_app = typer.Typer(
    help="Read what each tenant used from the run store (decision 0015).", no_args_is_help=True
)
hosted_app.add_typer(usage_app, name="usage")

TenantsOption = Annotated[
    Path | None,
    typer.Option(
        "--tenants",
        envvar="QUANT_AGENT_OS_TENANTS_FILE",
        help="Tenants file: one catalog per tenant (decision 0013).",
    ),
]


@hosted_app.command("serve")
def serve_hosted(
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
        Path | None, typer.Option(help="Run store (default: runs.db in the home).")
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
    """Serve many tenants over HTTP, with plans, OAuth, and the approval page if configured.

    Create keys with `qaos keys new`. Every call is recorded with the key's
    principal and tenant; the server does not start without a key or a run store. Without
    --tenants it serves one tenant, from --store and --datasets; with a tenants file, each key
    is served its own tenant (decision 0013), and --store, --toolsets, and --datasets are not
    used (--datasets is then the root of the tenants' dataset directories). Permissions come
    from QUANT_AGENT_OS_GRANTED_PERMISSIONS, or per tenant from the tenants file (read-only by
    default). Put TLS and rate limiting in a reverse proxy before binding anything but loopback
    (decision 0012). When the tenants file lists authorization_servers, MCP also accepts their
    OAuth tokens, checked by introspection, and publishes the Protected Resource Metadata.
    With --approval-url, a call that waits for an approval sends the client to the approval
    page; issue approver keys with `qaos keys new --scope runs:approve`.
    """
    from quant_platform.hosted.approval_desk import ApprovalDesk, approval_gates
    from quant_platform.hosted.approval_page import approval_routes
    from quant_platform.hosted.tenants import TenantCatalogs, entitled_toolsets, separate_approver

    settings = get_settings()
    try:
        ring = KeyRing.from_file(keys or settings.api_keys_file)
    except ApiKeyError as error:
        errors.print(f"[red]error:[/red] {error}")
        raise typer.Exit(1) from error
    if not len(ring):
        errors.print(
            "[red]error:[/red] the keys file has no enabled key; add one with `qaos keys new`."
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
            run_store = SqliteRunStore(runs or get_settings().runs_file)
        except RunStoreError as error:
            errors.print(f"[red]error:[/red] {error}")
            raise typer.Exit(1) from error
        if configured is None:
            catalog, research = open_catalog(
                parse_toolsets(toolsets), store, datasets, chosen=toolsets is not None
            )
            tenancy: Tenancy = single_tenant(catalog, run_store, settings.policy())
            instructions, served = catalog.instructions, f"{len(catalog.tools)} tools"
        else:
            assert whole is not None
            wants_factory = any(
                "factory" in (entitled_toolsets(c, whole.plans) or []) for c in configured.values()
            )
            catalogs = TenantCatalogs(
                configured,
                run_store=run_store,
                datasets_root=datasets or settings.datasets_dir,
                factory=_factory() if wants_factory else None,
                plans=whole.plans,
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
            gate_for=approval_gates(desk) if desk is not None else None,
            extra_routes=approval_routes(desk) if desk is not None else None,
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


def _load_tenants_file(path: Path) -> TenantsFile:
    from quant_platform.hosted.tenants import TenantsFileError, load_tenants_file

    try:
        return load_tenants_file(path)
    except TenantsFileError as error:
        errors.print(f"[red]error:[/red] {escape(str(error))}")
        raise typer.Exit(1) from error


def _oauth(whole: TenantsFile, keys: KeyRing, resource_url: str | None) -> dict[str, Any] | None:
    """Return build_http_app's OAuth arguments for the file's authorization servers, if any."""
    from quant_agent_os.mcp.http import KeyVerifier
    from quant_platform.hosted.oauth import (
        FirstOf,
        IntrospectionVerifier,
        protected_resource_routes,
    )
    from quant_platform.hosted.tenants import TenantsFileError

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
    return path or home_dir() / "tenants.toml"


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
    plan: Annotated[
        str | None, typer.Option(help="A plan in the tenants file (no limits if unset).")
    ] = None,
    file: TenantsOption = None,
) -> None:
    """Add a tenant to the tenants file, or replace its settings.

    On PostgreSQL, ingest its corpus into its own schema:
    `qaos ingest DIR --store URL --schema qr_<tenant>`.
    """
    from quant_platform.hosted.tenants import (
        TenantConfig,
        TenantsFile,
        TenantsFileError,
        describe,
        load_tenants_file,
        save_tenants,
    )

    path = _tenants_path(file)
    try:
        check_tenant(name)
        whole = load_tenants_file(path) if path.exists() else TenantsFile()
        configured = whole.tenants
        configured[name] = TenantConfig(
            store=store,
            schema_name=schema,
            datasets=datasets,
            toolsets=parse_toolsets(toolsets) if toolsets else None,
            plan=plan,
            granted_permissions=READ_ONLY | frozenset(grant or []),
            issuer=issuer,
        )
        whole.check_plans()
        save_tenants(path, whole)
    except (TenantsFileError, ValueError) as error:
        errors.print(f"[red]error:[/red] {escape(str(error))}")
        raise typer.Exit(1) from error
    shown = describe(name, configured[name])
    errors.print(f"Tenant {name} saved to {path}; restart the server to serve it.")
    if shown["schema"]:
        errors.print(
            f"Ingest its corpus with: qaos ingest DIR --store URL --schema {shown['schema']}"
        )


@tenant_app.command("list")
def tenant_list(file: TenantsOption = None) -> None:
    """List the tenants with their stores, toolsets, and issuers (passwords hidden)."""
    from quant_platform.hosted.tenants import describe

    whole = _load_tenants_file(_tenants_path(file))
    table = Table(title="Tenants")
    for column in ("Tenant", "Store", "Schema", "Toolsets", "Plan", "Issuer", "Disabled"):
        table.add_column(column)
    for name, config in sorted(whole.tenants.items()):
        table.add_row(*describe(name, config, whole.plans).values())
    console.print(table)


@tenant_app.command("disable")
def tenant_disable(
    name: Annotated[str, typer.Argument(help="The tenant to stop serving.")],
    file: TenantsOption = None,
) -> None:
    """Stop serving a tenant at the next restart; its data is kept."""
    from quant_platform.hosted.tenants import save_tenants

    path = _tenants_path(file)
    whole = _load_tenants_file(path)
    if name not in whole.tenants:
        errors.print(f"[red]error:[/red] no tenant {name} in {path}.")
        raise typer.Exit(1)
    whole.tenants[name] = whole.tenants[name].model_copy(update={"disabled": True})
    save_tenants(path, whole)
    errors.print(f"Tenant {name} disabled in {path}; restart the server to apply it.")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@usage_app.command("export")
def usage_export(
    tenant: Annotated[
        str | None, typer.Option(help="Only this tenant's usage (default: every tenant).")
    ] = None,
    start: Annotated[
        datetime | None,
        typer.Option(
            "--from",
            formats=["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"],
            help="First instant, UTC (default: the start of this month).",
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
    by: Annotated[
        str | None,
        typer.Option(help="Total the calls per tenant and 'day' or 'tool' (default: each call)."),
    ] = None,
    output_format: Annotated[
        str, typer.Option("--format", help="Output format: 'csv' or 'jsonl'.")
    ] = "csv",
    runs: Annotated[
        Path | None, typer.Option(help="Run store (default: runs.db in the home).")
    ] = None,
) -> None:
    """Write usage events, or their totals, to stdout: an operator command, not an agent tool.

    Usage holds sizes and statuses, never arguments or outputs. A call is billable when it
    ended ok, error, or timeout (decision 0015).
    """
    if by not in (None, "day", "tool"):
        errors.print(f"[red]error:[/red] --by is 'day' or 'tool', not {by!r}.")
        raise typer.Exit(2)
    if output_format not in ("csv", "jsonl"):
        errors.print(f"[red]error:[/red] --format is 'csv' or 'jsonl', not {output_format!r}.")
        raise typer.Exit(2)
    now = datetime.now(UTC)
    first = _utc(start) if start else now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last = _utc(end) if end else now
    path = runs or get_settings().runs_file
    if not path.is_file():
        errors.print(f"[red]error:[/red] no run store at {path}.")
        raise typer.Exit(1)
    try:
        with SqliteRunStore(path) as store:
            events = store.list_usage(first, last, tenant=tenant)
    except RunStoreError as error:
        errors.print(f"[red]error:[/red] {error}")
        raise typer.Exit(1) from error
    grouping: UsageGrouping | None = "day" if by == "day" else "tool" if by == "tool" else None
    rows = [
        item.model_dump(mode="json")
        for item in (summarize(events, grouping) if grouping else events)
    ]
    if output_format == "jsonl":
        for row in rows:
            sys.stdout.write(json.dumps(row, sort_keys=True) + "\n")
    elif rows:
        writer = csv.DictWriter(sys.stdout, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    errors.print(f"{len(events)} calls from {first.isoformat()} to {last.isoformat()}.")
