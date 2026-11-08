"""OAuth 2.1 tokens for remote MCP clients (decision 0013): a resource server, by introspection.

The server never issues tokens; each tenant's identity provider does. `IntrospectionVerifier`
checks a bearer token by RFC 7662 introspection at a configured authorization server, then
requires that the token is active and unexpired, that its audience is this server (RFC 8707),
that its issuer is one the server trusts, and that its tenant claim names a tenant pinned to
that issuer. The token is never sent anywhere else:
- a JWT-shaped token's unverified ``iss`` only chooses which configured server to ask;
- an opaque token is accepted only when exactly one server is configured.

Results are cached by the token's SHA-256 for at most `CACHE_SECONDS`, never past the token's
expiry, and a provider's refusal for `REFUSAL_CACHE_SECONDS`. So that unauthenticated requests
cannot make the server flood an identity provider (security review F-1), each provider gets at
most `INTROSPECTIONS_PER_MINUTE` introspections and `MAX_CONCURRENT_INTROSPECTIONS` at once;
beyond that a token is refused without asking. `protected_resource_routes` publishes the RFC
9728 metadata that points clients to the authorization servers. Needs the ``mcp`` and ``api``
extras (httpx comes with them).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import threading
import time
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel, ConfigDict, SecretStr

from quant_agent_os.api.keys import KEY_PREFIX, SCOPES
from quant_agent_os.mcp.http import TENANT_CLAIM
from quant_platform.identity.rate import TokenBucket

if TYPE_CHECKING:
    import httpx
    from mcp.server.auth.provider import AccessToken, TokenVerifier
    from starlette.routing import Route

logger = logging.getLogger(__name__)
V = TypeVar("V")
CACHE_SECONDS = 60.0
REFUSAL_CACHE_SECONDS = 30.0
INTROSPECTIONS_PER_MINUTE = 600
MAX_CONCURRENT_INTROSPECTIONS = 8
MAX_CACHED_TOKENS = 10_000
WARN_EVERY_SECONDS = 60.0
DEFAULT_TENANT_CLAIM = "qaos_tenant"


class AuthorizationServer(BaseModel):
    """An identity provider the server trusts, and how to ask it about a token."""

    model_config = ConfigDict(extra="forbid")

    issuer: str
    introspection_url: str
    client_id: str
    client_secret: SecretStr


class IntrospectionVerifier:
    """A `TokenVerifier` for OAuth access tokens, by introspection at a trusted server."""

    def __init__(
        self,
        servers: list[AuthorizationServer],
        *,
        resource: str,
        tenants_by_issuer: dict[str, set[str]],
        tenant_claim: str = DEFAULT_TENANT_CLAIM,
        http: httpx.AsyncClient | None = None,
        introspections_per_minute: int = INTROSPECTIONS_PER_MINUTE,
        max_concurrent: int = MAX_CONCURRENT_INTROSPECTIONS,
    ) -> None:
        if not servers:
            raise ValueError("OAuth needs at least one authorization server.")
        self.servers = {server.issuer: server for server in servers}
        self.resource = resource
        self.tenants_by_issuer = tenants_by_issuer
        self.tenant_claim = tenant_claim
        self.http = http
        self._cache: dict[str, tuple[float, AccessToken]] = {}
        self._refused: dict[str, float] = {}
        self._budget = {issuer: TokenBucket(introspections_per_minute) for issuer in self.servers}
        self._in_flight = dict.fromkeys(self.servers, 0)
        self._max_concurrent = max_concurrent
        self._warned: dict[str, float] = {}
        self._lock = threading.Lock()

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return the access an active token for this server grants, or None."""
        if token.startswith(KEY_PREFIX):
            return None  # an API key: KeyVerifier's job, and never sent to a provider
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = time.time()
        with self._lock:
            cached = self._cache.get(digest)
            if cached is not None and cached[0] > now:
                return cached[1]
            if self._refused.get(digest, 0.0) > now:
                return None
        server = self._server_for(token)
        if server is None or not self._admit(server.issuer, now):
            return None
        try:
            claims = await self._introspect(server, token)
        finally:
            with self._lock:
                self._in_flight[server.issuer] -= 1
        access = self._access(server, token, claims, now) if claims is not None else None
        with self._lock:
            if access is not None:
                until = min(now + CACHE_SECONDS, float(access.expires_at or now + CACHE_SECONDS))
                self._cache = _bounded({k: v for k, v in self._cache.items() if v[0] > now})
                self._cache[digest] = (until, access)
            elif claims is not None:  # the provider's verdict; a failed request is not one
                self._refused = _bounded({k: v for k, v in self._refused.items() if v > now})
                self._refused[digest] = now + REFUSAL_CACHE_SECONDS
        return access

    def _admit(self, issuer: str, now: float) -> bool:
        """Reserve one introspection at ``issuer`` within its budget, or return False."""
        with self._lock:
            busy = self._in_flight[issuer] >= self._max_concurrent
            if not busy and self._budget[issuer].take() == 0.0:
                self._in_flight[issuer] += 1
                return True
            if now - self._warned.get(issuer, 0.0) >= WARN_EVERY_SECONDS:
                self._warned[issuer] = now
                logger.warning(
                    "Refusing tokens for %s without introspection: its budget is used up "
                    "(security review F-1). Rate-limit unauthenticated requests in the proxy.",
                    issuer,
                )
            return False

    def _server_for(self, token: str) -> AuthorizationServer | None:
        """Choose the one configured server that may see ``token``, or None."""
        issuer = _unverified_issuer(token)
        if issuer is not None:
            return self.servers.get(issuer)
        if len(self.servers) == 1:
            return next(iter(self.servers.values()))
        return None  # an opaque token with several providers: asking each would leak it

    async def _introspect(self, server: AuthorizationServer, token: str) -> dict[str, Any] | None:
        import httpx

        try:
            if self.http is not None:
                response = await self._post(self.http, server, token)
            else:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await self._post(client, server, token)
            response.raise_for_status()
            claims = response.json()
        except (httpx.HTTPError, ValueError) as error:
            logger.warning("Token introspection at %s failed: %s", server.issuer, type(error))
            return None
        return claims if isinstance(claims, dict) else None

    @staticmethod
    async def _post(
        client: httpx.AsyncClient, server: AuthorizationServer, token: str
    ) -> httpx.Response:
        return await client.post(
            server.introspection_url,
            data={"token": token, "token_type_hint": "access_token"},
            auth=(server.client_id, server.client_secret.get_secret_value()),
            headers={"Accept": "application/json"},
        )

    def _access(
        self, server: AuthorizationServer, token: str, claims: dict[str, Any], now: float
    ) -> AccessToken | None:
        from mcp.server.auth.provider import AccessToken

        audience = claims.get("aud")
        audiences = audience if isinstance(audience, list) else [audience]
        expires = claims.get("exp")
        tenant = claims.get(self.tenant_claim)
        if (
            claims.get("active") is not True
            or not isinstance(expires, int | float)
            or expires <= now
            or self.resource not in audiences
            or claims.get("iss", server.issuer) != server.issuer
            or not isinstance(tenant, str)
            or tenant not in self.tenants_by_issuer.get(server.issuer, set())
        ):
            return None
        scope = claims.get("scope")
        granted = scope.split() if isinstance(scope, str) else []
        subject = claims.get("sub")
        return AccessToken(
            token=token,
            client_id=str(claims.get("client_id") or subject or "oauth"),
            # Approving is done on the approval page with an approver key, never by token.
            scopes=[name for name in SCOPES if name in granted and name != "runs:approve"],
            expires_at=int(expires),
            resource=self.resource,
            subject=subject if isinstance(subject, str) else None,
            claims={TENANT_CLAIM: tenant, "iss": server.issuer},
        )


