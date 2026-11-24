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
    tenant_claim_value = "acme"   # optional: what that claim holds, if not the tenant itself
    groups_claim = "groups"       # optional: "" when it sends no groups

An entry may instead set ``verify = "jwks"`` (decision 0031) for a provider with no introspection
endpoint (Entra ID, Amazon Cognito): tokens are JWTs checked against the issuer's keys, and the
introspection settings are not set::

    [[authorization_servers]]
    issuer = "https://login.microsoftonline.com/<tenant-guid>/v2.0"
    verify = "jwks"
    audiences = ["api://qaos"]    # the tokens' `aud`; or `client_ids` for tokens with no `aud`
    tenant_claim = ""
    scope_claims = ["scp"]
    scope_map = { "tools.call" = "tools:call", "tools.list" = "tools:list" }
    groups_claims = ["groups", "roles"]
    group_aliases = { "6f1c2a10-0000-4000-8000-0000000e0001" = "research-eq" }

URLs must be https, except on loopback. The secret is read from the environment, then from the
``.env`` in the home, or from the file that ``<NAME>_FILE`` names, held as a `SecretStr`, and never
displayed (`quant_agent_os.config.read_secret`).
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from quant_agent_os.api.keys import SCOPES
from quant_agent_os.config import SecretConfigError, home_env_file, read_secret, secret_sources
from quant_agent_os.tenancy import DEFAULT_TENANT, TENANT_ID
from quant_platform.identity.jwks import Algorithm, JwksServer, JwksVerifier, default_algorithms
from quant_platform.identity.oauth import (
    DEFAULT_TENANT_CLAIM,
    AuthorizationServer,
    FirstOf,
    IntrospectionVerifier,
    check_url,
    protected_resource_routes,
)

if TYPE_CHECKING:
    import httpx
    from mcp.server.auth.provider import TokenVerifier
    from starlette.routing import Route

CLAIM_NAME = r"^$|^[A-Za-z_][\w:./-]{0,127}$"
DEFAULT_GROUPS_CLAIM = "groups"


class SignInError(ValueError):
    """Raised when a sign-in file is missing or invalid, or a secret it names is unset."""


JWKS_ONLY = (
    "jwks_uri",
    "audiences",
    "client_ids",
    "algorithms",
    "leeway_seconds",
    "token_use",
    "principal_claim",
    "scope_claims",
    "scope_map",
    "groups_claims",
    "group_aliases",
    "provider_tenant_claim",
)
INTROSPECTION_ONLY = ("introspection_url", "client_id", "client_secret_env")


