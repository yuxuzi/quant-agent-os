"""Many tenants on one server (decision 0013): a tenants file and a catalog per tenant.

The tenants file (TOML) gives each tenant its knowledge store, datasets, toolsets, and
permissions. `TenantCatalogs` composes a tenant's catalog the first time one of its callers
arrives, and serves it as that tenant's `Surface`. A tenant's data never reaches another
tenant's surface: each has its own stores, and the run store filters reads by tenant.

    [plans.team]                                 # what a plan allows (decision 0015)
    toolsets = ["research", "analytics"]
    calls_per_minute = 120
    monthly_calls = 50_000

    [tenants.acme]
    store = "postgresql://qaos_reader@db/qaos"   # schema qr_acme by default
    plan = "team"
    toolsets = ["research"]                      # narrows the plan, never widens it
    issuer = "https://login.acme.example"        # the identity provider pinned for its tokens

    [[authorization_servers]]                    # OAuth for remote MCP clients (optional)
    issuer = "https://login.acme.example"
    introspection_url = "https://login.acme.example/oauth2/introspect"
    client_id = "qaos"
    client_secret_env = "ACME_INTROSPECTION_SECRET"   # the secret stays in the environment
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
import threading
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.runs import RunStore
from quant_agent_os.runtime import ToolExecutor
from quant_agent_os.serving import Surface
from quant_agent_os.tenancy import check_tenant
from quant_agent_os.tools import READ_ONLY, Permission, PermissionPolicy, ToolRegistry
from quant_platform.composition import Toolset, available_toolsets, compose
from quant_platform.hosted.approval_desk import four_eyes_except_local
from quant_platform.hosted.limits import PlanLimits
from quant_platform.hosted.oauth import DEFAULT_TENANT_CLAIM, AuthorizationServer
from quant_research import ResearchApp, open_research
from quant_research.infrastructure.postgres_store import is_postgres_url
from quant_research.infrastructure.sqlite_store import StoreError

if TYPE_CHECKING:
    from quant_app_factory import AppFactory

logger = logging.getLogger(__name__)
PLAN_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


class TenantsFileError(ValueError):
    """Raised when a tenants file is missing or invalid."""


class Plan(BaseModel):
    """What a plan allows its tenants (decision 0015); an unset limit is no limit.

    ``toolsets`` bounds what a tenant may be served. ``calls_per_minute`` and ``monthly_calls``
    are checked before a call starts; ``max_output_bytes`` caps every tool's output limit.
    """

    model_config = ConfigDict(extra="forbid")

    toolsets: list[Toolset] | None = None
    calls_per_minute: int | None = Field(default=None, gt=0)
    monthly_calls: int | None = Field(default=None, gt=0)
    max_output_bytes: int | None = Field(default=None, gt=0)
    packs: list[str] | None = None

    @model_validator(mode="after")
    def _no_packs_yet(self) -> Plan:
        if self.packs is not None:
            raise ValueError("Knowledge packs are not available yet; remove 'packs' from the plan.")
        return self


class TenantConfig(BaseModel):
    """What one tenant is served, and where its data lives."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    store: str | None = Field(default=None, description="SQLite path or postgresql:// URL")
    schema_name: str | None = Field(default=None, alias="schema")
    datasets: Path | None = None
    toolsets: list[Toolset] | None = None
    plan: str | None = None
    granted_permissions: frozenset[Permission] = READ_ONLY
    preapproved_permissions: frozenset[Permission] = frozenset()
    issuer: str | None = None
    separate_approver: bool | None = None
    disabled: bool = False


class ServerEntry(BaseModel):
    """An authorization server in the tenants file; its secret is read from the environment."""

    model_config = ConfigDict(extra="forbid")

    issuer: str
    introspection_url: str
    client_id: str
    client_secret_env: str = Field(pattern=r"^[A-Z_][A-Z0-9_]*$")

    def resolve(self) -> AuthorizationServer:
        """Return the server with its secret, or raise `TenantsFileError` if it is unset."""
        secret = os.environ.get(self.client_secret_env)
        if not secret:
            raise TenantsFileError(
                f"Set {self.client_secret_env} to the introspection secret for {self.issuer}."
            )
        return AuthorizationServer(
            issuer=self.issuer,
            introspection_url=self.introspection_url,
            client_id=self.client_id,
            client_secret=SecretStr(secret),
        )


