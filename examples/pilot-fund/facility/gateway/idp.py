"""Kestrel Point Capital's mock identity provider: issues tokens and answers introspection.

    python idp.py --port 9400 --config users.json

Standard library only. Endpoints:

- ``POST /token``: the ``password`` grant for a configured user, or ``client_credentials`` for a
  service client authenticated by HTTP Basic. Tokens are JWT-shaped and signed with HMAC-SHA256.
- ``POST /introspect``: RFC 7662, for a client allowed to introspect, authenticated by HTTP Basic.
- ``POST /revoke``: RFC 7009, for any confidential client.
- ``GET /.well-known/oauth-authorization-server``: RFC 8414 metadata.

A token is inactive once it expires, is revoked, or its user is disabled. For tests, `start` runs
the provider in a thread on an ephemeral port, with an injectable clock.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

Clock = Callable[[], float]
METADATA_PATH = "/.well-known/oauth-authorization-server"
DEFAULT_CONFIG = Path(__file__).with_name("users.json")


class ConfigError(ValueError):
    """Raised when the provider's configuration is invalid."""


@dataclass(frozen=True)
class User:
    """A person who signs in with a password."""

    name: str
    password: str
    groups: tuple[str, ...]
    tenant: str | None = None
    scope: str | None = None
    disabled: bool = False


@dataclass(frozen=True)
class Client:
    """An OAuth client: public (no secret), or confidential with its allowed uses."""

    client_id: str
    secret: str | None
    introspect: bool = False
    client_credentials: bool = False
    groups: tuple[str, ...] = ()


@dataclass(frozen=True)
class IdpConfig:
    """What `users.json` holds."""

    audience: tuple[str, ...]
    tenant: str
    signing_key: str
    issuer: str | None = None
    tenant_claim: str = "qaos_tenant"
    groups_claim: str = "groups"
    scope: str = "tools:list tools:call"
    token_ttl_seconds: int = 3600
    users: dict[str, User] = field(default_factory=dict)
    clients: dict[str, Client] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IdpConfig:
        """Build a configuration from parsed JSON; raise `ConfigError` naming the problem."""
        try:
            audience = data["audience"]
            users = {
                name: User(
                    name=name,
                    password=str(spec["password"]),
                    groups=tuple(spec.get("groups", [])),
                    tenant=spec.get("tenant"),
                    scope=spec.get("scope"),
                    disabled=bool(spec.get("disabled", False)),
                )
                for name, spec in data.get("users", {}).items()
            }
            clients = {
                client_id: Client(
                    client_id=client_id,
                    secret=spec.get("secret"),
                    introspect=bool(spec.get("introspect", False)),
                    client_credentials="client_credentials" in spec.get("grants", []),
                    groups=tuple(spec.get("groups", [])),
                )
                for client_id, spec in data.get("clients", {}).items()
            }
            return cls(
                audience=(audience,) if isinstance(audience, str) else tuple(audience),
                tenant=str(data["tenant"]),
                signing_key=str(data["signing_key"]),
                issuer=data.get("issuer"),
                tenant_claim=str(data.get("tenant_claim", "qaos_tenant")),
                groups_claim=str(data.get("groups_claim", "groups")),
                scope=str(data.get("scope", "tools:list tools:call")),
                token_ttl_seconds=int(data.get("token_ttl_seconds", 3600)),
                users=users,
                clients=clients,
            )
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise ConfigError(f"Invalid identity provider configuration: {error!r}") from error

    @classmethod
    def load(cls, path: Path) -> IdpConfig:
        """Read a configuration file."""
        try:
            return cls.from_dict(json.loads(path.read_text("utf-8")))
        except (OSError, json.JSONDecodeError) as error:
            raise ConfigError(f"Cannot read {path}: {error}") from error


