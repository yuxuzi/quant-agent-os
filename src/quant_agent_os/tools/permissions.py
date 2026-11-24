"""Declared tool capabilities and the least-privilege policy that grants them."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
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
# (decision 0005), unless the operator gave that permission a standing approval: for named tools
# (decision 0030), or for every tool.
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


class Refused(PermissionError):  # noqa: N818 (the firm-facing name, as FirmApp documents it)
    """Raised by a firm's own tool to refuse this caller, with a message the caller may see.

    The executor records the call as ``denied``, with the message, and not as an internal error,
    so a caller is told not to retry. A plain `PermissionError` still becomes an internal error.
    Refuse what the firm's own code decides, such as a desk the caller does not see (decision 0021).
    """


@dataclass(frozen=True)
class PermissionPolicy:
    """Which permissions are granted, which tools may run, and what is pre-approved.

    ``preapproved`` lists consequential permissions the operator approves for every call of every
    tool (a server-wide standing approval). ``preapproved_tools`` maps a consequential permission
    to the exact names of the tools it is approved for (decision 0030), so another tool needing
    the same permission still needs an approval for each call. Both are empty by default, so each
    consequential call needs its own approval. Neither grants anything: a permission must also be
    in ``granted``. Raises `ValueError` for a permission that needs no approval, or a tool name
    that is empty or a pattern.
    """

    granted: frozenset[Permission] = READ_ONLY
    tool_allowlist: frozenset[str] | None = None
    preapproved: frozenset[Permission] = frozenset()
    preapproved_tools: Mapping[Permission, frozenset[str]] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        check_preapproved_tools(self.preapproved_tools)
        frozen = {
            Permission(key): frozenset(names) for key, names in self.preapproved_tools.items()
        }
        object.__setattr__(self, "preapproved_tools", frozen)

    def standing(self, tool_name: str) -> frozenset[Permission]:
        """Return the consequential permissions pre-approved for ``tool_name``: its own or all."""
        own = {p for p, names in self.preapproved_tools.items() if tool_name in names}
        return (self.preapproved | own) & CONSEQUENTIAL

    def needs_approval(
        self, required: Iterable[Permission], tool_name: str | None = None
    ) -> frozenset[Permission]:
        """Return the consequential permissions in ``required`` that ``tool_name`` needs approved.

        Without ``tool_name``, only the server-wide standing approvals count.
        """
        standing = self.standing(tool_name) if tool_name is not None else self.preapproved
        return (frozenset(required) & CONSEQUENTIAL) - standing

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


def check_preapproved_tools(value: Mapping[Permission, Iterable[str]]) -> None:
    """Raise `ValueError` unless ``value`` maps consequential permissions to exact tool names."""
    for permission, names in value.items():
        try:
            known = Permission(permission)
        except ValueError:
            raise ValueError(f"'{permission}' is not a permission.") from None
        if known not in CONSEQUENTIAL:
            raise ValueError(
                f"'{known.value}' needs no approval, so it cannot be pre-approved; name one of "
                + ", ".join(sorted(p.value for p in CONSEQUENTIAL))
                + "."
            )
        if isinstance(names, str):
            raise ValueError(f"Pre-approved tools for '{known.value}' must be a list of names.")
        for name in names:
            if not isinstance(name, str) or not name.strip() or any(c in name for c in "*?[]"):
                raise ValueError(
                    f"Pre-approved tools for '{known.value}' must be exact tool names, not "
                    f"{name!r}: a pattern would approve tools added later."
                )
