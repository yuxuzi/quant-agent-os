"""An RFC 7662 introspection endpoint in front of Cognito, which has none (a workaround).

The platform's sign-in (decision 0028) checks a token by asking an introspection endpoint. A
Cognito user pool publishes signing keys and nothing to introspect, so Corvane runs this small
service (a Lambda behind API Gateway, or a sidecar) that does what Cognito will not: it checks the
token's RS256 signature against the pool's keys, and answers in the shape the platform expects.

What it adds, and why:
- ``aud``: Cognito access tokens have none, and the platform requires its resource URL in it.
- ``scope``: Cognito's ``qaos/tools.call`` becomes the platform's ``tools:call``.
- It accepts access tokens only (``token_use: access``) from Corvane's app clients.

``cognito:groups`` is passed through under that name, so the sign-in file names it as the groups
claim. The service never logs a token. Keys are fetched through ``fetch_jwks``, and kept by the
caller, so a pool's key rotation needs a refresh policy that is not modelled here.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from typing import Any

import jwt
from jwt import PyJWK

SCOPES = {"tools.list": "tools:list", "tools.call": "tools:call", "runs.read": "runs:read"}


def introspect(
    form: Mapping[str, str],
    authorization: str | None,
    *,
    issuer: str,
    resource: str,
    client_ids: frozenset[str],
    fetch_jwks: Callable[[], dict[str, Any]],
    basic: tuple[str, str],
) -> tuple[int, dict[str, Any]]:
    """Return the HTTP status and JSON body for one introspection request."""
    if authorization is None or not authorization.startswith("Basic "):
        return 401, {"error": "invalid_client"}
    try:
        user, _, secret = (
            base64.b64decode(authorization.removeprefix("Basic ")).decode().partition(":")
        )
    except ValueError:
        return 401, {"error": "invalid_client"}
    if (user, secret) != basic:
        return 401, {"error": "invalid_client"}
    token = form.get("token", "")
    inactive: tuple[int, dict[str, Any]] = (200, {"active": False})
    try:
        header = jwt.get_unverified_header(token)
        keys = {k["kid"]: k for k in fetch_jwks()["keys"]}
        key = PyJWK(keys[header["kid"]]).key
        claims = jwt.decode(
            token, key, algorithms=["RS256"], issuer=issuer, options={"verify_aud": False}
        )
    except (jwt.PyJWTError, KeyError, ValueError):
        return inactive
    if claims.get("token_use") != "access" or claims.get("client_id") not in client_ids:
        return inactive
    scopes = [
        SCOPES[s.split("/", 1)[1]]
        for s in claims.get("scope", "").split()
        if "/" in s and s.split("/", 1)[1] in SCOPES
    ]
    body: dict[str, Any] = {
        "active": True,
        "iss": claims["iss"],
        "sub": claims["sub"],
        "exp": claims["exp"],
        "client_id": claims["client_id"],
        "aud": [resource],
        "scope": " ".join(scopes),
    }
    if "cognito:groups" in claims:
        body["cognito:groups"] = claims["cognito:groups"]
    return 200, body
