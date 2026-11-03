"""What a server offers each tenant (decision 0013), shared by the REST and HTTP MCP interfaces.

A `Surface` is one tenant's executor (its registry and policy), resources, prompts, run
store, and the toolset of each tool (for usage events, decision 0015). A `Tenancy` returns the
surface for a caller's tenant, or None when that tenant is not served here. The tenant always
comes from the caller's credential, never from the request.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from quant_agent_os.runs.store import RunStore
from quant_agent_os.runtime.executor import ToolExecutor
from quant_agent_os.tenancy import DEFAULT_TENANT
from quant_agent_os.tools.catalog import PromptSpec, ResourceSpec


@dataclass(frozen=True)
class Surface:
    """One tenant's tools (through its executor), resources, prompts, and run store."""

    executor: ToolExecutor
    resources: list[ResourceSpec] = field(default_factory=list)
    prompts: list[PromptSpec] = field(default_factory=list)
    run_store: RunStore | None = None
    toolsets: Mapping[str, str] = field(default_factory=dict)


class Tenancy(Protocol):
    """Finds the surface a tenant is served."""

    def surface(self, tenant: str) -> Surface | None:
        """Return ``tenant``'s surface, or None when it is not served here."""
        ...


class SingleTenant:
    """A server for one tenant: credentials of any other tenant reach nothing."""

    def __init__(self, surface: Surface, tenant: str = DEFAULT_TENANT) -> None:
        self._surface = surface
        self.tenant = tenant

    def surface(self, tenant: str) -> Surface | None:
        """Return the surface for this server's tenant only."""
        return self._surface if tenant == self.tenant else None
