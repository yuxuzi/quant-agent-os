"""Opt-in OAuth sign-in for a firm's own server (decision 0028), configured in a sign-in file.

``QUANT_AGENT_OS_SIGN_IN_FILE`` (or ``sign_in_file`` in config.toml) names a TOML file; without
one, `serve_http` takes API keys only, as before. The file names this server's resource URL,
which every token's audience must be, and the identity providers it trusts::

    resource = "https://qaos.example.com/mcp"

    [[authorization_servers]]
    issuer = "https://login.example.com"
    introspection_url = "https://login.example.com/oauth2/introspect"
    client_id = "qaos"
    client_secret_env = "QAOS_INTROSPECTION_SECRET"  # the secret stays in the environment
    tenant = "local"              # optional: the tenant its users act in
    tenant_claim = "qaos_tenant"  # optional: "" when the provider sends no tenant claim
    groups_claim = "groups"       # optional: "" when it sends no groups

URLs must be https, except on loopback. The secret is read from the environment, then from the
``.env`` in the home, held as a `SecretStr`, and never displayed.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from quant_agent_os.api.server import is_loopback
from quant_agent_os.config import home_env_file
from quant_agent_os.tenancy import DEFAULT_TENANT, TENANT_ID
from quant_platform.identity.oauth import (
    DEFAULT_TENANT_CLAIM,
    AuthorizationServer,
    IntrospectionVerifier,
    protected_resource_routes,
)

if TYPE_CHECKING:
    import httpx
    from starlette.routing import Route

CLAIM_NAME = r"^$|^[A-Za-z_][\w:./-]{0,127}$"
DEFAULT_GROUPS_CLAIM = "groups"


class SignInError(ValueError):
    """Raised when a sign-in file is missing or invalid, or a secret it names is unset."""


def check_url(value: str) -> str:
    """Return ``value`` if it is an https URL, or an http one on loopback; raise otherwise."""
    parts = urlsplit(value)
    if not parts.hostname or parts.scheme not in ("https", "http"):
        raise ValueError(f"'{value}' is not an http(s) URL")
    if parts.scheme == "http" and not is_loopback(parts.hostname):
        raise ValueError(f"'{value}' must use https; plain http is allowed only on loopback")
    return value


class ServerEntry(BaseModel):
    """An identity provider in the sign-in file; its secret is read from the environment."""

    model_config = ConfigDict(extra="forbid")

    issuer: str
    introspection_url: str
    client_id: str = Field(min_length=1)
    client_secret_env: str = Field(pattern=r"^[A-Z_][A-Z0-9_]*$")
    tenant: str = Field(default=DEFAULT_TENANT, pattern=TENANT_ID.pattern)
    tenant_claim: str = Field(default=DEFAULT_TENANT_CLAIM, pattern=CLAIM_NAME)
    groups_claim: str = Field(default=DEFAULT_GROUPS_CLAIM, pattern=CLAIM_NAME)

    @field_validator("issuer", "introspection_url")
    @classmethod
    def _secure_urls(cls, value: str) -> str:
        return check_url(value)

    def secret(self, environ: Mapping[str, str | None]) -> SecretStr | None:
        """Return the client secret from ``environ``, or None when it is unset or empty."""
        value = environ.get(self.client_secret_env)
        return SecretStr(value) if value else None


class SignInFile(BaseModel):
    """The whole sign-in file: this server's resource URL and the providers it trusts."""

    model_config = ConfigDict(extra="forbid")

    resource: str
    authorization_servers: list[ServerEntry] = Field(min_length=1)

    @field_validator("resource")
    @classmethod
    def _secure_resource(cls, value: str) -> str:
        return check_url(value)

    @field_validator("authorization_servers")
    @classmethod
    def _one_entry_per_issuer(cls, servers: list[ServerEntry]) -> list[ServerEntry]:
        issuers = [server.issuer for server in servers]
        if len(set(issuers)) != len(issuers):
            raise ValueError("an issuer appears twice; list each provider once")
        return servers

    def missing_secrets(self, environ: Mapping[str, str | None] | None = None) -> list[str]:
        """Return the secret variables that are unset, in the environment and the home's .env."""
        values = environ if environ is not None else secret_sources()
        return [s.client_secret_env for s in self.authorization_servers if not s.secret(values)]

    def resolve(self, environ: Mapping[str, str | None] | None = None) -> SignIn:
        """Return the sign-in with its secrets; raise `SignInError` naming any that are unset."""
        values = environ if environ is not None else secret_sources()
        missing = self.missing_secrets(values)
        if missing:
            raise SignInError(
                f"Set {', '.join(missing)} to the introspection client secret (in the environment "
                f"or {home_env_file()}); sign-in does not start without it."
            )
        servers = []
        for entry in self.authorization_servers:
            secret = entry.secret(values)
            assert secret is not None  # checked above
            servers.append(
                AuthorizationServer(
                    issuer=entry.issuer,
                    introspection_url=entry.introspection_url,
                    client_id=entry.client_id,
                    client_secret=secret,
                    tenant_claim=entry.tenant_claim,
                    groups_claim=entry.groups_claim or None,
                )
            )
        tenants = {entry.issuer: {entry.tenant} for entry in self.authorization_servers}
        return SignIn(self.resource, servers, tenants)


