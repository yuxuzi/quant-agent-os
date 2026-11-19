"""A sidecar that turns Microsoft Entra ID v2.0 access tokens into RFC 7662 introspection answers.

Why it exists: the platform's sign-in (decision 0028, `IntrospectionVerifier`) asks an authorization
server to introspect each token, and Entra ID has no introspection endpoint. This shim validates the
JWT locally (signature against the tenant's JWKS, `iss`, `aud`, `exp`/`nbf`) and answers in the
shape
the platform reads: `active`, `exp`, `aud`, `iss`, `sub`, `client_id`, `scope` (Entra's `scp`, with
dots mapped to the platform's colons), and `groups` (Entra's group GUIDs, unchanged: `access.toml`
names the GUIDs). It runs beside the server in the same container group and listens on loopback
only.

It is the fund's own workaround, not a recommendation: see FINDINGS.md (Cloud) for what the platform
could do instead (JWKS validation in the platform). Needs PyJWT with its crypto extra, which the
repo
has only through `mcp`, not as a declared dependency.
"""

from __future__ import annotations

import base64
import json
import threading
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs

import jwt


class EntraValidator:
    """Validates Entra v2.0 access tokens for one tenant and one audience."""

    def __init__(self, issuer: str, jwks_url: str, audience: str) -> None:
        self.issuer, self.jwks_url, self.audience = issuer, jwks_url, audience
        self._keys: dict[str, Any] = {}

    def _key(self, kid: str) -> Any:
        if kid not in self._keys:  # fetched on first use and when a new kid appears (rollover)
            with urllib.request.urlopen(self.jwks_url, timeout=5) as response:
                keys = json.loads(response.read())["keys"]
            self._keys = {k["kid"]: jwt.PyJWK(k).key for k in keys}
        return self._keys[kid]

    def claims(self, token: str) -> dict[str, Any] | None:
        """Return the token's claims when it is valid for this tenant and audience, else None."""
        try:
            kid = jwt.get_unverified_header(token).get("kid", "")
            decoded: dict[str, Any] = jwt.decode(
                token,
                self._key(kid),
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
            return decoded
        except (jwt.PyJWTError, KeyError, OSError, ValueError):
            return None

    def introspection(self, token: str) -> dict[str, Any]:
        """Return the RFC 7662 answer for ``token``: `{"active": false}` unless it is valid."""
        claims = self.claims(token)
        if claims is None:
            return {"active": False}
        scope = str(claims.get("scp", "")).replace(".", ":")
        return {
            "active": True,
            "exp": claims["exp"],
            "iss": claims["iss"],
            "aud": claims["aud"],
            "sub": claims.get("oid") or claims.get("sub"),
            "client_id": claims.get("azp"),
            "scope": scope,
            "groups": claims.get("groups"),
            "tid": claims.get("tid"),
        }


@contextmanager
def serve_introspection(
    validator: EntraValidator, client_id: str, client_secret: str
) -> Iterator[str]:
    """Serve POST /introspect on a loopback port, with HTTP Basic auth; yield its URL."""
    expected = "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            form = parse_qs(self.rfile.read(length).decode())
            if self.path != "/introspect" or self.headers.get("Authorization") != expected:
                self.send_response(401)
                self.end_headers()
                return
            body = json.dumps(validator.introspection(form.get("token", [""])[0])).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/introspect"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
