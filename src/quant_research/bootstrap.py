"""Wire Quant Research's services to concrete adapters (the composition root)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

from quant_research.application.ingest import IngestService
from quant_research.application.retrieval import ResearchService
from quant_research.domain.ports import Embedder
from quant_research.domain.vocabulary import EntityMatcher, Vocabulary, load_vocabulary
from quant_research.infrastructure.embedding import HashingEmbedder
from quant_research.infrastructure.files import TextFileLoader
from quant_research.infrastructure.pdf import pdf_loader
from quant_research.infrastructure.postgres_store import (
    DEFAULT_SCHEMA,
    PostgresKnowledgeStore,
    is_postgres_url,
)
from quant_research.infrastructure.sqlite_store import SqliteKnowledgeStore

Store = SqliteKnowledgeStore | PostgresKnowledgeStore


@dataclass(frozen=True)
class ResearchApp:
    """A store with its ingestion and retrieval services."""

    store: Store
    vocabulary: Vocabulary
    ingest: IngestService
    research: ResearchService
    embedder: Embedder | None

    def close(self) -> None:
        """Close the store."""
        self.store.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def open_research(
    store_path: str | Path,
    vocabulary_path: Path | None = None,
    *,
    vector: bool = False,
    embedder: Embedder | None = None,
    read_only: bool = False,
    schema: str = DEFAULT_SCHEMA,
) -> ResearchApp:
    """Open (or create) a knowledge store and build the services over it.

    ``store_path`` is a SQLite file, or a ``postgresql://`` URL for the PostgreSQL store (the
    ``postgres`` extra); give the password through ``PGPASSWORD`` or ``.pgpass``, not the URL.
    ``schema`` names the PostgreSQL schema that holds the store, so one database can hold
    several corpora.

    ``vector`` enables the vector channel with the offline `HashingEmbedder`; the SQLite store
    doubles as the vector index. It is off by default because the mock embedder shows no
    measured gain over keyword, mention, and graph retrieval (decision 0004); a real embedder
    should beat the evaluation baseline before it becomes the default. ``read_only`` opens an
    existing store without creating, migrating, or writing anything. Passing ``embedder``
    (for example a `VoyageEmbedder` from `quant_research.config.make_embedder`) enables the
    vector channel with that model instead. Text and Markdown always
    load; PDFs load when the ``pdf`` extra is installed and are otherwise skipped with an
    installation hint.
    """
    store: Store = (
        PostgresKnowledgeStore(store_path, read_only=read_only, schema=schema)
        if is_postgres_url(store_path)
        else SqliteKnowledgeStore(store_path, read_only=read_only)
    )
    vocabulary = load_vocabulary(vocabulary_path)
    if embedder is None and vector:
        embedder = HashingEmbedder(EntityMatcher(vocabulary))
    vectors = store if embedder is not None else None
    return ResearchApp(
        store=store,
        vocabulary=vocabulary,
        ingest=IngestService(
            store, vocabulary, [TextFileLoader(), pdf_loader()], embedder=embedder, vectors=vectors
        ),
        research=ResearchService(store, vocabulary, embedder=embedder, vectors=vectors),
        embedder=embedder,
    )
