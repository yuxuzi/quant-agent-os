"""Declared tool capabilities and the least-privilege policy that grants them."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class Permission(StrEnum):
    """A capability a tool needs; anything beyond reading local data must be granted explicitly."""

    READ_LOCAL_DATA = "read_local_data"
    NETWORK = "network"
    WRITE_LOCAL_DATA = "write_local_data"
    EXECUTE_CODE = "execute_code"
    EXTERNAL_ACTION = "external_action"


READ_ONLY = frozenset({Permission.READ_LOCAL_DATA})
# Granting one of these makes a tool eligible to run; each call also needs an operator approval
# (decision 0005), unless the operator pre-approved that permission for every call.
CONSEQUENTIAL = frozenset(
    {
        Permission.WRITE_LOCAL_DATA,
        Permission.EXECUTE_CODE,
        Permission.NETWORK,
        Permission.EXTERNAL_ACTION,
    }
)


class PermissionDeniedError(PermissionError):
    """Raised when policy forbids a tool call."""


@dataclass(frozen=True)
class PermissionPolicy:
    """Which permissions are granted, which tools may run, and what is pre-approved.

    ``preapproved`` lists consequential permissions the operator approves for every call (a
    standing approval), for example ``network`` when serving with a hosted embedder. It is empty
    by default, so each consequential call needs its own approval.
    """

    granted: frozenset[Permission] = READ_ONLY
    tool_allowlist: frozenset[str] | None = None
    preapproved: frozenset[Permission] = frozenset()

    def needs_approval(self, required: Iterable[Permission]) -> frozenset[Permission]:
        """Return the consequential permissions in ``required`` that are not pre-approved."""
        return (frozenset(required) & CONSEQUENTIAL) - self.preapproved

    def allows_tool(self, tool_name: str) -> bool:
        """Return whether the allowlist (if any) contains ``tool_name``."""
        return self.tool_allowlist is None or tool_name in self.tool_allowlist

    def check(self, tool_name: str, required: Iterable[Permission]) -> None:
        """Raise `PermissionDeniedError` unless the tool is allowed and fully granted."""
        if not self.allows_tool(tool_name):
            raise PermissionDeniedError(f"Tool '{tool_name}' is not in the tool allowlist.")
        missing = sorted(set(required) - self.granted)
        if missing:
            names = ", ".join(permission.value for permission in missing)
            raise PermissionDeniedError(f"Tool '{tool_name}' needs ungranted permissions: {names}.")
