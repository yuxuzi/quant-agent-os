"""OAuth tokens that are signed JWTs, checked locally against the provider's keys (decision 0031).

Entra ID and Amazon Cognito have no RFC 7662 introspection endpoint, so `JwksVerifier` checks a
bearer token's signature against the issuer's JSON Web Key Set instead, and the claims that
`IntrospectionVerifier` checks from an introspection answer. A token is accepted only when:
- it is a JWT, no longer than `MAX_TOKEN_CHARS`, whose ``iss`` is one a sign-in file names (the
  unverified ``iss`` only picks which configured provider's keys to use);
- its header names an algorithm the file allows (asymmetric only: never ``none``, never HS*, so a
  public key cannot be used as an HMAC secret) and a ``kid`` among the issuer's keys, and has no
  ``crit`` header;
- the signature verifies, ``exp`` is present and in the future, ``nbf`` and ``iat`` are not in the
  future (each within the configured leeway), and ``iss`` equals the configured issuer exactly;
- its audience is one the file lists in ``audiences``, or, for a token with no ``aud`` (Cognito
  access tokens), its ``client_id`` is one the file lists in ``client_ids``;
- ``token_use``, when the file requires one, equals it;
- its tenant, groups, scopes, and provider tenant read as the file maps them, and a claim of the
  wrong type refuses the token.

Keys are fetched from ``jwks_uri``, or from the ``jwks_uri`` in the issuer's
``/.well-known/openid-configuration`` (whose ``issuer`` must equal the configured one), kept for
`KEYS_TTL_SECONDS`, and fetched again for an unknown ``kid`` at most once per
`REFETCH_SECONDS` per issuer, so unauthenticated requests cannot make the server flood a
provider (security review F-1). A key fetch that fails refuses the token. Nothing is sent to the
provider but the keys' own address, never a token. Needs the ``mcp`` and ``api`` extras (httpx2 and
PyJWT come with them).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from quant_agent_os.api.keys import KEY_PREFIX, SCOPES
from quant_agent_os.mcp.http import GROUPS_CLAIM, TENANT_CLAIM
from quant_platform.identity.oauth import (
    DEFAULT_TENANT_CLAIM,
    check_url,
    resolve_tenant,
    unverified_issuer,
)

if TYPE_CHECKING:
    import httpx2
    from mcp.server.auth.provider import AccessToken

logger = logging.getLogger(__name__)
KEYS_TTL_SECONDS = 3600.0
REFETCH_SECONDS = 30.0
MAX_TOKEN_CHARS = 16_384
MAX_JWKS_BYTES = 1_048_576
FETCH_TIMEOUT_SECONDS = 5.0
PROVIDER_TENANT_CLAIM = "qaos_provider_tenant"  # set from the verified token, never the request
Algorithm = Literal["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"]
KEY_TYPES = ("RSA", "EC")
MAX_PRINCIPAL_CHARS = 256


def default_algorithms() -> list[Algorithm]:
    """Return the algorithms allowed unless the file says otherwise: RS256 only."""
    return ["RS256"]


class JwksServer(BaseModel):
    """An identity provider whose tokens are JWTs, and how to read them.

    At least one of ``audiences`` and ``client_ids`` must be set. ``tenant_claim`` and
    ``tenant_claim_value`` work as for an introspecting provider. ``groups_claims`` are the
    claims whose values are the caller's groups (their union), renamed by ``group_aliases``;
    ``scope_claims`` are the claims that grant scopes (each a space-separated string or a list),
    renamed by ``scope_map``. ``principal_claim`` names the caller in the audit trail (``sub``
    when absent). With ``provider_tenant_claim``, the claim's value is required and joins the
    principal as ``principal@value``.
    """

    model_config = ConfigDict(extra="forbid")

    issuer: str
    jwks_uri: str | None = None
    audiences: list[str] = Field(default_factory=list)
    client_ids: list[str] = Field(default_factory=list)
    algorithms: list[Algorithm] = Field(default_factory=default_algorithms, min_length=1)
    leeway_seconds: int = Field(default=30, ge=0, le=300)
    token_use: str | None = None
    tenant_claim: str = DEFAULT_TENANT_CLAIM
    tenant_claim_value: str | None = None
    principal_claim: str = "sub"
    scope_claims: list[str] = Field(default_factory=lambda: ["scope"])
    scope_map: dict[str, str] = Field(default_factory=dict)
    groups_claims: list[str] = Field(default_factory=lambda: ["groups"])
    group_aliases: dict[str, str] = Field(default_factory=dict)
    provider_tenant_claim: str = ""


@dataclass
class _Keys:
    """An issuer's usable keys by ``kid``, each with the ``alg`` its JWK declares, if any."""

    keys: dict[str, tuple[Any, str | None]]
    fetched: float


