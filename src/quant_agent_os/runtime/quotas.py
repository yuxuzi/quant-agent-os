"""What the executor asks before a call starts, so that limits can be added without changing it.

The executor asks its `UsageGate` to admit each call after the call's permissions and input are
checked and before any approval is used or the tool runs. A refusal becomes a ``quota_exceeded``
record with the seconds to wait. A self-hosted server has no gate; `quant_platform.hosted`
implements one with plans (decision 0015).
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
