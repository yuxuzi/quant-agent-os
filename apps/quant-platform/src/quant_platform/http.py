"""The platform's HTTP app (decision 0012): MCP at /mcp and REST at /v1 on one port."""

from __future__ import annotations

from typing import TYPE_CHECKING

from quant_agent_os.api import KeyRing, build_app
from quant_agent_os.mcp.http import add_mcp_route
from quant_agent_os.runs import RunStore
from quant_agent_os.runtime import ToolExecutor
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
    app = build_app(registry, keys, run_store, policy=policy, title="quant-agent-os")
    add_mcp_route(
        app,
        ToolExecutor(registry, policy, approvals=run_store),
        name="quant-agent-os",
        keys=keys,
        run_store=run_store,
        instructions=catalog.instructions,
        resources=catalog.resources,
        prompts=catalog.prompts,
        extra_hosts=extra_hosts,
    )
    return app
