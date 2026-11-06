"""Who is calling, and what they may reach (decision 0021).

Permissions (`PermissionPolicy`) say what the server may do at all. An `AccessPolicy` says what one
authenticated caller may do: which tools they may call, and which resources they may read. Each
network interface binds its caller here once per request, and the executor carries it onto the
thread a tool runs on, so a tool and a store can ask `current_caller` without it being passed.

Without an access policy nothing here applies, so local use (stdio, the SDK) trusts whoever started
it. With one, anything uncertain is a refusal: no bound caller, or a policy that raises.
"""

from __future__ import annotations

import logging
import threading
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from quant_agent_os.tenancy import DEFAULT_TENANT

logger = logging.getLogger("quant_agent_os.access")

Action = Literal["tool.call", "dataset.read"]


@dataclass(frozen=True)
class Caller:
    """Who made a request, in which tenant, and in which groups. All from a verified credential."""

    principal: str
    tenant: str = DEFAULT_TENANT
    groups: frozenset[str] = frozenset()


class AccessPolicy(Protocol):
    """Decides what a caller may do. A firm's entitlement service implements this."""

    def allows(self, caller: Caller, action: Action, resource: str) -> bool:
        """Return whether ``caller`` may do ``action`` on ``resource``, a tool or dataset name."""
        ...


_caller: ContextVar[Caller | None] = ContextVar("qaos_caller", default=None)
_refusals: ContextVar[list[str] | None] = ContextVar("qaos_refusals", default=None)


def current_caller() -> Caller | None:
    """Return the caller of the request being served, or None for local use."""
    return _caller.get()


def bind_caller(caller: Caller | None) -> None:
    """Make ``caller`` current for the rest of this task, whose first step this must be."""
    _caller.set(caller)


@contextmanager
def acting_as(caller: Caller | None) -> Iterator[None]:
    """Make ``caller`` current within the block."""
    token = _caller.set(caller)
    try:
        yield
    finally:
        _caller.reset(token)


def permits(access: AccessPolicy, action: Action, resource: str) -> bool:
    """Return whether the current caller may do this. No caller, or a failed check, is a no."""
    caller = current_caller()
    if caller is None:
        return False
    try:
        return access.allows(caller, action, resource) is True
    except Exception:
        logger.exception("Refusing %s on %s: the access policy failed.", action, resource)
        return False


def note_refusal(action: Action, resource: str) -> None:
    """Record a refusal for the audit trail: on the call's record, and in the log.

    A refusal can look like a missing resource to the caller, so this is how an auditor sees it.
    """
    caller = current_caller()
    logger.warning(
        "Refused %s on %s for %s.", action, resource, caller.principal if caller else "no caller"
    )
    refusals = _refusals.get()
    if refusals is not None:
        refusals.append(f"{action} {resource}")


@contextmanager
def collecting_refusals() -> Iterator[list[str]]:
    """Yield the list refusals are noted on; a nested call adds to its outer call's list."""
    existing = _refusals.get()
    if existing is not None:
        yield existing
        return
    collected: list[str] = []
    token = _refusals.set(collected)
    try:
        yield collected
    finally:
        _refusals.reset(token)


class AccessFileError(ValueError):
    """Raised when an access file is missing or invalid."""


class _GroupRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tools: list[str] = Field(default_factory=list)
    datasets: list[str] = Field(default_factory=list)


class _AccessFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groups: dict[str, _GroupRules] = Field(default_factory=dict)


def load_access_rules(path: Path) -> dict[str, _GroupRules]:
    """Read an access file; raise `AccessFileError` naming the problem."""
    try:
        return _AccessFile.model_validate(tomllib.loads(path.read_text("utf-8"))).groups
    except FileNotFoundError as error:
        raise AccessFileError(f"No access file at {path}.") from error
    except (OSError, tomllib.TOMLDecodeError, ValidationError) as error:
        raise AccessFileError(f"Invalid access file {path}: {error}") from error


class FileAccessPolicy:
    """Group rules from a TOML file; what is not listed is denied::

        [groups.growth-desk]
        tools = ["analyze_risk", "list_datasets", "desk_*"]
        datasets = ["growth-*", "factors"]

    The file is followed, so a change needs no restart. If it becomes missing or invalid, nothing
    is allowed until it is fixed: a mistyped revocation must not leave access in place.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._stamp = _stamp_of(path)  # first, so a change made while loading is seen next time
        self._rules = load_access_rules(path)
        self._lock = threading.Lock()

    def allows(self, caller: Caller, action: Action, resource: str) -> bool:
        """Return whether one of the caller's groups lists ``resource`` for ``action``."""
        self._reload()
        for group in caller.groups:
            rules = self._rules.get(group)
            if rules is None:
                continue
            patterns = rules.tools if action == "tool.call" else rules.datasets
            if any(fnmatchcase(resource, pattern) for pattern in patterns):
                return True
        return False

    def _reload(self) -> None:
        with self._lock:
            stamp = _stamp_of(self.path)
            if stamp == self._stamp:
                return
            self._stamp = stamp
            try:
                self._rules = load_access_rules(self.path)
            except AccessFileError as error:
                self._rules = {}
                logger.error("Allowing nothing until this is fixed: %s", error)


def _stamp_of(path: Path) -> tuple[int, int, int] | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_ino, info.st_size, info.st_mtime_ns