class OAuthError(Exception):
    """An error response from an endpoint, as RFC 6749 section 5.2 shapes it."""

    def __init__(self, status: HTTPStatus, error: str, description: str) -> None:
        super().__init__(description)
        self.status = status
        self.error = error
        self.description = description


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class IdentityProvider:
    """Issues, checks, and revokes tokens. Thread-safe; knows nothing of HTTP."""

    def __init__(self, config: IdpConfig, *, base_url: str, clock: Clock = time.time) -> None:
        self.config = config
        self.base_url = base_url
        self.issuer = config.issuer or base_url
        self.clock = clock
        self._key = config.signing_key.encode("utf-8")
        self._revoked: set[str] = set()
        self._disabled: set[str] = {name for name, user in config.users.items() if user.disabled}
        self._lock = threading.Lock()

    def disable_user(self, name: str) -> None:
        """Disable a user: their tokens become inactive and they cannot get new ones."""
        with self._lock:
            self._disabled.add(name)

    def authenticate_client(self, authorization: str | None) -> Client:
        """Return the confidential client named by an HTTP Basic header, or raise."""
        refused = OAuthError(
            HTTPStatus.UNAUTHORIZED, "invalid_client", "Client authentication failed."
        )
        if not authorization or not authorization.startswith("Basic "):
            raise refused
        try:
            client_id, _, secret = _unb64(authorization[6:].strip()).decode("utf-8").partition(":")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            raise refused from None
        client = self.config.clients.get(client_id)
        if client is None or client.secret is None:
            raise refused
        if not hmac.compare_digest(client.secret.encode(), secret.encode()):
            raise refused
        return client

    def token(self, form: dict[str, str], authorization: str | None) -> dict[str, Any]:
        """Answer a token request."""
        grant = form.get("grant_type")
        if grant == "password":
            client_id = form.get("client_id", "kpc-cli")
            if authorization:
                client_id = self.authenticate_client(authorization).client_id
            elif client_id not in self.config.clients:
                raise OAuthError(HTTPStatus.UNAUTHORIZED, "invalid_client", "Unknown client.")
            user = self.config.users.get(form.get("username", ""))
            password = form.get("password", "")
            if user is None or not hmac.compare_digest(user.password.encode(), password.encode()):
                raise OAuthError(
                    HTTPStatus.BAD_REQUEST, "invalid_grant", "Bad username or password."
                )
            with self._lock:
                if user.name in self._disabled:
                    raise OAuthError(HTTPStatus.BAD_REQUEST, "invalid_grant", "User is disabled.")
            return self._issue(user.name, user.groups, client_id, user.tenant, user.scope, form)
        if grant == "client_credentials":
            client = self.authenticate_client(authorization)
            if not client.client_credentials:
                raise OAuthError(
                    HTTPStatus.BAD_REQUEST, "unauthorized_client", "Grant not allowed for client."
                )
            return self._issue(client.client_id, client.groups, client.client_id, None, None, form)
        raise OAuthError(HTTPStatus.BAD_REQUEST, "unsupported_grant_type", f"Grant {grant!r}.")

    def _issue(
        self,
        subject: str,
        groups: Sequence[str],
        client_id: str,
        tenant: str | None,
        allowed_scope: str | None,
        form: dict[str, str],
    ) -> dict[str, Any]:
        allowed = (allowed_scope or self.config.scope).split()
        requested = form.get("scope", "").split() or allowed
        if not set(requested) <= set(allowed):
            raise OAuthError(HTTPStatus.BAD_REQUEST, "invalid_scope", "Scope not allowed.")
        ttl = self.config.token_ttl_seconds
        if "expires_in" in form:
            try:
                ttl = max(1, min(ttl, int(form["expires_in"])))
            except ValueError:
                raise OAuthError(
                    HTTPStatus.BAD_REQUEST, "invalid_request", "expires_in must be an integer."
                ) from None
        now = int(self.clock())
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "sub": subject,
            "aud": list(self.config.audience),
            "iat": now,
            "exp": now + ttl,
            "jti": secrets.token_hex(8),
            "client_id": client_id,
            "scope": " ".join(requested),
            self.config.tenant_claim: tenant or self.config.tenant,
            self.config.groups_claim: list(groups),
        }
        return {
            "access_token": self._sign(claims),
            "token_type": "Bearer",
            "expires_in": ttl,
            "scope": claims["scope"],
        }

    def _sign(self, claims: dict[str, Any]) -> str:
        header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload = _b64(json.dumps(claims, sort_keys=True).encode())
        mac = hmac.new(self._key, f"{header}.{payload}".encode(), hashlib.sha256).digest()
        return f"{header}.{payload}.{_b64(mac)}"

    def claims_of(self, token: str) -> dict[str, Any] | None:
        """Return a token's claims if it is genuine and active, else None."""
        parts = token.split(".")
        if len(parts) != 3:
            return None
        expected = hmac.new(self._key, f"{parts[0]}.{parts[1]}".encode(), hashlib.sha256).digest()
        try:
            if not hmac.compare_digest(expected, _unb64(parts[2])):
                return None
            claims = json.loads(_unb64(parts[1]))
        except (binascii.Error, ValueError):
            return None
        if not isinstance(claims, dict):
            return None
        with self._lock:
            if claims.get("jti") in self._revoked or claims.get("sub") in self._disabled:
                return None
        if not isinstance(claims.get("exp"), int) or claims["exp"] <= self.clock():
            return None
        return claims

    def introspect(self, form: dict[str, str], authorization: str | None) -> dict[str, Any]:
        """Answer an RFC 7662 introspection request."""
        client = self.authenticate_client(authorization)
        if not client.introspect:
            raise OAuthError(HTTPStatus.FORBIDDEN, "unauthorized_client", "May not introspect.")
        claims = self.claims_of(form.get("token", ""))
        if claims is None:
            return {"active": False}
        return {"active": True, "token_type": "Bearer", "username": claims["sub"], **claims}

    def revoke(self, form: dict[str, str], authorization: str | None) -> dict[str, Any]:
        """Answer an RFC 7009 revocation request; unknown tokens are not an error."""
        self.authenticate_client(authorization)
        claims = self.claims_of(form.get("token", ""))
        if claims is not None:
            with self._lock:
                self._revoked.add(str(claims["jti"]))
        return {}

    def metadata(self) -> dict[str, Any]:
        """Return the RFC 8414 authorization server metadata."""
        return {
            "issuer": self.issuer,
            "token_endpoint": f"{self.base_url}/token",
            "introspection_endpoint": f"{self.base_url}/introspect",
            "revocation_endpoint": f"{self.base_url}/revoke",
            "grant_types_supported": ["password", "client_credentials"],
            "response_types_supported": [],
            "scopes_supported": sorted(set(self.config.scope.split())),
            "token_endpoint_auth_methods_supported": ["client_secret_basic", "none"],
            "introspection_endpoint_auth_methods_supported": ["client_secret_basic"],
            "revocation_endpoint_auth_methods_supported": ["client_secret_basic"],
        }


