"""API keys with scopes (decision 0011): generated once, stored only as SHA-256 hashes.

A keys file is TOML with one ``[[keys]]`` table per key::

    [[keys]]
    principal = "alice"
    sha256 = "<64 hex characters>"
    scopes = ["tools:list", "tools:call"]

Keys are 32 random bytes, so a plain SHA-256 is enough to store them; nothing here needs the
``api`` extra.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, Field, ValidationError

from quant_agent_os.tenancy import DEFAULT_TENANT, TENANT_ID

Scope = Literal["tools:list", "tools:call", "runs:read"]
SCOPES: tuple[Scope, ...] = get_args(Scope)
KEY_PREFIX = "qaos_"
PRINCIPAL_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$"


class ApiKeyError(ValueError):
    """Raised when a keys file is missing or invalid, or a key cannot be added."""


class ApiKey(BaseModel):
    """One key's hash, who it belongs to, its tenant (decision 0013), and what it may do."""

    principal: str = Field(pattern=PRINCIPAL_PATTERN)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scopes: frozenset[Scope] = Field(min_length=1)
    tenant: str = Field(default=DEFAULT_TENANT, pattern=TENANT_ID.pattern)
    disabled: bool = False


class _KeyFile(BaseModel):
    keys: list[ApiKey] = Field(default_factory=list)


def new_key() -> str:
    """Return a new random API key."""
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    """Return the SHA-256 (hex) under which ``key`` is stored."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def load_keys(path: Path) -> list[ApiKey]:
    """Read a keys file; raise `ApiKeyError` naming the problem."""
    try:
        data = tomllib.loads(path.read_text("utf-8"))
        keys = _KeyFile.model_validate(data).keys
    except FileNotFoundError as error:
        raise ApiKeyError(
            f"No API keys file at {path}; create one with `quant-agent-os api-key new`."
        ) from error
    except (OSError, tomllib.TOMLDecodeError, ValidationError) as error:
        raise ApiKeyError(f"Invalid API keys file {path}: {error}") from error
    hashes = [key.sha256 for key in keys]
    if len(set(hashes)) != len(hashes):
        raise ApiKeyError(f"Invalid API keys file {path}: a key hash appears twice.")
    return keys


def add_key(
    path: Path, principal: str, scopes: Iterable[Scope], *, tenant: str = DEFAULT_TENANT
) -> str:
    """Generate a key, append its hash to ``path`` (created with mode 0600), and return it.

    The key itself is returned once and never written anywhere. A key acts only within its
    ``tenant``.
    """
    key = new_key()
    entry = ApiKey(
        principal=principal, sha256=hash_key(key), scopes=frozenset(scopes), tenant=tenant
    )
    if path.exists():
        load_keys(path)  # refuse to append to a file the server could not read
    path.parent.mkdir(parents=True, exist_ok=True)
    scope_list = ", ".join(f'"{scope}"' for scope in sorted(entry.scopes))
    # The principal and tenant patterns and the hex hash need no TOML escaping.
    block = (
        f'\n[[keys]]\nprincipal = "{entry.principal}"\nsha256 = "{entry.sha256}"\n'
        f"scopes = [{scope_list}]\n"
        + (f'tenant = "{entry.tenant}"\n' if entry.tenant != DEFAULT_TENANT else "")
    )
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
        handle.write(block)
    return key


class KeyRing:
    """The enabled keys a server accepts."""

    def __init__(self, keys: Iterable[ApiKey]) -> None:
        self._keys = [key for key in keys if not key.disabled]

    def __len__(self) -> int:
        return len(self._keys)

    def authenticate(self, presented: str) -> ApiKey | None:
        """Return the key matching ``presented``, comparing every hash in constant time."""
        digest = hash_key(presented)
        found: ApiKey | None = None
        for key in self._keys:
            if hmac.compare_digest(key.sha256, digest):
                found = key
        return found
