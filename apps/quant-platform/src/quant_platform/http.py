"""The platform's HTTP app (decisions 0012, 0013): MCP at /mcp and REST at /v1 on one port."""

from __future__ import annotations

from typing import TYPE_CHECKING

from quant_agent_os.api import KeyRing, build_app
from quant_agent_os.api.approval_page import approval_routes
from quant_agent_os.approval_desk import ApprovalDesk
from quant_agent_os.mcp.http import KeyVerifier, add_mcp_route
from quant_agent_os.runs import RunStore
from quant_agent_os.runtime import ToolExecutor
from quant_agent_os.serving import SingleTenant, Surface, Tenancy
from quant_agent_os.tools import PermissionPolicy, ToolRegistry
from quant_platform.composition import Catalog

if TYPE_CHECKING:
    from fastapi import FastAPI
    from mcp.server.auth.provider import TokenVerifier
    from starlette.routing import Route


def single_tenant(catalog: Catalog, run_store: RunStore, policy: PermissionPolicy) -> Tenancy:
    """Return a one-tenant server for ``catalog`` (the default tenant), recording in
    ``run_store``, which is also its approval ledger."""
    executor = ToolExecutor(ToolRegistry(catalog.tools), policy, approvals=run_store)
    return SingleTenant(Surface(executor, catalog.resources, catalog.prompts, run_store))


def build_http_app(
    tenancy: Tenancy,
    keys: KeyRing,
    *,
    instructions: str,
    verifier: TokenVerifier | None = None,
    extra_hosts: list[str] | None = None,
    resource_metadata_url: str | None = None,
    metadata_routes: list[Route] | None = None,
    desk: ApprovalDesk | None = None,
) -> FastAPI:
    """Return one app serving each caller its tenant over REST (0011) and MCP (0012, 0013).

    REST takes API keys; MCP takes ``verifier``'s credentials (API keys by default, or OAuth
    tokens too), and ``metadata_routes`` publish the OAuth Protected Resource Metadata that
    ``resource_metadata_url`` points to. With ``desk``, a call that waits for an approval sends
    the client to the approval page, served here too (decision 0014). Raises `ValueError`
    without an enabled key.
    """
    app = build_app(None, keys, None, title="quant-agent-os", tenancy=tenancy)
    app.router.routes.extend(metadata_routes or [])
    if desk is not None:
        app.router.routes.extend(approval_routes(desk))
    add_mcp_route(
        app,
        tenancy,
        name="quant-agent-os",
        verifier=verifier or KeyVerifier(keys),
        instructions=instructions,
        extra_hosts=extra_hosts,
        resource_metadata_url=resource_metadata_url,
        desk=desk,
    )
    return app