class _Handler(BaseHTTPRequestHandler):
    provider: IdentityProvider  # set on the subclass `serve` makes
    quiet: bool = False

    def do_GET(self) -> None:
        if self.path == METADATA_PATH:
            self._send(HTTPStatus.OK, self.provider.metadata())
        else:
            self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:
        routes = {
            "/token": self.provider.token,
            "/introspect": self.provider.introspect,
            "/revoke": self.provider.revoke,
        }
        handler = routes.get(self.path)
        if handler is None:
            self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        form = dict(parse_qsl(self.rfile.read(length).decode("utf-8")))
        try:
            self._send(HTTPStatus.OK, handler(form, self.headers.get("Authorization")))
        except OAuthError as error:
            extra = {"WWW-Authenticate": 'Basic realm="kpc"'} if error.status == 401 else {}
            body = {"error": error.error, "error_description": error.description}
            self._send(error.status, body, extra)

    def _send(
        self, status: HTTPStatus, body: dict[str, Any], headers: dict[str, str] | None = None
    ) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        if not self.quiet:
            super().log_message(format, *args)


@dataclass
class RunningIdp:
    """A provider serving in a background thread."""

    base_url: str
    provider: IdentityProvider
    server: ThreadingHTTPServer
    thread: threading.Thread

    def stop(self) -> None:
        """Stop serving and release the port."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def __enter__(self) -> RunningIdp:
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


def serve(
    config: IdpConfig,
    host: str = "127.0.0.1",
    port: int = 0,
    *,
    clock: Clock = time.time,
    quiet: bool = False,
) -> tuple[ThreadingHTTPServer, IdentityProvider]:
    """Bind a server for ``config``; its issuer defaults to its own base URL."""
    server = ThreadingHTTPServer((host, port), _Handler)
    server.daemon_threads = True
    base_url = f"http://{host}:{server.server_address[1]}"
    provider = IdentityProvider(config, base_url=base_url, clock=clock)
    server.RequestHandlerClass = type(
        "Handler", (_Handler,), {"provider": provider, "quiet": quiet}
    )
    return server, provider


def start(
    config: IdpConfig | Path = DEFAULT_CONFIG,
    host: str = "127.0.0.1",
    port: int = 0,
    *,
    clock: Clock = time.time,
) -> RunningIdp:
    """Serve in a daemon thread, on an ephemeral port by default; call `stop` when done."""
    if isinstance(config, Path):
        config = IdpConfig.load(config)
    server, provider = serve(config, host, port, clock=clock, quiet=True)
    thread = threading.Thread(
        target=server.serve_forever, args=(0.05,), name="kpc-idp", daemon=True
    )
    thread.start()
    return RunningIdp(f"http://{host}:{server.server_address[1]}", provider, server, thread)


def main(argv: Sequence[str] | None = None) -> None:
    """Serve from the command line until interrupted."""
    parser = argparse.ArgumentParser(description="Kestrel Point Capital mock identity provider")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9400)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    server, provider = serve(IdpConfig.load(args.config), args.host, args.port)
    print(f"Mock identity provider at {provider.issuer} (issuer); Ctrl-C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
