"""Larchmere's mock Microsoft Entra ID tenant, and the introspection shim the fund would run.

Faithful to Entra ID's v2.0 access tokens: RS256 JWTs with ``iss``
``https://login.microsoftonline.com/<tid>/v2.0``, ``tid``, ``oid``, ``azp``, ``aud`` (the API's
application ID URI), ``scp`` (space-separated delegated scopes), ``roles`` (app roles), and
``groups`` (security-group object IDs), signed by a key published at ``/<tid>/discovery/v2.0/keys``.
Entra has no RFC 7662 introspection endpoint, so ``/introspect`` here is the *shim*: a small service
the fund would deploy (a Container App, or an API Management policy) that checks the signature
against the JWKS and answers the platform in the shape its sign-in expects.

Needs PyJWT with its crypto extra (installed beside the platform through the mcp package, but not
declared by it). Endpoints (all under one loopback port in tests):

- ``GET  /<tid>/discovery/v2.0/keys``             the JWKS
- ``GET  /<tid>/v2.0/.well-known/openid-configuration``
- ``POST /<tid>/oauth2/v2.0/introspect``          404, as in Entra
- ``POST /shim/introspect``                       the shim (HTTP Basic client, form ``token``)
- ``POST /naive/introspect``                      Entra's claims as they are, ``active`` added
"""

from __future__ import annotations

import base64
import json
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

DIRECTORY = Path(__file__).with_name("directory.json")
KEY_ID = "larchmere-key-1"
Clock = Callable[[], float]


class EntraTenant:
    """One tenant: its signing key, directory, and token issuing."""

    def __init__(self, directory: Path = DIRECTORY, clock: Clock = time.time) -> None:
        self.directory: dict[str, Any] = json.loads(directory.read_text())
        self.clock = clock
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    @property
    def tid(self) -> str:
        """The tenant (directory) ID."""
        return str(self.directory["tenant_id"])

    @property
    def issuer(self) -> str:
        """The v2.0 issuer, as it appears in ``iss``."""
        return f"{self.directory['authority']}/{self.tid}/v2.0"

    @property
    def audience(self) -> str:
        """The API's application ID URI, as it appears in ``aud``."""
        return str(self.directory["api_app_id_uri"])

    def jwks(self) -> dict[str, Any]:
        """Return the public key as a JSON Web Key Set."""
        public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self._key.public_key()))
        return {"keys": [{**public, "kid": KEY_ID, "use": "sig", "alg": "RS256"}]}

    def issue(
        self,
        upn: str,
        *,
        scopes: tuple[str, ...] = ("tools.list", "tools.call"),
        lifetime: float = 3600,
        audience: str | None = None,
        claims: dict[str, Any] | None = None,
    ) -> str:
        """Return an access token for a directory user, as Entra would after sign-in."""
        user = next(u for u in self.directory["users"] if u["upn"] == upn)
        now = int(self.clock())
        body = {
            "aud": audience or self.audience,
            "iss": self.issuer,
            "iat": now,
            "nbf": now,
            "exp": now + int(lifetime),
            "azp": "9a8b7c6d-0000-4000-8000-00000000b002",
            "azpacr": "0",
            "name": upn.split("@")[0].replace(".", " ").title(),
            "oid": user["oid"],
            "preferred_username": upn,
            "rh": "1.AQ-fictional",
            "scp": " ".join(scopes),
            "sub": f"sub-{user['oid'][-4:]}",
            "tid": self.tid,
            "uti": "fictional-uti",
            "ver": "2.0",
            "roles": user["roles"],
            "groups": [self.directory["groups"][name] for name in user["groups"]],
            **(claims or {}),
        }
        return jwt.encode(body, self._key, algorithm="RS256", headers={"kid": KEY_ID})

    def verify(self, token: str) -> dict[str, Any]:
        """Check a token's signature, issuer, audience, and expiry; return its claims."""
        key = jwt.PyJWK(self.jwks()["keys"][0]).key
        claims: dict[str, Any] = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=self.audience,
            issuer=self.issuer,
            options={"verify_exp": False},
        )
        if claims["exp"] <= self.clock():
            raise jwt.ExpiredSignatureError("expired")
        return claims

    def introspect(self, token: str, resource: str) -> dict[str, Any]:
        """Answer as the shim does: RFC 7662 JSON for the platform, from the verified claims.

        The mapping is the fund's: ``scp`` becomes ``scope`` with ``.`` as ``:``; ``aud`` becomes
        the platform's resource URL; ``tid`` becomes the ``qaos_tenant`` claim; ``azp`` is the
        client.
        """
        try:
            claims = self.verify(token)
        except jwt.PyJWTError:
            return {"active": False}
        return {
            "active": True,
            "iss": claims["iss"],
            "aud": [resource],
            "exp": claims["exp"],
            "sub": claims["oid"],
            "client_id": claims["azp"],
            "scope": " ".join(s.replace(".", ":") for s in claims["scp"].split()),
            "groups": claims.get("groups", []),
            "roles": claims.get("roles", []),
            "qaos_tenant": claims["tid"],
        }


def _handler(tenant: EntraTenant, resource: str, shim_secret: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path == f"/{tenant.tid}/discovery/v2.0/keys":
                self._send(HTTPStatus.OK, tenant.jwks())
            elif self.path == f"/{tenant.tid}/v2.0/.well-known/openid-configuration":
                authority = tenant.directory["authority"]
                self._send(
                    HTTPStatus.OK,
                    {
                        "issuer": tenant.issuer,
                        "jwks_uri": f"{authority}/{tenant.tid}/discovery/v2.0/keys",
                        "token_endpoint": f"{authority}/{tenant.tid}/oauth2/v2.0/token",
                    },
                )
            else:
                self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            form = dict(parse_qsl(self.rfile.read(length).decode()))
            if self.path == "/shim/introspect":
                expected = base64.b64encode(f"qaos-server:{shim_secret}".encode()).decode()
                if self.headers.get("Authorization") != f"Basic {expected}":
                    self._send(HTTPStatus.UNAUTHORIZED, {"error": "invalid_client"})
                else:
                    self._send(HTTPStatus.OK, tenant.introspect(form.get("token", ""), resource))
            elif self.path == "/naive/introspect":  # the claims as they are, with active added
                try:
                    claims = tenant.verify(form.get("token", ""))
                except jwt.PyJWTError:
                    self._send(HTTPStatus.OK, {"active": False})
                else:
                    self._send(HTTPStatus.OK, {"active": True, **claims})
            else:  # Entra has no introspection endpoint, and no other path here
                self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    return Handler


@contextmanager
def running(tenant: EntraTenant, resource: str, shim_secret: str) -> Iterator[str]:
    """Serve the tenant's endpoints and the shim on a loopback port; yield the base URL."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(tenant, resource, shim_secret))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
