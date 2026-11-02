"""MCP over Streamable HTTP (decision 0012): stateless, JSON responses, and REST's API keys.

`add_mcp_route` adds ``/mcp`` to an app (the REST app, so both share one port and one set of
keys). Each request stands alone: a bearer ``qaos_`` key is verified through the SDK's
`TokenVerifier` interface (P5's OAuth tokens plug in at the same place), each MCP method then
checks the key's scope (`authorize_request`), and every call is recorded with actor
``mcp-http`` and the key's principal. DNS-rebinding protection admits only the allowed hosts.
Needs the ``mcp`` and ``api`` extras.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from quant_agent_os.api.keys import KeyRing
from quant_agent_os.mcp.server import AccessDeniedError, build_server
from quant_agent_os.runs.recorder import RunRecorder
from quant_agent_os.runs.store import RunStore
from quant_agent_os.runtime.executor import ToolExecutor
from quant_agent_os.tools.catalog import PromptSpec, ResourceSpec

if TYPE_CHECKING:
    from mcp.server.auth.provider import AccessToken
    from starlette.applications import Starlette

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
MAX_REQUEST_BYTES = 5 * 1024 * 1024


class KeyVerifier:
    """A `TokenVerifier` over API keys: the key's principal is the client, its scopes the scopes."""

    def __init__(self, keys: KeyRing) -> None:
        self.keys = keys

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return the access the key grants, or None for an unknown or revoked key."""
        from mcp.server.auth.provider import AccessToken

        key = self.keys.authenticate(token)
        if key is None:
            return None
        return AccessToken(token=token, client_id=key.principal, scopes=list(key.scopes))


def authorize_request(request: Any, scope: str) -> str:
    """Return the principal of an authenticated request whose key has ``scope``."""
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

    user = request.scope.get("user") if request is not None else None
    if not isinstance(user, AuthenticatedUser):
        raise AccessDeniedError("Send an API key as 'Authorization: Bearer <key>'.")
    if scope not in user.scopes:
        raise AccessDeniedError(f"This API key lacks the '{scope}' scope.")
    return user.access_token.client_id


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
    executor: ToolExecutor,
    *,
    name: str,
    keys: KeyRing,
    run_store: RunStore,
    instructions: str | None = None,
    resources: list[ResourceSpec] | None = None,
    prompts: list[PromptSpec] | None = None,
    extra_hosts: list[str] | None = None,
    max_request_bytes: int = MAX_REQUEST_BYTES,
    path: str = "/mcp",
) -> None:
    """Serve ``executor``'s tools, resources, and prompts as MCP at ``path`` on ``app``.

    Raises `ValueError` without an enabled key, as the REST app does.
    """
    from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend, RequireAuthMiddleware
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.middleware.authentication import AuthenticationMiddleware
    from starlette.routing import Route

    if not len(keys):
        raise ValueError("MCP over HTTP needs at least one enabled API key.")
    server = build_server(
        executor,
        name=name,
        instructions=instructions,
        resources=resources,
        prompts=prompts,
        authorize=authorize_request,
        recorder_for=lambda principal: RunRecorder(
            run_store, actor="mcp-http", principal=principal
        ),
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

    endpoint = AuthenticationMiddleware(
        RequireAuthMiddleware(handle, required_scopes=[]),
        backend=BearerAuthBackend(KeyVerifier(keys)),
    )
    app.router.routes.append(Route(path, endpoint=endpoint, methods=["GET", "POST", "DELETE"]))
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
