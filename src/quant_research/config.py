"""Embedding configuration: choose an embedder and read provider keys from the environment.

Keys use each provider's conventional variable (``VOYAGE_API_KEY``, ``OPENAI_API_KEY``), may come
from the ``.env`` file in the platform's home, are held as `SecretStr`, and are never displayed.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from quant_agent_os.config import home_dir, home_env_file
from quant_research.domain.ports import Embedder
from quant_research.infrastructure.local_embedding import DEFAULT_MODEL, LocalEmbedder
from quant_research.infrastructure.remote_embedding import (
    EmbeddingServiceError,
    OpenAIEmbedder,
    VoyageEmbedder,
)

EmbedderName = Literal["local", "voyage", "openai"]


class EmbeddingSettings(BaseSettings):
    """Provider credentials and defaults for hosted embedders."""

    model_config = SettingsConfigDict(extra="ignore")

    voyage_api_key: SecretStr | None = Field(default=None, validation_alias="VOYAGE_API_KEY")
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    timeout_seconds: float = Field(
        default=30.0, gt=0, le=300, validation_alias="QUANT_AGENT_OS_EMBEDDING_TIMEOUT"
    )


def make_embedder(
    name: EmbedderName,
    *,
    model: str | None = None,
    dimensions: int | None = None,
    settings: EmbeddingSettings | None = None,
) -> Embedder:
    """Build the named embedder; hosted ones fail fast with an actionable message without a key."""
    if name == "local":
        return LocalEmbedder(model or DEFAULT_MODEL, cache_dir=home_dir() / "models")
    settings = settings or EmbeddingSettings(_env_file=home_env_file())
    if name == "voyage":
        if settings.voyage_api_key is None:
            raise EmbeddingServiceError("The voyage embedder needs VOYAGE_API_KEY to be set.")
        return VoyageEmbedder(
            settings.voyage_api_key,
            model=model or "voyage-finance-2",
            dimensions=dimensions,
            timeout=settings.timeout_seconds,
        )
    if settings.openai_api_key is None:
        raise EmbeddingServiceError("The openai embedder needs OPENAI_API_KEY to be set.")
    return OpenAIEmbedder(
        settings.openai_api_key,
        model=model or "text-embedding-3-small",
        dimensions=dimensions,
        timeout=settings.timeout_seconds,
    )