class TenantsFile(BaseModel):
    """The whole tenants file: tenants, and the authorization servers their tokens come from."""

    model_config = ConfigDict(extra="forbid")

    plans: dict[str, Plan] = Field(default_factory=dict)
    tenants: dict[str, TenantConfig] = Field(default_factory=dict)
    authorization_servers: list[ServerEntry] = Field(default_factory=list)
    tenant_claim: str = Field(default=DEFAULT_TENANT_CLAIM, pattern=r"^[A-Za-z_][\w:./-]{0,63}$")

    def check_plans(self) -> None:
        """Raise `ValueError` for a bad plan name, an unknown plan, or a tenant widening one."""
        for name in self.plans:
            if not PLAN_NAME.fullmatch(name):
                raise ValueError(
                    f"Plan name '{name}' must be lowercase letters, digits, '-', or '_'."
                )
        for tenant, config in self.tenants.items():
            if config.plan is None:
                continue
            plan = self.plans.get(config.plan)
            if plan is None:
                raise ValueError(f"Tenant {tenant}'s plan '{config.plan}' is not among the plans.")
            if config.toolsets is not None and plan.toolsets is not None:
                extra = sorted(set(config.toolsets) - set(plan.toolsets))
                if extra:
                    raise ValueError(
                        f"Tenant {tenant} lists {', '.join(extra)}, which its plan "
                        f"'{config.plan}' does not include; a tenant narrows its plan, never "
                        "widens it."
                    )

    def tenants_by_issuer(self) -> dict[str, set[str]]:
        """Return each issuer's pinned tenants (enabled ones only)."""
        pinned: dict[str, set[str]] = {}
        for name, config in self.tenants.items():
            if config.issuer and not config.disabled:
                pinned.setdefault(config.issuer, set()).add(name)
        return pinned


def schema_for(tenant: str) -> str:
    """Return the default PostgreSQL schema of ``tenant``'s knowledge store."""
    return "qr_" + tenant.replace("-", "_")


def load_tenants_file(path: Path) -> TenantsFile:
    """Read a tenants file; raise `TenantsFileError` naming the problem."""
    try:
        loaded = TenantsFile.model_validate(tomllib.loads(path.read_text("utf-8")))
        for name in loaded.tenants:
            check_tenant(name)
        loaded.check_plans()
    except FileNotFoundError as error:
        raise TenantsFileError(
            f"No tenants file at {path}; add a tenant with `quant-platform tenant add`."
        ) from error
    except (OSError, tomllib.TOMLDecodeError, ValidationError, ValueError) as error:
        raise TenantsFileError(f"Invalid tenants file {path}: {error}") from error
    issuers = {server.issuer for server in loaded.authorization_servers}
    for name, config in loaded.tenants.items():
        if config.issuer and issuers and config.issuer not in issuers:
            raise TenantsFileError(
                f"Invalid tenants file {path}: tenant {name}'s issuer {config.issuer} is not "
                "among the authorization_servers."
            )
    return loaded


def load_tenants(path: Path) -> dict[str, TenantConfig]:
    """Read a tenants file's tenants; raise `TenantsFileError` naming the problem."""
    return load_tenants_file(path).tenants


def dump_tenants(tenants: dict[str, TenantConfig] | TenantsFile) -> str:
    """Return a tenants file. Strings are written as JSON strings, which are valid TOML basic
    strings, so Windows paths and any other characters round-trip."""
    whole = tenants if isinstance(tenants, TenantsFile) else TenantsFile(tenants=tenants)
    lines: list[str] = []
    if whole.tenant_claim != DEFAULT_TENANT_CLAIM:
        lines += [f"tenant_claim = {_string(whole.tenant_claim)}", ""]
    for server in whole.authorization_servers:
        lines.append("[[authorization_servers]]")
        for key, value in server.model_dump().items():
            lines.append(f"{key} = {_string(value)}")
        lines.append("")
    for name in sorted(whole.plans):
        lines.append(f"[plans.{name}]")
        plan = whole.plans[name].model_dump(mode="json", exclude_none=True)
        if "toolsets" in plan:
            lines.append(f"toolsets = [{', '.join(_string(value) for value in plan['toolsets'])}]")
        for key in ("calls_per_minute", "monthly_calls", "max_output_bytes"):
            if key in plan:
                lines.append(f"{key} = {plan[key]}")
        lines.append("")
    for name in sorted(whole.tenants):
        config = whole.tenants[name]
        lines.append(f"[tenants.{name}]")
        data = config.model_dump(mode="json", by_alias=True, exclude_defaults=True)
        for key in ("store", "schema", "datasets", "plan", "issuer"):
            if key in data:
                lines.append(f"{key} = {_string(data[key])}")
        for key in ("toolsets", "granted_permissions", "preapproved_permissions"):
            if key in data:
                values = ", ".join(_string(value) for value in sorted(data[key]))
                lines.append(f"{key} = [{values}]")
        if config.separate_approver is not None:
            lines.append(f"separate_approver = {str(config.separate_approver).lower()}")
        if config.disabled:
            lines.append("disabled = true")
        lines.append("")
    return "\n".join(lines)


def _string(value: str) -> str:
    return json.dumps(value)


