"""The model-provider boundary (Bring Your Own Model)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class ProviderError(RuntimeError):
    """Raised when a model provider cannot produce a response."""


@runtime_checkable
class ModelProvider(Protocol):
    """Anything that turns a prompt into text. Adapters wrap a vendor SDK behind this."""

    @property
    def name(self) -> str:
        """Return a stable provider identifier recorded in report metadata."""
        ...

    def generate(self, prompt: str) -> str:
        """Return the model's text response to ``prompt``."""
        ...
