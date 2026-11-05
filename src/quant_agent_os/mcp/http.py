"""MCP over Streamable HTTP (decisions 0012, 0013): stateless, JSON, keys or OAuth tokens.

`add_mcp_route` adds ``/mcp`` to an app (the REST app, so both share one port). Each request
stands alone: its bearer credential is verified through the SDK's `TokenVerifier` interface
(`KeyVerifier` for API keys; OAuth tokens through another verifier), which names the caller's
tenant in the token's claims. Each MCP method then checks the credential's scope and is served
the caller's tenant surface (`caller_of`), and every call is recorded with actor ``mcp-http``,
the principal, and the tenant. DNS-rebinding protection admits only the allowed hosts. Needs
the ``mcp`` and ``api`` extras.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from quant_agent_os.api.keys import KeyRing
from quant_agent_os.mcp.server import (
    AccessDeniedError,
    ApprovalGate,
    Route,
    Served,
    build_server,
)
from quant_agent_os.runs.recorder import RunRecorder
from quant_agent_os.serving import Tenancy

if TYPE_CHECKING:
    from mcp.server.auth.provider import AccessToken, TokenVerifier
    from starlette.applications import Starlette

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
MAX_REQUEST_BYTES = 5 * 1024 * 1024
TENANT_CLAIM = "tenant"  # set by every verifier from the verified credential, never the request


class KeyVerifier:
    """A `TokenVerifier` over API keys: the key's principal is the client, with its scopes and
    tenant."""

    def __init__(self, keys: KeyRing) -> None:
        self.keys = keys

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return the access the key grants, or None for an unknown or revoked key."""
        from mcp.server.auth.provider import AccessToken

        key = self.keys.authenticate(token)
        if key is None:
            return None
        return AccessToken(
            token=token,
            client_id=key.principal,
            scopes=list(key.scopes),
            claims={TENANT_CLAIM: key.tenant},
        )


@dataclass(frozen=True)
class Caller:
    """Who made a request, and in which tenant."""

    principal: str
    tenant: str


GateFor = Callable[[Caller], ApprovalGate]


def caller_of(request: Any, scope: str) -> Caller:
    """Return the caller of an authenticated request whose credential has ``scope``.

    The tenant comes only from the verified credential's claims; a credential without one is
    refused.
    """
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

    user = request.scope.get("user") if request is not None else None
    if not isinstance(user, AuthenticatedUser):
        raise AccessDeniedError("Send a credential as 'Authorization: Bearer <key or token>'.")
    if scope not in user.scopes:
        raise AccessDeniedError(f"This credential lacks the '{scope}' scope.")
    token = user.access_token
    tenant = (token.claims or {}).get(TENANT_CLAIM)
    if not isinstance(tenant, str):
        raise AccessDeniedError("This credential names no tenant.")
    return Caller(principal=token.subject or token.client_id, tenant=tenant)


def tenant_route(tenancy: Tenancy, gate_for: GateFor | None = None) -> Route:
    """Return the `Route` that serves each caller its tenant's surface, recording as them.

    With ``gate_for``, a call that waits for an approval goes through that caller's gate.
    """

    def route(request: Any, scope: str) -> Served:
        caller = caller_of(request, scope)
        surface = tenancy.surface(caller.tenant)
        if surface is None:
            raise AccessDeniedError(f"Tenant '{caller.tenant}' is not served here.")
        recorder = (
            RunRecorder(
                surface.run_store,
                actor="mcp-http",
                principal=caller.principal,
                tenant=caller.tenant,
                toolsets=surface.toolsets,
            )
            if surface.run_store is not None
            else None
        )
        return Served(surface, recorder, gate_for(caller) if gate_for is not None else None)

    return route


def allowed_hosts(extra: list[str]) -> tuple[list[str], list[str]]:
    """Return the Host and Origin values to admit: loopback on any port, plus ``extra``."""
    hosts = [f"{host}:*" for host in LOOPBACK_HOSTS] + list(LOOPBACK_HOSTS)
    origins = [f"http://{host}:*" for host in LOOPBACK_HOSTS]
    origins += [f"http://{host}" for host in LOOPBACK_HOSTS]
    for host in extra:
        hosts += [host, f"{host}:*"]
        origins += [f"https://{host}", f"https://{host}:*"]
    return hosts, origins


def add_mcp_route(
    app: Starlette,
    tenancy: Tenancy,
    *,
    name: str,
    verifier: TokenVerifier,
    instructions: str | None = None,
    extra_hosts: list[str] | None = None,
    max_request_bytes: int = MAX_REQUEST_BYTES,
    path: str = "/mcp",
    resource_metadata_url: str | None = None,
    gate_for: GateFor | None = None,
) -> None:
    """Serve each caller its tenant's tools, resources, and prompts as MCP at ``path``.

    ``verifier`` checks bearer credentials (`KeyVerifier` for API keys). With
    ``resource_metadata_url``, a 401 points clients to the OAuth Protected Resource Metadata.
    With ``gate_for``, a call that waits for an approval goes through that caller's gate.
    """
    from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend, RequireAuthMiddleware
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from mcp.server.transport_security import TransportSecuritySettings
    from pydantic import AnyHttpUrl
    from starlette.middleware.authentication import AuthenticationMiddleware
    from starlette.routing import Route as StarletteRoute

    server = build_server(
        None, name=name, instructions=instructions, route=tenant_route(tenancy, gate_for)
    )
    hosts, origins = allowed_hosts(extra_hosts or [])
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins
    )
    # A session manager runs once, so each start of the app (a restart, a test) gets its own.
    running: list[StreamableHTTPSessionManager] = []

    async def handle(scope: Any, receive: Any, send: Any) -> None:
        if not running:
            raise RuntimeError("The app's lifespan must run before it serves MCP requests.")
        await running[-1].handle_request(scope, receive, send)

    metadata = AnyHttpUrl(resource_metadata_url) if resource_metadata_url else None
    endpoint = AuthenticationMiddleware(
        RequireAuthMiddleware(handle, required_scopes=[], resource_metadata_url=metadata),
        backend=BearerAuthBackend(verifier),
    )
    app.router.routes.append(
        StarletteRoute(path, endpoint=endpoint, methods=["GET", "POST", "DELETE"])
    )
    previous = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(started: Any) -> AsyncIterator[Any]:
        manager = StreamableHTTPSessionManager(
            app=server,
            json_response=True,
            stateless=True,
            security_settings=security,
            max_request_body_size=max_request_bytes,
        )
        running.append(manager)
        try:
            async with manager.run(), previous(started) as state:
                yield state
        finally:
            running.remove(manager)

    app.router.lifespan_context = lifespan