def _bounded(cache: dict[str, V]) -> dict[str, V]:
    """Return ``cache`` with room for one more entry, dropping the oldest entries first."""
    while len(cache) >= MAX_CACHED_TOKENS:
        del cache[next(iter(cache))]
    return cache


class FirstOf:
    """A `TokenVerifier` that asks each verifier in turn: API keys first, then OAuth."""

    def __init__(self, *verifiers: TokenVerifier) -> None:
        self.verifiers = verifiers

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return the first verifier's access for ``token``, or None."""
        for verifier in self.verifiers:
            access = await verifier.verify_token(token)
            if access is not None:
                return access
        return None


def protected_resource_routes(resource: str, issuers: list[str]) -> tuple[list[Route], str]:
    """Return the RFC 9728 metadata routes for ``resource`` and the metadata URL for 401s."""
    from mcp.server.auth.routes import (
        build_resource_metadata_url,
        create_protected_resource_routes,
    )
    from pydantic import AnyHttpUrl

    url = AnyHttpUrl(resource)
    routes = create_protected_resource_routes(
        resource_url=url,
        authorization_servers=[AnyHttpUrl(issuer) for issuer in issuers],
        scopes_supported=list(SCOPES),
        resource_name="Quant-Agent-OS",
    )
    return routes, str(build_resource_metadata_url(url))


def _unverified_issuer(token: str) -> str | None:
    """Return a JWT's ``iss`` without verifying it (it only picks a server), or None."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (binascii.Error, ValueError):
        return None
    issuer = payload.get("iss") if isinstance(payload, dict) else None
    return issuer if isinstance(issuer, str) else None
