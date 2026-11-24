"""What a server offers each tenant (decision 0013), shared by the REST and HTTP MCP interfaces.

A `Surface` is one tenant's executor (its registry and policy), resources, prompts, run
store, and the toolset of each tool (for usage events, decision 0015). A `Tenancy` returns the
surface for a caller's tenant, or None when that tenant is not served here. The tenant always
comes from the caller's credential, never from the request.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from quant_agent_os.runs.store import RunStore
from quant_agent_os.runtime.executor import ToolExecutor
from quant_agent_os.tenancy import DEFAULT_TENANT
from quant_agent_os.tools.catalog import PromptSpec, ResourceSpec, ViewSpec


@dataclass(frozen=True)
class Surface:
    """One tenant's tools (through its executor), resources, prompts, views, and run store."""

    executor: ToolExecutor
    resources: list[ResourceSpec] = field(default_factory=list)
    prompts: list[PromptSpec] = field(default_factory=list)
    run_store: RunStore | None = None
    toolsets: Mapping[str, str] = field(default_factory=dict)
    views: list[ViewSpec] = field(default_factory=list)
    # Named reads of the stores this surface was composed with, such as its knowledge store and
    # its datasets. Each must return normally when its store can be read (see `readiness`).
    checks: Mapping[str, Callable[[], object]] = field(default_factory=dict)

    def readiness(self) -> dict[str, Callable[[], object]]:
        """Return the reads `/readyz` makes: the run store's, then ``checks``."""
        probes: dict[str, Callable[[], object]] = {}
        store = self.run_store
        if store is not None:
            probes["runs"] = lambda: store.list_runs(1)
        probes.update(self.checks)
        return probes


class Tenancy(Protocol):
    """Finds the surface a tenant is served."""

    def surface(self, tenant: str) -> Surface | None:
        """Return ``tenant``'s surface, or None when it is not served here."""
        ...

    def readiness(self) -> dict[str, Callable[[], object]]:
        """Return the named reads that show every surface served here can answer."""
        ...


class SingleTenant:
    """A server for one tenant: credentials of any other tenant reach nothing."""

    def __init__(self, surface: Surface, tenant: str = DEFAULT_TENANT) -> None:
        self._surface = surface
        self.tenant = tenant

    def surface(self, tenant: str) -> Surface | None:
        """Return the surface for this server's tenant only."""
        return self._surface if tenant == self.tenant else None

    def readiness(self) -> dict[str, Callable[[], object]]:
        """Return the reads of this server's one surface."""
        return self._surface.readiness()
