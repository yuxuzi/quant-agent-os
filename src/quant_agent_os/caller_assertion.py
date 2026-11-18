"""A signed, short-lived statement of who called a tool, for a firm's service (decision 0030).

`FirmApp.call_service(..., forward_caller=True, caller_secret_env="NAME")` sends one with each
call, in the ``X-QAOS-Caller`` header, so the service can scope its answer to the caller (a PM's
own desk). The format, version 1, is three parts joined by dots::

    v1.<payload>.<signature>

``payload`` is the base64url encoding, without padding, of a compact JSON object with sorted
keys::

    {"exp": 1763460060, "grp": ["equity-desk"], "iat": 1763460000,
     "nonce": "5f0c...32 hex digits", "sub": "pm.equity", "ten": "local", "tool": "risk_desk_var"}

``sub`` is the caller's principal, ``ten`` its tenant, ``grp`` its groups (sorted), ``tool`` the
tool that was called, ``iat`` and ``exp`` whole seconds since the epoch (60 s apart), and
``nonce`` 128 random bits. ``signature`` is the base64url encoding, without padding, of
HMAC-SHA256 over the ASCII bytes ``v1.<payload>``, keyed with the UTF-8 bytes of a secret that
the platform and the service share, at least 32 bytes long.

The service checks it with `verify_caller`. This module uses only the standard library and
imports nothing else of the platform, so a service can import it from the wheel or copy it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

CALLER_HEADER = "X-QAOS-Caller"
VERSION = "v1"
LIFETIME_SECONDS = 60
MAX_LIFETIME_SECONDS = 300  # a verifier refuses an assertion that claims to live longer
DEFAULT_LEEWAY_SECONDS = 10  # clock difference allowed between the platform and the service
MIN_SECRET_BYTES = 32
MAX_HEADER_LENGTH = 8192


class CallerAssertionError(ValueError):
    """Raised when a caller assertion cannot be trusted. Each subclass says why."""


class MalformedAssertionError(CallerAssertionError):
    """The header is not ``v1.<payload>.<signature>``, or its payload is not as documented."""


class UnsupportedVersionError(CallerAssertionError):
    """The header names a version this verifier does not know."""


class BadSignatureError(CallerAssertionError):
    """The signature does not match: another secret, or a changed header."""


class ExpiredAssertionError(CallerAssertionError):
    """The assertion has expired, or was issued in the future."""


class WrongToolError(CallerAssertionError):
    """The assertion was issued for another tool than the one the service answers for."""


class WeakSecretError(ValueError):
    """Raised when the shared secret is shorter than 32 bytes."""


@dataclass(frozen=True)
class VerifiedCaller:
    """A caller assertion whose signature, version, lifetime, and tool were checked."""

    principal: str
    tenant: str
    groups: frozenset[str]
    tool: str
    issued_at: int
    expires_at: int
    nonce: str  # a service that must refuse a replay remembers nonces until they expire


def sign_caller(
    secret: str | bytes,
    *,
    principal: str,
    tenant: str,
    groups: frozenset[str] | set[str] | list[str] | tuple[str, ...],
    tool: str,
    now: float | None = None,
    lifetime: int = LIFETIME_SECONDS,
) -> str:
    """Return a signed assertion that ``principal`` called ``tool``, valid for ``lifetime`` s.

    The platform calls this for each call of a tool registered with ``forward_caller=True``; a
    firm's tests can call it to exercise their service. Raises `WeakSecretError` for a secret
    shorter than 32 bytes, and `ValueError` for an empty principal or tool.
    """
    key = _key(secret)
    if not principal or not tool:
        raise ValueError("A caller assertion needs a principal and a tool.")
    issued = int(time.time() if now is None else now)
    claims = {
        "exp": issued + lifetime,
        "grp": sorted(groups),
        "iat": issued,
        "nonce": secrets.token_hex(16),
        "sub": principal,
        "ten": tenant,
        "tool": tool,
    }
    payload = _b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    signed = f"{VERSION}.{payload}"
    return f"{signed}.{_b64(hmac.new(key, signed.encode('ascii'), hashlib.sha256).digest())}"


def verify_caller(
    header: str | None,
    secret: str | bytes,
    *,
    tool: str | None = None,
    now: float | None = None,
    leeway: int = DEFAULT_LEEWAY_SECONDS,
) -> VerifiedCaller:
    """Return the caller that ``header`` (the ``X-QAOS-Caller`` value) asserts, once checked.

    Checks the version, the signature (in constant time) under ``secret``, the payload's
    fields, that it is neither expired nor issued in the future (``leeway`` seconds allowed
    either way), that it claims to live at most 300 s, and, with ``tool``, that it was issued
    for that tool. Pass ``tool`` always: it stops an assertion for one endpoint being used at
    another that shares the secret. Raises a `CallerAssertionError` subclass naming the
    problem (never the secret), or `WeakSecretError` for a secret shorter than 32 bytes.
    """
    key = _key(secret)
    if not header:
        raise MalformedAssertionError(f"No {CALLER_HEADER} header.")
    if len(header) > MAX_HEADER_LENGTH:
        raise MalformedAssertionError(f"The {CALLER_HEADER} header is too long.")
    parts = header.split(".")
    if len(parts) != 3:
        raise MalformedAssertionError(f"{CALLER_HEADER} is not version.payload.signature.")
    version, payload, signature = parts
    if version != VERSION:
        raise UnsupportedVersionError(f"{CALLER_HEADER} version is not {VERSION}.")
    try:
        signed = f"{version}.{payload}".encode("ascii")
        given = signature.encode("ascii")
    except UnicodeEncodeError:
        raise MalformedAssertionError(f"{CALLER_HEADER} is not ASCII.") from None
    expected = _b64(hmac.new(key, signed, hashlib.sha256).digest()).encode("ascii")
    if not hmac.compare_digest(expected, given):
        raise BadSignatureError(f"{CALLER_HEADER}'s signature does not match.")
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        raise MalformedAssertionError(f"{CALLER_HEADER}'s payload is not JSON.") from None
    caller = _claims(claims)
    moment = time.time() if now is None else now
    if caller.expires_at - caller.issued_at > MAX_LIFETIME_SECONDS:
        raise ExpiredAssertionError(f"{CALLER_HEADER} claims to live too long.")
    if caller.issued_at > moment + leeway:
        raise ExpiredAssertionError(f"{CALLER_HEADER} was issued in the future.")
    if caller.expires_at <= moment - leeway:
        raise ExpiredAssertionError(f"{CALLER_HEADER} has expired.")
    if tool is not None and caller.tool != tool:
        raise WrongToolError(f"{CALLER_HEADER} was issued for another tool.")
    return caller


def _claims(claims: object) -> VerifiedCaller:
    if not isinstance(claims, dict):
        raise MalformedAssertionError(f"{CALLER_HEADER}'s payload is not an object.")
    texts = ("sub", "ten", "tool", "nonce")
    numbers = ("iat", "exp")
    groups = claims.get("grp")
    if (
        any(not isinstance(claims.get(name), str) or not claims[name] for name in texts)
        or any(type(claims.get(name)) is not int for name in numbers)
        or not isinstance(groups, list)
        or any(not isinstance(group, str) for group in groups)
    ):
        raise MalformedAssertionError(f"{CALLER_HEADER}'s payload lacks a field or has a bad one.")
    return VerifiedCaller(
        principal=claims["sub"],
        tenant=claims["ten"],
        groups=frozenset(groups),
        tool=claims["tool"],
        issued_at=claims["iat"],
        expires_at=claims["exp"],
        nonce=claims["nonce"],
    )


def _key(secret: str | bytes) -> bytes:
    key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    if len(key) < MIN_SECRET_BYTES:
        raise WeakSecretError(f"The shared secret must be at least {MIN_SECRET_BYTES} bytes.")
    return key


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")
