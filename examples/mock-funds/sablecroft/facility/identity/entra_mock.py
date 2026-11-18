"""A mock Microsoft Entra ID (v2.0) token issuer for Sablecroft's tests: stdlib http.server plus
`cryptography` for RS256. It imports nothing of the platform.

What it models, from Entra's documented shapes:
- tokens are JWTs signed RS256, with `kid` in the header; v2.0 access tokens carry `iss`
  `https://login.microsoftonline.com/<tid>/v2.0`, `tid`, `oid`, `sub` (pairwise per app), `aud` (the API's
  application id URI or client id), `azp`, `ver` "2.0", `scp` (delegated, space-separated) or `roles`
  (application permissions), `groups` (security-group object ids, GUIDs), `preferred_username`, `name`.
- keys at `/<tid>/discovery/v2.0/keys`; metadata at `/<tid>/v2.0/.well-known/openid-configuration`;
  tokens at `/<tid>/oauth2/v2.0/token`.
- there is NO RFC 7662 introspection endpoint: any other path answers 404, as Entra does.

Deviations: the `iss` host here is a loopback URL (a real one is login.microsoftonline.com), so the
issuer a verifier trusts is configurable; there is no consent, no `_claim_names` groups overage (more
than 200 groups), no key rollover, and `sub` is the `oid`.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

TENANT_ID = "b1d0c2de-7a61-4f9e-a3b2-5c0d9e8f1a22"
API_APP_ID_URI = "api://sablecroft-qaos"
CLIENT_ID = "0c9f5f5e-2b7a-4a52-9d0c-1f3e6a7b8c90"  # the app registration of the MCP client
KID = "sablecroft-key-1"
SCOPES = "tools.list tools.call"

GROUPS = {
    "sg-sablecroft-cio": "6f1c2a10-0000-4000-8000-00000000c10a",
    "sg-research-eq-ls": "6f1c2a10-0000-4000-8000-0000000e0001",
    "sg-research-macro": "6f1c2a10-0000-4000-8000-0000000e0002",
    "sg-research-event": "6f1c2a10-0000-4000-8000-0000000e0003",
    "sg-research-credit-real-assets": "6f1c2a10-0000-4000-8000-0000000e0004",
    "sg-private-markets": "6f1c2a10-0000-4000-8000-0000000b0001",
    "sg-risk": "6f1c2a10-0000-4000-8000-0000000a0001",
    "sg-investor-relations": "6f1c2a10-0000-4000-8000-0000000d0001",
    "sg-operations": "6f1c2a10-0000-4000-8000-0000000f0001",
}
USERS = {  # upn -> (object id, display name, group names)
    "amira.cole@sablecroft.example": ("a0000000-0000-4000-8000-000000000001", "Amira Cole", ["sg-sablecroft-cio"]),
    "dev.shah@sablecroft.example": ("a0000000-0000-4000-8000-000000000002", "Dev Shah", ["sg-research-eq-ls"]),
    "ines.moreau@sablecroft.example": ("a0000000-0000-4000-8000-000000000003", "Ines Moreau", ["sg-research-credit-real-assets"]),
    "tom.okafor@sablecroft.example": ("a0000000-0000-4000-8000-000000000004", "Tom Okafor", ["sg-private-markets"]),
    "lena.ruiz@sablecroft.example": ("a0000000-0000-4000-8000-000000000005", "Lena Ruiz", ["sg-risk"]),
    "paul.eng@sablecroft.example": ("a0000000-0000-4000-8000-000000000006", "Paul Eng", ["sg-investor-relations"]),
    "nora.fitch@sablecroft.example": ("a0000000-0000-4000-8000-000000000007", "Nora Fitch", ["sg-operations"]),
}


def b64(data: bytes) -> str:
    """Return URL-safe base64 without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class EntraIssuer:
    """Signs tokens and serves keys for one tenant."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.clock = clock
        self.base_url = ""  # set when serving

    @property
    def issuer(self) -> str:
        """The `iss` of v2.0 tokens (here: the loopback base URL, see the module's deviations)."""
        return f"{self.base_url}/{TENANT_ID}/v2.0"

    def jwks(self) -> dict[str, Any]:
        """Return the JSON Web Key Set."""
        numbers = self.key.public_key().public_numbers()

        def int_b64(value: int) -> str:
            return b64(value.to_bytes((value.bit_length() + 7) // 8, "big"))

        return {"keys": [{"kty": "RSA", "use": "sig", "kid": KID, "alg": "RS256",
                          "n": int_b64(numbers.n), "e": int_b64(numbers.e)}]}

    def sign(self, claims: dict[str, Any]) -> str:
        """Return a compact RS256 JWT for ``claims``."""
        header = {"typ": "JWT", "alg": "RS256", "kid": KID}
        signing_input = f"{b64(json.dumps(header).encode())}.{b64(json.dumps(claims).encode())}"
        signature = self.key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{signing_input}.{b64(signature)}"

    def user_token(self, upn: str, *, audience: str = API_APP_ID_URI, lifetime: int = 3600,
                   scp: str = SCOPES) -> str:
        """Return a delegated v2.0 access token for ``upn`` (groups as GUIDs)."""
        oid, name, groups = USERS[upn]
        now = int(self.clock())
        return self.sign({
            "aud": audience, "iss": self.issuer, "iat": now, "nbf": now, "exp": now + lifetime,
            "ver": "2.0", "tid": TENANT_ID, "oid": oid, "sub": oid, "azp": CLIENT_ID, "azpacr": "0",
            "scp": scp, "groups": [GROUPS[g] for g in groups], "name": name,
            "preferred_username": upn,
        })

    def app_token(self, *, audience: str = API_APP_ID_URI, roles: tuple[str, ...] = ("Tools.Call",)) -> str:
        """Return an application (client credentials) token: `roles`, no `scp`, no `groups`."""
        now = int(self.clock())
        return self.sign({
            "aud": audience, "iss": self.issuer, "iat": now, "nbf": now, "exp": now + 3600,
            "ver": "2.0", "tid": TENANT_ID, "oid": CLIENT_ID, "sub": CLIENT_ID, "azp": CLIENT_ID,
            "roles": list(roles), "idtyp": "app",
        })


def _handler(issuer: EntraIssuer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _send(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == f"/{TENANT_ID}/discovery/v2.0/keys":
                self._send(200, issuer.jwks())
            elif self.path == f"/{TENANT_ID}/v2.0/.well-known/openid-configuration":
                self._send(200, {
                    "issuer": issuer.issuer,
                    "jwks_uri": f"{issuer.base_url}/{TENANT_ID}/discovery/v2.0/keys",
                    "token_endpoint": f"{issuer.base_url}/{TENANT_ID}/oauth2/v2.0/token",
                    "id_token_signing_alg_values_supported": ["RS256"],
                })
            else:
                self._send(404, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
            if self.path != f"/{TENANT_ID}/oauth2/v2.0/token":
                self._send(404, {"error": "not_found"})  # including .../introspect: Entra has none
            elif form.get("grant_type") == "client_credentials":
                self._send(200, {"token_type": "Bearer", "expires_in": 3599,
                                 "access_token": issuer.app_token()})
            elif form.get("username") in USERS:
                self._send(200, {"token_type": "Bearer", "expires_in": 3599,
                                 "access_token": issuer.user_token(form["username"])})
            else:
                self._send(400, {"error": "invalid_grant"})

    return Handler


@contextmanager
def serve(issuer: EntraIssuer | None = None) -> Iterator[EntraIssuer]:
    """Serve the issuer on a loopback port for the duration of the block."""
    issuer = issuer or EntraIssuer()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(issuer))
    issuer.base_url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield issuer
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
