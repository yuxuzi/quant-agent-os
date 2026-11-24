"""Embedding and parsing configuration: choose an embedder or a PDF parser, with keys from the
environment.

Keys use each provider's conventional variable (``VOYAGE_API_KEY``, ``OPENAI_API_KEY``, and
``RAGFLOW_API_KEY``), or ``NAME_FILE`` naming a file that holds it. `read_secret` reads them from
the environment, then from the platform's ``.env``. They are held as `SecretStr`, never displayed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, Field, SecretStr

from quant_agent_os.config import SecretConfigError, home_dir, read_secret, secret_sources
from quant_research.domain.ports import Embedder, SourceReadError
from quant_research.infrastructure.local_embedding import DEFAULT_MODEL, LocalEmbedder
from quant_research.infrastructure.ragflow import RagflowPdfLoader
from quant_research.infrastructure.remote_embedding import (
    EmbeddingServiceError,
    OpenAIEmbedder,
    VoyageEmbedder,
)

EmbedderName = Literal["local", "voyage", "openai"]


class EmbeddingSettings(BaseModel):
    """Provider keys and defaults for hosted embedders. Build one with `embedding_settings`."""

    voyage_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)


def _key(value: str | None) -> SecretStr | None:
    return SecretStr(value) if value else None


def embedding_settings(environ: Mapping[str, str | None] | None = None) -> EmbeddingSettings:
    """Return the hosted embedders' settings from ``environ``, or from the environment and ``.env``.

    Raises `SecretConfigError` for a key given twice or an unreadable key file, and a validation
    error for a timeout that is not a number from 0 (exclusive) to 300.
    """
    values = secret_sources() if environ is None else environ
    fields: dict[str, object] = {
        "voyage_api_key": _key(read_secret("VOYAGE_API_KEY", values)),
        "openai_api_key": _key(read_secret("OPENAI_API_KEY", values)),
    }
    if values.get("QUANT_AGENT_OS_EMBEDDING_TIMEOUT"):
        fields["timeout_seconds"] = values["QUANT_AGENT_OS_EMBEDDING_TIMEOUT"]
    return EmbeddingSettings.model_validate(fields)


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
    try:
        settings = settings if settings is not None else embedding_settings()
    except SecretConfigError as error:
        raise EmbeddingServiceError(str(error)) from None
    if name == "voyage":
        if settings.voyage_api_key is None:
            raise EmbeddingServiceError(
                "The voyage embedder needs VOYAGE_API_KEY (or VOYAGE_API_KEY_FILE) to be set."
            )
        return VoyageEmbedder(
            settings.voyage_api_key,
            model=model or "voyage-finance-2",
            dimensions=dimensions,
            timeout=settings.timeout_seconds,
        )
    if settings.openai_api_key is None:
        raise EmbeddingServiceError(
            "The openai embedder needs OPENAI_API_KEY (or OPENAI_API_KEY_FILE) to be set."
        )
    return OpenAIEmbedder(
        settings.openai_api_key,
        model=model or "text-embedding-3-small",
        dimensions=dimensions,
        timeout=settings.timeout_seconds,
    )


class RagflowSettings(BaseModel):
    """Where a firm's RAGFlow server is, for parsing PDFs at ingestion (``--pdf-parser``).

    Build one with `ragflow_settings`.
    """

    url: str | None = None
    api_key: SecretStr | None = None
    parse_timeout_seconds: float = Field(default=600.0, gt=0, le=3600)


def ragflow_settings(environ: Mapping[str, str | None] | None = None) -> RagflowSettings:
    """Return the RAGFlow settings from ``environ``, or from the environment and ``.env``.

    Raises `SecretConfigError` for a key given twice or an unreadable key file.
    """
    values = secret_sources() if environ is None else environ
    fields: dict[str, object] = {
        "url": values.get("RAGFLOW_URL") or None,
        "api_key": _key(read_secret("RAGFLOW_API_KEY", values)),
    }
    if values.get("RAGFLOW_PARSE_TIMEOUT"):
        fields["parse_timeout_seconds"] = values["RAGFLOW_PARSE_TIMEOUT"]
    return RagflowSettings.model_validate(fields)


def make_ragflow_loader(settings: RagflowSettings | None = None) -> RagflowPdfLoader:
    """Build the RAGFlow PDF loader; fails fast with an actionable message when unconfigured."""
    try:
        settings = settings if settings is not None else ragflow_settings()
    except SecretConfigError as error:
        raise SourceReadError(str(error)) from None
    if not settings.url or settings.api_key is None:
        raise SourceReadError(
            "The RAGFlow PDF parser needs RAGFLOW_URL and RAGFLOW_API_KEY (or "
            "RAGFLOW_API_KEY_FILE) to be set."
        )
    return RagflowPdfLoader(
        settings.url, settings.api_key, parse_timeout=settings.parse_timeout_seconds
    )
