"""A deterministic provider for tests, examples, and offline demonstrations."""

from __future__ import annotations

from collections.abc import Iterable

from quant_agent_os.providers.base import ProviderError


class FakeModelProvider:
    """Return scripted responses in order and record every prompt it receives."""

    def __init__(self, responses: Iterable[str]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    @property
    def name(self) -> str:
        """Return the provider identifier."""
        return "fake"

    def generate(self, prompt: str) -> str:
        """Return the next scripted response."""
        self.prompts.append(prompt)
        if not self._responses:
            raise ProviderError("FakeModelProvider has no scripted responses left.")
        return self._responses.pop(0)