@dataclass(frozen=True)
class SignIn:
    """Resolved sign-in: the resource URL, the trusted providers, and each one's tenant."""

    resource: str
    servers: list[AuthorizationServer]
    tenants_by_issuer: dict[str, set[str]]

    @property
    def issuers(self) -> list[str]:
        """Return the trusted issuers, in the file's order."""
        return [server.issuer for server in self.servers]

    @property
    def host(self) -> str:
        """Return the host name clients reach this server by (the resource URL's)."""
        return urlsplit(self.resource).hostname or ""

    def tenants(self) -> set[str]:
        """Return every tenant a signed-in user may act in."""
        return set().union(*self.tenants_by_issuer.values())

    def verifier(self, http: httpx.AsyncClient | None = None) -> IntrospectionVerifier:
        """Return the verifier for these providers' tokens; ``http`` is for tests."""
        return IntrospectionVerifier(
            self.servers,
            resource=self.resource,
            tenants_by_issuer=self.tenants_by_issuer,
            http=http,
        )

    def metadata_routes(self) -> tuple[list[Route], str]:
        """Return the RFC 9728 metadata routes and the metadata URL 401 responses name."""
        return protected_resource_routes(self.resource, self.issuers)


def secret_sources() -> dict[str, str | None]:
    """Return the home's .env overlaid by the environment: where client secrets are read."""
    from dotenv import dotenv_values

    path = home_env_file()
    values = dict(dotenv_values(path)) if path.is_file() else {}
    return values | dict(os.environ)


def load_sign_in_file(path: Path) -> SignInFile:
    """Read a sign-in file without reading any secret; raise `SignInError` naming the problem."""
    try:
        data = tomllib.loads(path.read_text("utf-8"))
        return SignInFile.model_validate(data)
    except FileNotFoundError as error:
        raise SignInError(
            f"No sign-in file at {path}; create it, or unset QUANT_AGENT_OS_SIGN_IN_FILE to take "
            "API keys only."
        ) from error
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(map(str, item['loc'])) or 'file'}: {item['msg']}" for item in error.errors()
        )
        raise SignInError(f"Invalid sign-in file {path}: {problems}") from error
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise SignInError(f"Invalid sign-in file {path}: {error}") from error


def load_sign_in(path: Path, environ: Mapping[str, str | None] | None = None) -> SignIn:
    """Read a sign-in file and its secrets; raise `SignInError` for anything missing or wrong."""
    return load_sign_in_file(path).resolve(environ)
