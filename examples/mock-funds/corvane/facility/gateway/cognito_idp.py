"""Corvane Equity Partners' mock identity provider, shaped like an Amazon Cognito user pool.

    python cognito_idp.py --port 9500

It issues RS256 JWTs and publishes the signing keys, and nothing else. Like Cognito it has NO
RFC 7662 introspection endpoint. What it keeps faithful to Cognito:

- Issuer ``https://cognito-idp.<region>.amazonaws.com/<pool id>``, keys at
  ``<issuer>/.well-known/jwks.json`` (served here at ``/.well-known/jwks.json``), a ``kid`` in each
  token's header, and OpenID metadata at ``/.well-known/openid-configuration``.
- Access tokens: ``token_use: access``, ``client_id``, ``username``, ``scope`` (a user pool's
  resource-server scopes look like ``qaos/tools.call``), ``cognito:groups``, ``version: 2``,
  ``event_id``, ``jti``, ``origin_jti``, ``auth_time`` ... and NO ``aud`` claim.
- ID tokens: ``token_use: id``, ``aud`` (the app client), ``cognito:username``, ``cognito:groups``,
  ``email``. A client-credentials access token carries no groups and no username.

Deviations: the issuer in tokens is the real-looking Cognito URL while the keys are served from
wherever this runs, so a verifier is given the keys' address; there is no sign-up, hosted UI,
refresh, or MFA. The ``/oauth2/token`` endpoint takes a username with a ``password`` grant, which a
real pool does not offer to a confidential client; it exists so tests can ask for a token.
"""

from __future__ import annotations

import argparse
import base64
import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

REGION = "us-east-1"
POOL_ID = "us-east-1_AbC123dEf"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL_ID}"
CLIENT_ID = "3n4b5urk1ft4fl3mg5e62d9ado"
KID = "corvane-key-1"
SCOPE_PREFIX = "qaos/"  # the user pool's resource server identifier

# group -> people, as the pool holds them (the groups are what access.toml names)
USERS: dict[str, tuple[str, ...]] = {
    "priya": ("pm-us-core",),
    "grace": ("pm-global-quality",),
    "ines": ("analyst-industrials",),
    "raj": ("risk",),
    "claire": ("client-service-aldergate",),
    "olu": ("compliance",),
    "dev": ("operators",),
}
Clock = Callable[[], float]


def b64(number: int) -> str:
    """Return ``number`` as unpadded base64url bytes, as a JWK writes it."""
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@dataclass
class CognitoPool:
    """The pool: one RSA key, a clock, and the tokens it mints."""

    clock: Clock = time.time
    issuer: str = ISSUER
    client_id: str = CLIENT_ID

    def __post_init__(self) -> None:
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def jwks(self) -> dict[str, Any]:
        """Return the public key set, as ``/.well-known/jwks.json`` serves it."""
        numbers = self._key.public_key().public_numbers()
        return {
            "keys": [
                {
                    "alg": "RS256",
                    "e": b64(numbers.e),
                    "kid": KID,
                    "kty": "RSA",
                    "n": b64(numbers.n),
                    "use": "sig",
                }
            ]
        }

    def _sign(self, claims: dict[str, Any]) -> str:
        return jwt.encode(claims, self._key, algorithm="RS256", headers={"kid": KID})

    def access_token(
        self, username: str, scopes: tuple[str, ...] = ("tools.list", "tools.call"), ttl: int = 3600
    ) -> str:
        """Return a user's access token: groups, scopes, and no ``aud``."""
        now = int(self.clock())
        return self._sign(
            {
                "sub": str(uuid.uuid5(uuid.NAMESPACE_DNS, username)),
                "cognito:groups": list(USERS[username]),
                "iss": self.issuer,
                "version": 2,
                "client_id": self.client_id,
                "origin_jti": str(uuid.uuid4()),
                "event_id": str(uuid.uuid4()),
                "token_use": "access",
                "scope": " ".join(f"{SCOPE_PREFIX}{s}" for s in scopes),
                "auth_time": now,
                "exp": now + ttl,
                "iat": now,
                "jti": str(uuid.uuid4()),
                "username": username,
            }
        )

    def id_token(self, username: str, ttl: int = 3600) -> str:
        """Return a user's ID token, which has ``aud`` (the app client) but is for the app."""
        now = int(self.clock())
        return self._sign(
            {
                "at_hash": "x",
                "sub": str(uuid.uuid5(uuid.NAMESPACE_DNS, username)),
                "cognito:groups": list(USERS[username]),
                "email_verified": True,
                "iss": self.issuer,
                "cognito:username": username,
                "origin_jti": str(uuid.uuid4()),
                "aud": self.client_id,
                "event_id": str(uuid.uuid4()),
                "token_use": "id",
                "auth_time": now,
                "exp": now + ttl,
                "iat": now,
                "jti": str(uuid.uuid4()),
                "email": f"{username}@corvane.example.com",
            }
        )

    def machine_token(self, ttl: int = 3600) -> str:
        """Return a client-credentials access token: a scope, no groups, no username."""
        now = int(self.clock())
        return self._sign(
            {
                "sub": self.client_id,
                "token_use": "access",
                "scope": f"{SCOPE_PREFIX}tools.call",
                "auth_time": now,
                "iss": self.issuer,
                "exp": now + ttl,
                "iat": now,
                "version": 2,
                "jti": str(uuid.uuid4()),
                "client_id": self.client_id,
            }
        )