class JwksVerifier:
    """A `TokenVerifier` for JWT access tokens, by signature against the issuer's keys."""

    def __init__(
        self,
        servers: list[JwksServer],
        *,
        resource: str,
        tenants_by_issuer: dict[str, set[str]],
        http: httpx2.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        keys_ttl: float = KEYS_TTL_SECONDS,
        refetch_interval: float = REFETCH_SECONDS,
    ) -> None:
        if not servers:
            raise ValueError("JWKS sign-in needs at least one provider.")
        self.servers = {server.issuer: server for server in servers}
        self.resource = resource
        self.tenants_by_issuer = tenants_by_issuer
        self.http = http
        self._clock = clock
        self._ttl = keys_ttl
        self._interval = refetch_interval
        self._keys: dict[str, _Keys] = {}
        self._attempted: dict[str, float] = {}
        self._fetching = {issuer: asyncio.Lock() for issuer in self.servers}
        self._lock = threading.Lock()

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return the access a valid token for this server grants, or None."""
        import jwt

        if token.startswith(KEY_PREFIX) or len(token) > MAX_TOKEN_CHARS:
            return None  # an API key is KeyVerifier's job; an oversized token is never a JWT
        issuer = unverified_issuer(token)
        server = self.servers.get(issuer) if issuer is not None else None
        if server is None:
            return None
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            return None
        algorithm, kid = header.get("alg"), header.get("kid")
        if (
            algorithm not in server.algorithms
            or not isinstance(kid, str)
            or not kid
            or "crit" in header
        ):
            return self._refuse(server, "with an algorithm, key id, or header not allowed")
        found = await self._key(server, kid)
        if found is None:
            return self._refuse(server, "with no key to check it against")
        key, declared = found
        if declared is not None and declared != algorithm:
            return self._refuse(server, "signed with another algorithm than its key's")
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=[algorithm],
                issuer=server.issuer,
                leeway=server.leeway_seconds,
                options={"require": ["exp", "iss"], "verify_aud": False},
            )
        except jwt.PyJWTError as error:
            return self._refuse(server, f"that failed validation ({type(error).__name__})")
        return self._access(server, token, claims)

    @staticmethod
    def _refuse(server: JwksServer, reason: str) -> None:
        # Says why, never the token, so an operator can fix the provider's configuration.
        logger.info("Refused a token from %s: %s.", server.issuer, reason)

    async def _key(self, server: JwksServer, kid: str) -> tuple[Any, str | None] | None:
        """Return the issuer's key ``kid`` (fetching keys when due), or None."""
        found = self._cached(server.issuer, kid)
        if found is not None:
            return found
        async with self._fetching[server.issuer]:
            found = self._cached(server.issuer, kid)  # another request may have just fetched
            if found is not None:
                return found
            now = self._clock()
            with self._lock:
                if now - self._attempted.get(server.issuer, -self._interval) < self._interval:
                    return None  # asked lately, whether or not it answered: do not ask again
                self._attempted[server.issuer] = now
            keys = await self._fetch(server)
            if keys is None:
                return None  # fail closed; keys already held stay until their time is up
            with self._lock:
                self._keys[server.issuer] = _Keys(keys, now)
            return keys.get(kid)

    def _cached(self, issuer: str, kid: str) -> tuple[Any, str | None] | None:
        with self._lock:
            held = self._keys.get(issuer)
            if held is not None and self._clock() - held.fetched >= self._ttl:
                del self._keys[issuer]
                held = None
            return held.keys.get(kid) if held is not None else None

    async def _fetch(self, server: JwksServer) -> dict[str, tuple[Any, str | None]] | None:
        import httpx2

        try:
            if self.http is not None:
                data = await self._load(self.http, server)
            else:
                async with httpx2.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS) as client:
                    data = await self._load(client, server)
        except (httpx2.HTTPError, ValueError) as error:
            logger.warning("Fetching the keys of %s failed: %s", server.issuer, type(error))
            return None
        keys = _usable_keys(data)
        if not keys:
            logger.warning("The keys of %s hold no usable signing key.", server.issuer)
            return None
        return keys

    @staticmethod
    async def _load(client: httpx2.AsyncClient, server: JwksServer) -> Any:
        uri = server.jwks_uri
        if uri is None:
            base = server.issuer.rstrip("/")
            config = await _get_json(client, f"{base}/.well-known/openid-configuration")
            if not isinstance(config, dict) or config.get("issuer") != server.issuer:
                raise ValueError("discovery names another issuer")
            uri = config.get("jwks_uri")
            if not isinstance(uri, str):
                raise ValueError("discovery names no jwks_uri")
            check_url(uri)
        return await _get_json(client, uri)

    def _access(self, server: JwksServer, token: str, claims: dict[str, Any]) -> AccessToken | None:
        from mcp.server.auth.provider import AccessToken

        pinned = self.tenants_by_issuer.get(server.issuer, set())
        tenant = resolve_tenant(
            claims,
            tenant_claim=server.tenant_claim,
            tenant_claim_value=server.tenant_claim_value,
            pinned=pinned,
        )
        scopes = _strings(claims, server.scope_claims, split=True)
        groups = _strings(claims, server.groups_claims, split=False)
        reason = (
            self._doubt(server, claims)
            or (None if isinstance(tenant, str) and tenant in pinned else "for a tenant not served")
            or (
                None
                if scopes is not None and groups is not None
                else "with a scope or groups claim that is not a string or a list of strings"
            )
        )
        if reason is not None or scopes is None or groups is None:
            return self._refuse(server, reason or "")
        if _overage(claims, server.groups_claims):
            # No Graph call: the groups that were not sent are none, so access fails closed.
            logger.warning(
                "A token from %s has too many groups to carry them (Entra's overage), so none "
                "were read. Assign app roles, or limit the groups the token carries, in the "
                "provider.",
                server.issuer,
            )
        granted = {server.scope_map.get(name, name) for name in scopes}
        subject = _principal(claims, server.principal_claim)
        provider_tenant = claims.get(server.provider_tenant_claim)
        extra: dict[str, Any] = {}
        if server.provider_tenant_claim:
            if not isinstance(provider_tenant, str) or not provider_tenant:
                return self._refuse(server, "with no provider tenant claim, which the file needs")
            subject = f"{subject or claims.get('client_id') or 'oauth'}@{provider_tenant}"
            extra[PROVIDER_TENANT_CLAIM] = provider_tenant
        expires = claims["exp"]
        assert isinstance(tenant, str) and isinstance(expires, int | float)  # checked above
        return AccessToken(
            token=token,
            client_id=str(claims.get("client_id") or claims.get("azp") or subject or "oauth"),
            # Approving is done on the approval page with an approver key, never by token.
            scopes=[name for name in SCOPES if name in granted and name != "runs:approve"],
            expires_at=int(expires),
            resource=self.resource,
            subject=subject,
            claims={
                TENANT_CLAIM: tenant,
                GROUPS_CLAIM: sorted({server.group_aliases.get(g, g) for g in groups}),
                "iss": server.issuer,
                **extra,
            },
        )

    @staticmethod
    def _doubt(server: JwksServer, claims: dict[str, Any]) -> str | None:
        """Return why a signed token's audience or kind is not accepted, or None."""
        audience = claims.get("aud")
        if audience is None:
            accepted = claims.get("client_id") in server.client_ids
        else:
            audiences = audience if isinstance(audience, list) else [audience]
            accepted = any(a in server.audiences for a in audiences if isinstance(a, str))
        if not accepted:
            return "for another audience"
        if server.token_use is not None and claims.get("token_use") != server.token_use:
            return "of another kind than the file requires"
        return None


