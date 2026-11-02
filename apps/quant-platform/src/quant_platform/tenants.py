"""Many tenants on one server (decision 0013): a tenants file and a catalog per tenant.

The tenants file (TOML) gives each tenant its knowledge store, datasets, toolsets, and
permissions. `TenantCatalogs` composes a tenant's catalog the first time one of its callers
arrives, and serves it as that tenant's `Surface`. A tenant's data never reaches another
tenant's surface: each has its own stores, and the run store filters reads by tenant.

    [tenants.acme]
    store = "postgresql://qaos_reader@db/qaos"   # schema qr_acme by default
    toolsets = ["research", "analytics"]
    issuer = "https://login.acme.example"        # the identity provider pinned for its tokens

    [[authorization_servers]]                    # OAuth for remote MCP clients (optional)
    issuer = "https://login.acme.example"
    introspection_url = "https://login.acme.example/oauth2/introspect"
    client_id = "qaos"
    client_secret_env = "ACME_INTROSPECTION_SECRET"   # the secret stays in the environment
"""

from __future__ import annotations

import logging
import os
import threading
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.mcp.oauth import DEFAULT_TENANT_CLAIM, AuthorizationServer
from quant_agent_os.runs import RunStore
from quant_agent_os.runtime import ToolExecutor
from quant_agent_os.serving import Surface
from quant_agent_os.tenancy import check_tenant
from quant_agent_os.tools import READ_ONLY, Permission, PermissionPolicy, ToolRegistry
from quant_platform.composition import Toolset, available_toolsets, compose
from quant_research import ResearchApp, open_research
from quant_research.infrastructure.postgres_store import is_postgres_url
from quant_research.infrastructure.sqlite_store import StoreError

if TYPE_CHECKING:
    from quant_app_factory import AppFactory

logger = logging.getLogger(__name__)


class TenantsFileError(ValueError):
    """Raised when a tenants file is missing or invalid."""


class TenantConfig(BaseModel):
    """What one tenant is served, and where its data lives."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    store: str | None = Field(default=None, description="SQLite path or postgresql:// URL")
    schema_name: str | None = Field(default=None, alias="schema")
    datasets: Path | None = None
    toolsets: list[Toolset] | None = None
    granted_permissions: frozenset[Permission] = READ_ONLY
    preapproved_permissions: frozenset[Permission] = frozenset()
    issuer: str | None = None
    disabled: bool = False

    @field_validator("store", "issuer")
    @classmethod
    def _plain(cls, value: str | None) -> str | None:
        if value is not None and any(char in value for char in '"\\\n'):
            raise ValueError("must not contain quotes, backslashes, or newlines")
        return value


class ServerEntry(BaseModel):
    """An authorization server in the tenants file; its secret is read from the environment."""

    model_config = ConfigDict(extra="forbid")

    issuer: str
    introspection_url: str
    client_id: str
    client_secret_env: str = Field(pattern=r"^[A-Z_][A-Z0-9_]*$")

    @field_validator("issuer", "introspection_url", "client_id")
    @classmethod
    def _plain(cls, value: str) -> str:
        if any(char in value for char in '"\\\n'):
            raise ValueError("must not contain quotes, backslashes, or newlines")
        return value

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

    tenants: dict[str, TenantConfig] = Field(default_factory=dict)
    authorization_servers: list[ServerEntry] = Field(default_factory=list)
    tenant_claim: str = Field(default=DEFAULT_TENANT_CLAIM, pattern=r"^[A-Za-z_][\w:./-]{0,63}$")

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
    """Return a tenants file (values are validated, so none need escaping)."""
    whole = tenants if isinstance(tenants, TenantsFile) else TenantsFile(tenants=tenants)
    lines: list[str] = []
    if whole.tenant_claim != DEFAULT_TENANT_CLAIM:
        lines += [f'tenant_claim = "{whole.tenant_claim}"', ""]
    for server in whole.authorization_servers:
        lines.append("[[authorization_servers]]")
        for key, value in server.model_dump().items():
            lines.append(f'{key} = "{value}"')
        lines.append("")
    for name in sorted(whole.tenants):
        config = whole.tenants[name]
        lines.append(f"[tenants.{name}]")
        data = config.model_dump(mode="json", by_alias=True, exclude_defaults=True)
        for key in ("store", "schema", "datasets", "issuer"):
            if key in data:
                lines.append(f'{key} = "{data[key]}"')
        for key in ("toolsets", "granted_permissions", "preapproved_permissions"):
            if key in data:
                values = ", ".join(f'"{value}"' for value in sorted(data[key]))
                lines.append(f"{key} = [{values}]")
        if config.disabled:
            lines.append("disabled = true")
        lines.append("")
    return "\n".join(lines)


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
    ) -> None:
        self.tenants = tenants
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
        toolsets = config.toolsets or available_toolsets()
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
        executor = ToolExecutor(ToolRegistry(catalog.tools), policy, approvals=self.run_store)
        return Surface(executor, catalog.resources, catalog.prompts, self.run_store)

    def close(self) -> None:
        """Close every tenant's knowledge store."""
        with self._lock:
            for research in self._open:
                research.close()
            self._open.clear()
            self._surfaces.clear()


def describe(tenant: str, config: TenantConfig) -> dict[str, Any]:
    """Return a tenant's settings for display, with any password in its store URL hidden."""
    from quant_research.infrastructure.postgres_store import redact_url

    store = config.store
    shown = redact_url(store) if store and is_postgres_url(store) else store
    return {
        "tenant": tenant,
        "store": shown or "(none)",
        "schema": (config.schema_name or schema_for(tenant)) if is_postgres_url(store) else "",
        "toolsets": ", ".join(config.toolsets or available_toolsets()),
        "issuer": config.issuer or "",
        "disabled": "yes" if config.disabled else "",
    }
