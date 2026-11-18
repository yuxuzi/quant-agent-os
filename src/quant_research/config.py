"""Embedding and parsing configuration: choose an embedder or a PDF parser, with keys from the
environment.

Keys use each provider's conventional variable (``VOYAGE_API_KEY``, ``OPENAI_API_KEY``), may come
from the ``.env`` file in the platform's home, are held as `SecretStr`, and are never displayed.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from quant_agent_os.config import home_dir, home_env_file
from quant_research.domain.ports import Embedder, SourceReadError
from quant_research.infrastructure.local_embedding import DEFAULT_MODEL, LocalEmbedder
from quant_research.infrastructure.ragflow import RagflowPdfLoader
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


class RagflowSettings(BaseSettings):
    """Where a firm's RAGFlow server is, for parsing PDFs at ingestion (``--pdf-parser``)."""

    model_config = SettingsConfigDict(extra="ignore")

    url: str | None = Field(default=None, validation_alias="RAGFLOW_URL")
    api_key: SecretStr | None = Field(default=None, validation_alias="RAGFLOW_API_KEY")
    parse_timeout_seconds: float = Field(
        default=600.0, gt=0, le=3600, validation_alias="RAGFLOW_PARSE_TIMEOUT"
    )


def make_ragflow_loader(settings: RagflowSettings | None = None) -> RagflowPdfLoader:
    """Build the RAGFlow PDF loader; fails fast with an actionable message when unconfigured."""
    settings = settings or RagflowSettings(_env_file=home_env_file())
    if not settings.url or settings.api_key is None:
        raise SourceReadError(
            "The RAGFlow PDF parser needs RAGFLOW_URL and RAGFLOW_API_KEY to be set."
        )
    return RagflowPdfLoader(
        settings.url, settings.api_key, parse_timeout=settings.parse_timeout_seconds
    )
