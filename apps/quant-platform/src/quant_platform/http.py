"""The platform's HTTP app (decision 0012): MCP at /mcp and REST at /v1 on one port."""

from __future__ import annotations

from typing import TYPE_CHECKING

from quant_agent_os.api import KeyRing, build_app
from quant_agent_os.mcp.http import KeyVerifier, add_mcp_route
from quant_agent_os.runs import RunStore
from quant_agent_os.runtime import ToolExecutor
from quant_agent_os.serving import SingleTenant, Surface
from quant_agent_os.tools import PermissionPolicy, ToolRegistry
from quant_platform.composition import Catalog

if TYPE_CHECKING:
    from fastapi import FastAPI


def build_http_app(
    catalog: Catalog,
    keys: KeyRing,
    run_store: RunStore,
    *,
    policy: PermissionPolicy,
    extra_hosts: list[str] | None = None,
) -> FastAPI:
    """Return one app serving ``catalog`` as REST (decision 0011) and MCP (decision 0012).

    Both interfaces use the same keys, policy, and run store, which is also the approval
    ledger. Raises `ValueError` without an enabled key.
    """
    registry = ToolRegistry(catalog.tools)
    executor = ToolExecutor(registry, policy, approvals=run_store)
    tenancy = SingleTenant(Surface(executor, catalog.resources, catalog.prompts, run_store))
    app = build_app(None, keys, None, title="quant-agent-os", tenancy=tenancy)
    add_mcp_route(
        app,
        tenancy,
        name="quant-agent-os",
        verifier=KeyVerifier(keys),
        instructions=catalog.instructions,
        extra_hosts=extra_hosts,
    )
    return app