class ServerEntry(BaseModel):
    """An identity provider in the sign-in file; its secret is read from the environment.

    ``verify`` is ``"introspection"`` (the default: RFC 7662, with the three introspection
    settings) or ``"jwks"`` (JWTs checked against the issuer's keys, with the settings that
    `JwksServer` describes). A setting that the chosen mode would ignore is refused.
    """

    model_config = ConfigDict(extra="forbid")

    issuer: str
    verify: Literal["introspection", "jwks"] = "introspection"
    introspection_url: str | None = None
    client_id: str | None = Field(default=None, min_length=1)
    client_secret_env: str | None = Field(default=None, pattern=r"^[A-Z_][A-Z0-9_]*$")
    tenant: str = Field(default=DEFAULT_TENANT, pattern=TENANT_ID.pattern)
    tenant_claim: str = Field(default=DEFAULT_TENANT_CLAIM, pattern=CLAIM_NAME)
    tenant_claim_value: str | None = Field(default=None, min_length=1, max_length=256)
    groups_claim: str = Field(default=DEFAULT_GROUPS_CLAIM, pattern=CLAIM_NAME)
    jwks_uri: str | None = None
    audiences: list[str] = Field(default_factory=list)
    client_ids: list[str] = Field(default_factory=list)
    algorithms: list[Algorithm] = Field(default_factory=default_algorithms, min_length=1)
    leeway_seconds: int = Field(default=30, ge=0, le=300)
    token_use: str | None = Field(default=None, min_length=1, max_length=64)
    principal_claim: str = Field(default="sub", pattern=CLAIM_NAME, min_length=1)
    scope_claims: list[str] = Field(default_factory=lambda: ["scope"])
    scope_map: dict[str, str] = Field(default_factory=dict)
    groups_claims: list[str] = Field(default_factory=list)
    group_aliases: dict[str, str] = Field(default_factory=dict)
    provider_tenant_claim: str = Field(default="", pattern=CLAIM_NAME)

    @field_validator("issuer", "introspection_url", "jwks_uri")
    @classmethod
    def _secure_urls(cls, value: str | None) -> str | None:
        return check_url(value) if value is not None else None

    @field_validator("scope_claims", "groups_claims")
    @classmethod
    def _claim_names(cls, names: list[str]) -> list[str]:
        for name in names:
            if not re.fullmatch(CLAIM_NAME, name) or not name:
                raise ValueError(f"'{name}' is not a claim name")
        return names

    @field_validator("scope_map")
    @classmethod
    def _known_scopes(cls, mapping: dict[str, str]) -> dict[str, str]:
        for name, scope in mapping.items():
            if scope not in SCOPES or scope == "runs:approve":
                raise ValueError(
                    f"'{name}' maps to '{scope}', which is not a scope a token may grant "
                    f"({', '.join(s for s in SCOPES if s != 'runs:approve')})"
                )
        return mapping

    @model_validator(mode="after")
    def _value_needs_a_claim(self) -> ServerEntry:
        if self.tenant_claim_value is not None and not self.tenant_claim:
            raise ValueError("tenant_claim_value needs a tenant_claim to read it from")
        return self

    @model_validator(mode="after")
    def _settings_fit_the_mode(self) -> ServerEntry:
        given = self.model_fields_set
        if self.verify == "introspection":
            misplaced = [name for name in JWKS_ONLY if name in given]
            if misplaced:
                raise ValueError(f'{", ".join(misplaced)} apply only with verify = "jwks"')
            absent = [name for name in INTROSPECTION_ONLY if getattr(self, name) is None]
            if absent:
                raise ValueError(f'{", ".join(absent)} must be set (verify = "introspection")')
            return self
        misplaced = [name for name in INTROSPECTION_ONLY if name in given]
        if misplaced:
            raise ValueError(f'{", ".join(misplaced)} apply only with verify = "introspection"')
        if not self.audiences and not self.client_ids:
            raise ValueError(
                'verify = "jwks" needs audiences, or client_ids for tokens without aud'
            )
        if "groups_claims" in given and "groups_claim" in given:
            raise ValueError('set groups_claims, not groups_claim, with verify = "jwks"')
        return self

    @property
    def jwks(self) -> bool:
        """Return whether this provider's tokens are JWTs checked against its keys."""
        return self.verify == "jwks"

    def secret(self, environ: Mapping[str, str | None]) -> SecretStr | None:
        """Return the client secret from ``environ``, or None when it is unset or empty.

        Raises `SecretConfigError` when it is given twice, or its ``_FILE`` cannot be read.
        """
        if not self.client_secret_env:
            return None
        value = read_secret(self.client_secret_env, environ)
        return SecretStr(value) if value else None

    def jwks_server(self) -> JwksServer:
        """Return the JWT settings for a ``verify = "jwks"`` provider."""
        groups = (
            self.groups_claims
            if "groups_claims" in self.model_fields_set
            else ([self.groups_claim] if self.groups_claim else [])
        )
        return JwksServer(
            issuer=self.issuer,
            jwks_uri=self.jwks_uri,
            audiences=self.audiences,
            client_ids=self.client_ids,
            algorithms=self.algorithms,
            leeway_seconds=self.leeway_seconds,
            token_use=self.token_use,
            tenant_claim=self.tenant_claim,
            tenant_claim_value=self.tenant_claim_value,
            principal_claim=self.principal_claim,
            scope_claims=self.scope_claims,
            scope_map=self.scope_map,
            groups_claims=groups,
            group_aliases=self.group_aliases,
            provider_tenant_claim=self.provider_tenant_claim,
        )


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
        return [
            s.client_secret_env
            for s in self.authorization_servers
            if s.client_secret_env is not None and not s.secret(values)
        ]

    def resolve(self, environ: Mapping[str, str | None] | None = None) -> SignIn:
        """Return the sign-in with its secrets; raise `SignInError` naming any that are unset."""
        values = environ if environ is not None else secret_sources()
        try:
            missing = self.missing_secrets(values)
        except SecretConfigError as error:
            raise SignInError(str(error)) from None
        if missing:
            raise SignInError(
                f"Set {', '.join(missing)} to the introspection client secret, or "
                f"{', '.join(f'{name}_FILE' for name in missing)} to a file holding it (in the "
                f"environment or {home_env_file()}); sign-in does not start without it."
            )
        servers = []
        for entry in self.authorization_servers:
            if entry.jwks:
                continue
            try:
                secret = entry.secret(values)
            except SecretConfigError as error:
                raise SignInError(str(error)) from None
            assert secret is not None and entry.introspection_url and entry.client_id  # checked
            servers.append(
                AuthorizationServer(
                    issuer=entry.issuer,
                    introspection_url=entry.introspection_url,
                    client_id=entry.client_id,
                    client_secret=secret,
                    tenant_claim=entry.tenant_claim,
                    tenant_claim_value=entry.tenant_claim_value,
                    groups_claim=entry.groups_claim or None,
                )
            )
        tenants = {entry.issuer: {entry.tenant} for entry in self.authorization_servers}
        jwks = [entry.jwks_server() for entry in self.authorization_servers if entry.jwks]
        return SignIn(self.resource, servers, tenants, jwks)


