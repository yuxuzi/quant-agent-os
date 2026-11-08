"""What the executor asks before a call starts, so that limits can be added without changing it.

The executor asks its `UsageGate` to admit each call after the call's permissions and input are
checked and before any approval is used or the tool runs. A refusal becomes a ``quota_exceeded``
record with the seconds to wait. The platform's servers have no gate (decision 0023); the hook
remains for a firm's own server.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Refusal:
    """Why a call was not admitted, and how many seconds until it can be."""

    message: str
    retry_after_seconds: int


class UsageGate(Protocol):
    """Admits a call before it starts, or refuses it with a `Refusal`."""

    def admit(self, tool: str) -> Refusal | None:
        """Return None to admit a call of ``tool``, or why it must wait."""
        ...