def metadata(base: str) -> dict[str, Any]:
    """Return OpenID metadata like Cognito's: a jwks_uri, no introspection endpoint."""
    return {
        "issuer": ISSUER,
        "jwks_uri": f"{base}/.well-known/jwks.json",
        "token_endpoint": f"{base}/oauth2/token",
        "userinfo_endpoint": f"{base}/oauth2/userInfo",
        "revocation_endpoint": f"{base}/oauth2/revoke",
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["openid", "email", "phone", "profile"],
    }


class RunningIdp:
    """A mock pool serving over HTTP on an ephemeral loopback port."""

    def __init__(self, pool: CognitoPool, server: ThreadingHTTPServer) -> None:
        self.pool = pool
        self._server = server
        self.url = f"http://127.0.0.1:{server.server_address[1]}"

    def stop(self) -> None:
        """Stop serving."""
        self._server.shutdown()
        self._server.server_close()


def start(pool: CognitoPool | None = None, port: int = 0) -> RunningIdp:
    """Serve ``pool`` on loopback in a thread; ``port`` 0 picks a free port."""
    the_pool = pool or CognitoPool()
    bound: list[int] = []  # the port, known once the server is bound

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
            if self.path == "/.well-known/jwks.json":
                self._send(HTTPStatus.OK, the_pool.jwks())
            elif self.path == "/.well-known/openid-configuration":
                self._send(HTTPStatus.OK, metadata(f"http://127.0.0.1:{bound[0]}"))
            else:
                self._send(HTTPStatus.NOT_FOUND, {"message": "Not Found"})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            form = dict(parse_qsl(self.rfile.read(length).decode()))
            if self.path != "/oauth2/token":
                self._send(HTTPStatus.NOT_FOUND, {"message": "Not Found"})  # incl. /introspect
            elif form.get("grant_type") == "client_credentials":
                token = the_pool.machine_token()
                self._send(
                    HTTPStatus.OK,
                    {"access_token": token, "expires_in": 3600, "token_type": "Bearer"},
                )
            elif form.get("grant_type") == "password" and form.get("username") in USERS:
                token = the_pool.access_token(form["username"])
                self._send(
                    HTTPStatus.OK,
                    {"access_token": token, "expires_in": 3600, "token_type": "Bearer"},
                )
            else:
                self._send(HTTPStatus.BAD_REQUEST, {"error": "invalid_request"})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    bound.append(server.server_address[1])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return RunningIdp(the_pool, server)


def main() -> None:
    """Run the mock pool until interrupted."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--port", type=int, default=9500)
    args = parser.parse_args()
    running = start(port=args.port)
    print(f"Mock Cognito pool at {running.url} (issuer {ISSUER})")
    threading.Event().wait()


if __name__ == "__main__":
    main()
