"""Wrenfield Credit Opportunities' mock Amazon Cognito user pool: standard library plus RSA from
`cryptography` (RS256, as Cognito signs).

    python cognito_idp.py --port 9500

It models what Cognito gives a resource server, and what it does not:
- ``GET /.well-known/jwks.json`` and ``/.well-known/openid-configuration``;
- ``POST /oauth2/token``: ``client_credentials`` (a confidential app client) and, as a fixture
  deviation from the hosted UI's authorization code flow, ``password``. Access tokens carry
  ``token_use: access``, ``client_id``, ``scope`` (resource server scopes, ``qaos/tools.call``),
  ``cognito:groups``, ``username``, ``jti``, ``origin_jti``, and NO ``aud``; ID tokens carry
  ``aud`` (the client id), ``cognito:username``, ``cognito:groups`` and ``email``;
- ``GET /oauth2/userInfo``;
- NO ``/oauth2/introspect``: Cognito has no RFC 7662 endpoint, so asking for one is a 404.
The ``iss`` claim is ``https://cognito-idp.<region>.amazonaws.com/<pool id>``, though the mock
listens on loopback.
"""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

Clock = Callable[[], float]
DEFAULT_CONFIG = Path(__file__).with_name("users.json")


def b64(data: bytes) -> str:
    """Return unpadded URL-safe base64."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def unb64(text: str) -> bytes:
    """Decode unpadded URL-safe base64."""
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def int_b64(value: int) -> str:
    """Return a big integer as the base64url of its big-endian bytes (a JWK member)."""
    return b64(value.to_bytes((value.bit_length() + 7) // 8, "big"))


class OAuthError(Exception):
    """An error response, as RFC 6749 shapes it."""

    def __init__(self, status: HTTPStatus, error: str) -> None:
        super().__init__(error)
        self.status = status
        self.error = error


@dataclass
class UserPool:
    """A user pool with one app client's tokens, a signing key, and no introspection."""

    config: dict[str, Any]
    clock: Clock = time.time
    ttl_seconds: int = 3600

    def __post_init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = secrets.token_hex(4)
        region, pool = self.config["region"], self.config["pool_id"]
        self.issuer = f"https://cognito-idp.{region}.amazonaws.com/{pool}"
        self._revoked: set[str] = set()

    def jwks(self) -> dict[str, Any]:
        """Return the public key set."""
        numbers = self.key.public_key().public_numbers()
        key = {
            "alg": "RS256", "e": int_b64(numbers.e), "kid": self.kid, "kty": "RSA",
            "n": int_b64(numbers.n), "use": "sig",
        }  # fmt: skip
        return {"keys": [key]}

    def openid_configuration(self, base_url: str) -> dict[str, Any]:
        """Return the discovery document: note the absence of an introspection endpoint."""
        return {
            "issuer": self.issuer,
            "jwks_uri": f"{base_url}/.well-known/jwks.json",
            "token_endpoint": f"{base_url}/oauth2/token",
            "userinfo_endpoint": f"{base_url}/oauth2/userInfo",
            "revocation_endpoint": f"{base_url}/oauth2/revoke",
            "id_token_signing_alg_values_supported": ["RS256"],
            "scopes_supported": ["openid", "email", "phone", "profile"],
        }

    def _sign(self, claims: dict[str, Any]) -> str:
        header = b64(json.dumps({"kid": self.kid, "alg": "RS256"}).encode())
        payload = b64(json.dumps(claims, sort_keys=True).encode())
        signature = self.key.sign(
            f"{header}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256()
        )
        return f"{header}.{payload}.{b64(signature)}"

    def token(self, form: dict[str, str], authorization: str | None) -> dict[str, Any]:
        """Answer a token request with an access token (and, for a user, an ID token)."""
        clients: dict[str, Any] = self.config["app_clients"]
        grant = form.get("grant_type")
        now = int(self.clock())
        scope = (
            form.get("scope")
            or f"{self.config['resource_server']}/tools.list "
            f"{self.config['resource_server']}/tools.call"
        )
        if grant == "client_credentials":
            client_id, _, secret = _basic(authorization)
            client = clients.get(client_id)
            if client is None or client.get("secret") != secret:
                raise OAuthError(HTTPStatus.BAD_REQUEST, "invalid_client")
            groups: list[str] = client.get("groups", [])
            subject = client_id
        elif grant == "password":
            client_id = form.get("client_id", "")
            user = self.config["users"].get(form.get("username", ""))
            if client_id not in clients or user is None or user["password"] != form.get("password"):
                raise OAuthError(HTTPStatus.BAD_REQUEST, "invalid_grant")
            groups, subject = user["groups"], form["username"]
        else:
            raise OAuthError(HTTPStatus.BAD_REQUEST, "unsupported_grant_type")
        jti = secrets.token_hex(8)
        access: dict[str, Any] = {
            "sub": secrets.token_hex(8) if grant == "password" else client_id,
            "iss": self.issuer, "client_id": client_id, "origin_jti": secrets.token_hex(8),
            "event_id": secrets.token_hex(8), "token_use": "access", "scope": scope,
            "auth_time": now, "exp": now + self.ttl_seconds, "iat": now, "jti": jti,
            "version": 2, "username": subject,
        }  # fmt: skip
        if groups:
            access["cognito:groups"] = groups
        body: dict[str, Any] = {
            "access_token": self._sign(access), "expires_in": self.ttl_seconds,
            "token_type": "Bearer",
        }  # fmt: skip
        if grant == "password":
            ident = {
                "sub": access["sub"], "iss": self.issuer, "aud": client_id, "token_use": "id",
                "cognito:username": subject, "cognito:groups": groups, "auth_time": now,
                "exp": now + self.ttl_seconds, "iat": now, "jti": secrets.token_hex(8),
                "email": f"{subject}@wrenfield.example",
            }  # fmt: skip
            body["id_token"] = self._sign(ident)
        return body

    def verify(self, token: str) -> dict[str, Any] | None:
        """Return the claims of a genuine, unexpired token, else None (for userInfo)."""
        parts = token.split(".")
        if len(parts) != 3:
            return None
        try:
            self.key.public_key().verify(
                unb64(parts[2]),
                f"{parts[0]}.{parts[1]}".encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            claims = json.loads(unb64(parts[1]))
        except Exception:
            return None
        ok = isinstance(claims, dict) and claims.get("exp", 0) > self.clock()
        return claims if ok and claims.get("jti") not in self._revoked else None

    def user_info(self, authorization: str | None) -> dict[str, Any]:
        """Answer GET /oauth2/userInfo: needs an access token that carries the openid scope."""
        claims = self.verify((authorization or "").removeprefix("Bearer "))
        if claims is None or claims.get("token_use") != "access":
            raise OAuthError(HTTPStatus.UNAUTHORIZED, "invalid_token")
        if "openid" not in str(claims.get("scope", "")).split():
            raise OAuthError(HTTPStatus.FORBIDDEN, "insufficient_scope")
        return {"sub": claims["sub"], "username": claims["username"]}


def _basic(header: str | None) -> tuple[str, str, str | None]:
    if not header or not header.startswith("Basic "):
        raise OAuthError(HTTPStatus.BAD_REQUEST, "invalid_client")
    client_id, _, secret = unb64(header[6:]).decode().partition(":")
    return client_id, "", secret


class _Handler(BaseHTTPRequestHandler):
    pool: UserPool
    quiet = True

    def do_GET(self) -> None:
        base = f"http://{self.headers.get('Host')}"
        try:
            if self.path == "/.well-known/jwks.json":
                self._send(HTTPStatus.OK, self.pool.jwks())
            elif self.path == "/.well-known/openid-configuration":
                self._send(HTTPStatus.OK, self.pool.openid_configuration(base))
            elif self.path == "/oauth2/userInfo":
                self._send(HTTPStatus.OK, self.pool.user_info(self.headers.get("Authorization")))
            else:
                self._send(HTTPStatus.NOT_FOUND, {"message": "not found"})
        except OAuthError as error:
            self._send(error.status, {"error": error.error})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        form = dict(parse_qsl(self.rfile.read(length).decode("utf-8")))
        if self.path != "/oauth2/token":  # including /oauth2/introspect: there is none
            self._send(HTTPStatus.NOT_FOUND, {"message": "not found"})
            return
        try:
            self._send(HTTPStatus.OK, self.pool.token(form, self.headers.get("Authorization")))
        except OAuthError as error:
            self._send(error.status, {"error": error.error})

    def _send(self, status: HTTPStatus, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        if not self.quiet:
            super().log_message(format, *args)


@dataclass
class RunningPool:
    """A pool serving in a background thread."""

    base_url: str
    pool: UserPool
    server: ThreadingHTTPServer
    thread: threading.Thread

    def stop(self) -> None:
        """Stop serving and release the port."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def __enter__(self) -> RunningPool:
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


def start(config: Path = DEFAULT_CONFIG, port: int = 0, *, clock: Clock = time.time) -> RunningPool:
    """Serve in a daemon thread on loopback; call `stop` when done."""
    pool = UserPool(json.loads(config.read_text("utf-8")), clock=clock)
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    server.daemon_threads = True
    server.RequestHandlerClass = type("Handler", (_Handler,), {"pool": pool})
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    return RunningPool(f"http://127.0.0.1:{server.server_address[1]}", pool, server, thread)


def main(argv: Sequence[str] | None = None) -> None:
    """Serve from the command line until interrupted."""
    parser = argparse.ArgumentParser(description="Wrenfield mock Cognito user pool")
    parser.add_argument("--port", type=int, default=9500)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    running = start(args.config, args.port)
    print(f"Mock Cognito at {running.base_url} (iss {running.pool.issuer}); Ctrl-C to stop.")
    try:
        running.thread.join()
    except KeyboardInterrupt:
        running.stop()


if __name__ == "__main__":
    main()
