"""Embedding configuration: choose an embedder and read provider keys from the environment.

Keys use each provider's conventional variable (``VOYAGE_API_KEY``, ``OPENAI_API_KEY``) or a
``QUANT_RESEARCH_``-prefixed one, may come from a git-ignored ``.env`` file, are held as
`SecretStr`, and are never displayed.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from quant_research.domain.ports import Embedder
from quant_research.domain.vocabulary import EntityMatcher, Vocabulary
from quant_research.infrastructure.embedding import HashingEmbedder
from quant_research.infrastructure.remote_embedding import (
    EmbeddingServiceError,
    OpenAIEmbedder,
    VoyageEmbedder,
)

EmbedderName = Literal["hashing", "voyage", "openai"]


class EmbeddingSettings(BaseSettings):
    """Provider credentials and defaults for hosted embedders."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    voyage_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("QUANT_RESEARCH_VOYAGE_API_KEY", "VOYAGE_API_KEY"),
    )
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("QUANT_RESEARCH_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    timeout_seconds: float = Field(
        default=30.0, gt=0, le=300, validation_alias="QUANT_RESEARCH_EMBEDDING_TIMEOUT"
    )


def make_embedder(
    name: EmbedderName,
    vocabulary: Vocabulary,
    *,
    model: str | None = None,
    dimensions: int | None = None,
    settings: EmbeddingSettings | None = None,
) -> Embedder:
    """Build the named embedder; hosted ones fail fast with an actionable message without a key."""
    if name == "hashing":
        if model is not None:
            raise EmbeddingServiceError("The hashing embedder has no model option.")
        return HashingEmbedder(EntityMatcher(vocabulary), dimensions=dimensions or 256)
    settings = settings or EmbeddingSettings()
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