@dataclass(frozen=True)
class SignIn:
    """Resolved sign-in: the resource URL, the trusted providers, and each one's tenant."""

    resource: str
    servers: list[AuthorizationServer]
    tenants_by_issuer: dict[str, set[str]]
    jwks_servers: list[JwksServer] = field(default_factory=list)

    @property
    def issuers(self) -> list[str]:
        """Return the trusted issuers: those that introspect, then those checked by their keys."""
        return [s.issuer for s in self.servers] + [s.issuer for s in self.jwks_servers]

    @property
    def host(self) -> str:
        """Return the host name clients reach this server by (the resource URL's)."""
        return urlsplit(self.resource).hostname or ""

    def tenants(self) -> set[str]:
        """Return every tenant a signed-in user may act in."""
        return set().union(*self.tenants_by_issuer.values())

    def verifier(self, http: httpx.AsyncClient | None = None) -> TokenVerifier:
        """Return the verifier for these providers' tokens; ``http`` is for tests.

        Providers that introspect are asked by `IntrospectionVerifier`, the others checked by
        `JwksVerifier`; a file with both asks each, and a token goes only to the one that
        trusts its issuer.
        """
        verifiers: list[TokenVerifier] = []
        if self.servers:
            verifiers.append(
                IntrospectionVerifier(
                    self.servers,
                    resource=self.resource,
                    tenants_by_issuer=self.tenants_by_issuer,
                    http=http,
                )
            )
        if self.jwks_servers:
            verifiers.append(
                JwksVerifier(
                    self.jwks_servers,
                    resource=self.resource,
                    tenants_by_issuer=self.tenants_by_issuer,
                    http=http,
                )
            )
        return verifiers[0] if len(verifiers) == 1 else FirstOf(*verifiers)

    def metadata_routes(self) -> tuple[list[Route], str]:
        """Return the RFC 9728 metadata routes and the metadata URL 401 responses name."""
        return protected_resource_routes(self.resource, self.issuers)


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


def scope_gap(scope_map: Mapping[str, str]) -> str | None:
    """Return a warning when ``scope_map`` grants ``tools:call`` but never ``tools:list``.

    Otherwise return None.

    Most MCP clients list the tools before they call one, so a token with only the call scope
    fails at the listing. The server does not refuse such a file: it warns, at start-up and in
    `qaos doctor`.
    """
    granted = set(scope_map.values())
    if "tools:call" in granted and "tools:list" not in granted:
        return (
            "its scope_map grants tools:call and never tools:list. Most MCP clients list the tools "
            "before they call one, so its users are refused at the listing; map a provider's "
            'name to "tools:list" too.'
        )
    return None
