"""An explicit, in-process registry of tools; no dynamic discovery or code loading."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from quant_agent_os.tools.contracts import ToolSpec


class UnknownToolError(LookupError):
    """Raised when a tool name is not registered."""


class DuplicateToolError(ValueError):
    """Raised when a tool name is registered twice."""


class ToolRegistry:
    """Holds the tools available to an orchestrator or MCP adapter."""

    def __init__(self, tools: Iterable[ToolSpec[Any, Any]] = ()) -> None:
        self._tools: dict[str, ToolSpec[Any, Any]] = {}
        for spec in tools:
            self.register(spec)

    def register(self, spec: ToolSpec[Any, Any]) -> None:
        """Add a tool; names are unique."""
        if spec.name in self._tools:
            raise DuplicateToolError(f"Tool '{spec.name}' is already registered.")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec[Any, Any]:
        """Return the tool called ``name``."""
        try:
            return self._tools[name]
        except KeyError:
            available = ", ".join(sorted(self._tools)) or "none"
            raise UnknownToolError(f"Unknown tool '{name}'. Available: {available}.") from None

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def specs(self) -> list[ToolSpec[Any, Any]]:
        """Return tools in name order."""
        return [self._tools[name] for name in sorted(self._tools)]

    def descriptors(self) -> list[dict[str, Any]]:
        """Return JSON-serializable descriptions of every tool, in name order."""
        return [spec.descriptor() for spec in self.specs()]