def _principal(claims: dict[str, Any], claim: str) -> str | None:
    """Return the caller's name: ``claim`` if it is a short string, else ``sub``, else None."""
    for name in (claim, "sub"):
        value = claims.get(name)
        if isinstance(value, str) and 0 < len(value) <= MAX_PRINCIPAL_CHARS and value.isprintable():
            return value
    return None


def _strings(claims: dict[str, Any], names: list[str], *, split: bool) -> list[str] | None:
    """Return the strings ``names`` hold, or None when one holds anything else.

    A missing claim holds nothing. With ``split``, a string is a space-separated list.
    """
    found: list[str] = []
    for name in names:
        value = claims.get(name)
        if value is None:
            continue
        if split and isinstance(value, str):
            found += value.split()
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            found += value
        else:
            return None
    return found


def _overage(claims: dict[str, Any], names: list[str]) -> bool:
    """Return whether Entra left the groups out for being too many (it names where to ask)."""
    claimed = claims.get("_claim_names")
    pointed = isinstance(claimed, dict) and "groups" in claimed
    return "groups" in names and (pointed or claims.get("hasgroups") is True)


async def _get_json(client: httpx2.AsyncClient, url: str) -> Any:
    response = await client.get(url, headers={"Accept": "application/json"})
    response.raise_for_status()
    if len(response.content) > MAX_JWKS_BYTES:
        raise ValueError("response too large")
    return response.json()


def _usable_keys(data: Any) -> dict[str, tuple[Any, str | None]]:
    """Return the signing keys in a JWKS by ``kid``; a ``kid`` that appears twice is dropped."""
    import jwt

    entries = data.get("keys") if isinstance(data, dict) else None
    found: dict[str, tuple[Any, str | None]] = {}
    seen: set[str] = set()
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        kid = entry.get("kid")
        if not isinstance(kid, str) or not kid or entry.get("use") not in (None, "sig"):
            continue
        if entry.get("kty") not in KEY_TYPES:
            continue  # a symmetric ("oct") key is never a provider's public key
        if kid in seen:
            found.pop(kid, None)
            continue
        seen.add(kid)
        try:
            key = jwt.PyJWK(entry).key
        except jwt.PyJWTError:
            continue
        declared = entry.get("alg")
        found[kid] = (key, declared if isinstance(declared, str) else None)
    return found
