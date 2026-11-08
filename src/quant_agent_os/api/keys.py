"""API keys with scopes (decision 0011): generated once, stored only as SHA-256 hashes.

A keys file is TOML with one ``[[keys]]`` table per key::

    [[keys]]
    principal = "alice"
    sha256 = "<64 hex characters>"
    scopes = ["tools:list", "tools:call"]
    expires = 2027-01-31T00:00:00Z  # optional
    groups = ["growth-desk"]        # optional; what an access policy reads (decision 0021)

Keys are 32 random bytes, so a plain SHA-256 is enough to store them; nothing here needs the
``api`` extra.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import threading
import tomllib
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, get_args

from pydantic import AwareDatetime, BaseModel, Field, ValidationError, model_validator

from quant_agent_os.tenancy import DEFAULT_TENANT, TENANT_ID

Scope = Literal["tools:list", "tools:call", "runs:read", "runs:approve"]
SCOPES: tuple[Scope, ...] = get_args(Scope)
KEY_PREFIX = "qaos_"
logger = logging.getLogger("quant_agent_os.api")
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
    expires: AwareDatetime | None = None
    groups: frozenset[Annotated[str, Field(pattern=PRINCIPAL_PATTERN)]] = frozenset()

    @property
    def expired(self) -> bool:
        """Return whether the key's expiry has passed."""
        return self.expires is not None and self.expires <= datetime.now(UTC)

    @model_validator(mode="after")
    def _approvers_cannot_call(self) -> ApiKey:
        if {"runs:approve", "tools:call"} <= self.scopes:
            raise ValueError(
                "a key with runs:approve cannot also have tools:call: the key an agent's client "
                "holds must never be able to approve (decision 0014)"
            )
        return self


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
            f"No API keys file at {path}; create one with `qaos keys new`."
        ) from error
    except ValidationError as error:  # its plain text repeats the input, which holds key hashes
        problems = "; ".join(
            f"{'.'.join(map(str, item['loc']))}: {item['msg']}" for item in error.errors()
        )
        raise ApiKeyError(f"Invalid API keys file {path}: {problems}") from error
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ApiKeyError(f"Invalid API keys file {path}: {error}") from error
    hashes = [key.sha256 for key in keys]
    if len(set(hashes)) != len(hashes):
        raise ApiKeyError(f"Invalid API keys file {path}: a key hash appears twice.")
    return keys


def add_key(
    path: Path,
    principal: str,
    scopes: Iterable[Scope],
    *,
    tenant: str = DEFAULT_TENANT,
    expires: datetime | None = None,
    groups: Iterable[str] = (),
) -> str:
    """Generate a key, append its hash to ``path`` (created with mode 0600), and return it.

    The key itself is returned once and never written anywhere. A key acts only within its
    ``tenant``, and until ``expires`` if given. ``groups`` are what an access policy reads.
    """
    key = new_key()
    try:
        entry = ApiKey(
            principal=principal,
            sha256=hash_key(key),
            scopes=frozenset(scopes),
            tenant=tenant,
            expires=expires,
            groups=frozenset(groups),
        )
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'key'}: {item['msg']}"
            for item in error.errors()
        )
        raise ApiKeyError(f"Cannot add the key: {problems}") from error
    if path.exists():
        load_keys(path)  # refuse to append to a file the server could not read
    path.parent.mkdir(parents=True, exist_ok=True)
    scope_list = ", ".join(f'"{scope}"' for scope in sorted(entry.scopes))
    # The principal, group, and tenant patterns and the hex hash need no TOML escaping.
    group_list = ", ".join(f'"{group}"' for group in sorted(entry.groups))
    block = (
        f'\n[[keys]]\nprincipal = "{entry.principal}"\nsha256 = "{entry.sha256}"\n'
        f"scopes = [{scope_list}]\n"
        + (f'tenant = "{entry.tenant}"\n' if entry.tenant != DEFAULT_TENANT else "")
        + (f"expires = {entry.expires.isoformat()}\n" if entry.expires else "")
        + (f"groups = [{group_list}]\n" if entry.groups else "")
    )
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
        handle.write(block)
    return key


class KeyRing:
    """The keys a server accepts: enabled ones whose expiry has not passed.

    `from_file` follows a keys file, so issuing or revoking a key needs no restart. If the file
    becomes missing or invalid, no key is accepted until it is fixed: a mistyped revocation must
    not leave the revoked key working.
    """

    def __init__(self, keys: Iterable[ApiKey]) -> None:
        self._keys = [key for key in keys if not key.disabled]
        self._path: Path | None = None
        self._stamp: tuple[int, int, int] | None = None
        self._lock = threading.Lock()

    @classmethod
    def from_file(cls, path: Path) -> KeyRing:
        """Return a ring that follows ``path``; raise `ApiKeyError` if it cannot be read now."""
        stamp = _stamp_of(path)  # first, so a change made while loading is seen next time
        ring = cls(load_keys(path))
        ring._path = path
        ring._stamp = stamp
        return ring

    def __len__(self) -> int:
        self._reload()
        return sum(not key.expired for key in self._keys)

    def enabled(self) -> tuple[ApiKey, ...]:
        """Return the enabled keys as the ring holds them now; never re-raises a read error."""
        self._reload()
        return tuple(self._keys)

    def authenticate(self, presented: str) -> ApiKey | None:
        """Return the key matching ``presented``, comparing every hash in constant time."""
        self._reload()
        digest = hash_key(presented)
        found: ApiKey | None = None
        for key in self._keys:
            if hmac.compare_digest(key.sha256, digest):
                found = key
        return None if found is None or found.expired else found

    def _reload(self) -> None:
        """Re-read the file if it changed; only a change costs more than one `stat`."""
        if self._path is None:
            return
        with self._lock:
            stamp = _stamp_of(self._path)
            if stamp == self._stamp:
                return
            self._stamp = stamp
            try:
                self._keys = [key for key in load_keys(self._path) if not key.disabled]
            except ApiKeyError as error:
                self._keys = []
                logger.error("Accepting no API keys until this is fixed: %s", error)


def _stamp_of(path: Path) -> tuple[int, int, int] | None:
    """Return what identifies a version of ``path``: inode, size, and modification time."""
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_ino, info.st_size, info.st_mtime_ns
