"""An RFC 7662 introspection endpoint in front of Amazon Cognito, which has none (decision 0028).

The platform's sign-in asks the identity provider whether a token is active. Cognito offers a
JWKS and signed JWT access tokens, not introspection, so Wrenfield runs this small service (a
sidecar container, or a Lambda behind the ALB): it checks the access token's RS256 signature
against the pool's JWKS and answers in the shape the platform expects. What it adds, because
Cognito access tokens do not carry them:
- ``aud``: the platform needs its resource URL there (RFC 8707); Cognito access tokens have no
  ``aud``, so the shim states the one resource it fronts, after checking the token's
  ``client_id`` is an app client Wrenfield issued tokens to;
- ``qaos_tenant``: the firm's tenant, which no Cognito claim names;
- ``scope``: Cognito resource-server scopes (``qaos/tools.call``) become the platform's
  (``tools:list tools:call``).
``cognito:groups`` is passed through unchanged; the sign-in file reads it as the groups claim.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

SCOPES = {"qaos/tools.call": "tools:list tools:call"}


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass
class IntrospectionShim:
    """Checks Cognito access tokens and answers as an RFC 7662 server."""

    issuer: str
    jwks_url: str
    resource: str
    tenant: str
    allowed_clients: frozenset[str]
    caller_id: str
    caller_secret: str
    clock: Callable[[], float] = time.time
    _keys: dict[str, rsa.RSAPublicKey] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _key(self, kid: str) -> rsa.RSAPublicKey | None:
        with self._lock:
            if kid not in self._keys:  # fetched on a miss, which also picks up a rotated key
                with urllib.request.urlopen(self.jwks_url, timeout=5) as response:
                    for jwk in json.loads(response.read())["keys"]:
                        numbers = rsa.RSAPublicNumbers(
                            int.from_bytes(_unb64(jwk["e"]), "big"),
                            int.from_bytes(_unb64(jwk["n"]), "big"),
                        )
                        self._keys[jwk["kid"]] = numbers.public_key()
            return self._keys.get(kid)

    def introspect(self, token: str) -> dict[str, Any]:
        """Return ``{"active": False}`` or the token's claims, as RFC 7662 describes."""
        parts = token.split(".")
        try:
            header = json.loads(_unb64(parts[0]))
            claims = json.loads(_unb64(parts[1]))
            key = self._key(header["kid"]) if header.get("alg") == "RS256" else None
            if key is None or len(parts) != 3:
                return {"active": False}
            key.verify(
                _unb64(parts[2]), f"{parts[0]}.{parts[1]}".encode(), padding.PKCS1v15(),
                hashes.SHA256(),
            )  # fmt: skip
        except (InvalidSignature, ValueError, KeyError, IndexError, OSError):
            return {"active": False}
        expires = claims.get("exp")
        if (
            claims.get("iss") != self.issuer
            or claims.get("token_use") != "access"
            or claims.get("client_id") not in self.allowed_clients
            or not isinstance(expires, int)
            or expires <= self.clock()
        ):
            return {"active": False}
        scope = " ".join(SCOPES[s] for s in str(claims.get("scope", "")).split() if s in SCOPES)
        return {
            "active": True, "token_type": "Bearer", "iss": self.issuer, "sub": claims["sub"],
            "username": claims.get("username"), "client_id": claims["client_id"],
            "exp": expires, "scope": scope, "aud": [self.resource], "qaos_tenant": self.tenant,
            "cognito:groups": claims.get("cognito:groups", []),
        }  # fmt: skip

    def allows(self, authorization: str | None) -> bool:
        """Return whether the caller (the platform) sent the right client credentials."""
        if not authorization or not authorization.startswith("Basic "):
            return False
        decoded = base64.b64decode(authorization[6:]).decode()
        return decoded == f"{self.caller_id}:{self.caller_secret}"


def serve(shim: IntrospectionShim, port: int = 0) -> tuple[ThreadingHTTPServer, threading.Thread]:
    """Serve ``POST /introspect`` in a daemon thread on loopback; the caller stops the server."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            form = dict(parse_qsl(self.rfile.read(length).decode("utf-8")))
            if self.path != "/introspect":
                status, body = 404, {"error": "not_found"}
            elif not shim.allows(self.headers.get("Authorization")):
                status, body = 401, {"error": "invalid_client"}
            else:
                status, body = 200, shim.introspect(form.get("token", ""))
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    return server, thread


def main() -> None:
    """Run the shim for the fund's pool, configured by environment (ECS injects the secret)."""
    import os

    region, pool = os.environ["COGNITO_REGION"], os.environ["COGNITO_POOL_ID"]
    issuer = f"https://cognito-idp.{region}.amazonaws.com/{pool}"
    shim = IntrospectionShim(
        issuer=issuer,
        jwks_url=f"{issuer}/.well-known/jwks.json",
        resource=os.environ["QAOS_RESOURCE_URL"],
        tenant="wrenfield",
        allowed_clients=frozenset(os.environ["COGNITO_APP_CLIENTS"].split(",")),
        caller_id="qaos-server",
        caller_secret=os.environ["WRENFIELD_INTROSPECTION_SECRET"],
    )
    server, thread = serve(shim, int(os.environ.get("SHIM_PORT", "9600")))
    thread.join()
    server.server_close()


if __name__ == "__main__":
    main()