def save_tenants(path: Path, tenants: dict[str, TenantConfig] | TenantsFile) -> None:
    """Write ``tenants`` to ``path`` atomically, readable only by its owner."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(dump_tenants(tenants))
    partial.replace(path)


class TenantCatalogs:
    """A `Tenancy` over a tenants file: each tenant's catalog, composed on first use."""

    def __init__(
        self,
        tenants: dict[str, TenantConfig],
        *,
        run_store: RunStore,
        datasets_root: Path,
        factory: AppFactory | None = None,
        plans: dict[str, Plan] | None = None,
    ) -> None:
        self.tenants = tenants
        self.plans = plans or {}
        self.run_store = run_store
        self.datasets_root = datasets_root
        self.factory = factory
        self._surfaces: dict[str, Surface] = {}
        self._open: list[ResearchApp] = []
        self._lock = threading.Lock()

    def surface(self, tenant: str) -> Surface | None:
        """Return ``tenant``'s surface, composing it on first use; None if not served."""
        config = self.tenants.get(tenant)
        if config is None or config.disabled:
            return None
        with self._lock:
            if tenant not in self._surfaces:
                try:
                    self._surfaces[tenant] = self._compose(tenant, config)
                except StoreError as error:
                    logger.warning("Tenant %s is not served: %s", tenant, error)
                    return None
            return self._surfaces[tenant]

    def _compose(self, tenant: str, config: TenantConfig) -> Surface:
        toolsets = entitled_toolsets(config, self.plans) or available_toolsets()
        research: ResearchApp | None = None
        if "research" in toolsets and config.store is not None:
            schema = config.schema_name or schema_for(tenant)
            research = open_research(config.store, read_only=True, schema=schema)
            self._open.append(research)
        elif "research" in toolsets:
            logger.warning("Tenant %s has no store, so research is not served.", tenant)
        policy = PermissionPolicy(
            granted=config.granted_permissions, preapproved=config.preapproved_permissions
        )
        catalog = compose(
            policy=policy,
            research=research.research if research else None,
            datasets=(
                DirectoryDatasetStore(config.datasets or self.datasets_root / tenant)
                if "analytics" in toolsets
                else None
            ),
            factory=self.factory if "factory" in toolsets else None,
        )
        plan = self.plans.get(config.plan) if config.plan else None
        executor = ToolExecutor(
            ToolRegistry(catalog.tools),
            policy,
            approvals=self.run_store,
            usage=plan_limits(tenant, plan, self.run_store) if plan else None,
            max_output_bytes=plan.max_output_bytes if plan else None,
            tenant=tenant,
        )
        return Surface(
            executor, catalog.resources, catalog.prompts, self.run_store, catalog.toolsets
        )

    def close(self) -> None:
        """Close every tenant's knowledge store."""
        with self._lock:
            for research in self._open:
                research.close()
            self._open.clear()
            self._surfaces.clear()


def plan_limits(tenant: str, plan: Plan, run_store: RunStore) -> PlanLimits | None:
    """Return ``tenant``'s rate limit and monthly quota under ``plan``, or None if it sets none.

    The monthly quota counts billable usage events in ``run_store``.
    """
    if plan.calls_per_minute is None and plan.monthly_calls is None:
        return None
    return PlanLimits(
        tenant,
        calls_per_minute=plan.calls_per_minute,
        monthly_calls=plan.monthly_calls,
        count_billable=functools.partial(run_store.count_billable, tenant),
    )


def entitled_toolsets(config: TenantConfig, plans: dict[str, Plan]) -> list[Toolset] | None:
    """Return the toolsets a tenant names, else its plan's, else None (every available one).

    `TenantsFile.check_plans` has already refused a tenant that widens its plan.
    """
    plan = plans.get(config.plan) if config.plan else None
    return config.toolsets or (plan.toolsets if plan else None)


def describe(
    tenant: str, config: TenantConfig, plans: dict[str, Plan] | None = None
) -> dict[str, Any]:
    """Return a tenant's settings for display, with any password in its store URL hidden."""
    from quant_research.infrastructure.postgres_store import redact_url

    store = config.store
    shown = redact_url(store) if store and is_postgres_url(store) else store
    return {
        "tenant": tenant,
        "store": shown or "(none)",
        "schema": (config.schema_name or schema_for(tenant)) if is_postgres_url(store) else "",
        "toolsets": ", ".join(entitled_toolsets(config, plans or {}) or available_toolsets()),
        "plan": config.plan or "",
        "issuer": config.issuer or "",
        "disabled": "yes" if config.disabled else "",
    }


def separate_approver(tenants: dict[str, TenantConfig]) -> Callable[[str], bool]:
    """Return whether each tenant needs an approver other than the requester (decision 0014).

    A tenant's ``separate_approver`` wins; otherwise every tenant but the local one needs one.
    """

    def needs(tenant: str) -> bool:
        config = tenants.get(tenant)
        if config is not None and config.separate_approver is not None:
            return config.separate_approver
        return four_eyes_except_local(tenant)

    return needs
