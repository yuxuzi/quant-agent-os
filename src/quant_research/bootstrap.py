"""Wire Quant Research's services to concrete adapters (the composition root)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

from quant_research.application.ingest import IngestService
from quant_research.application.retrieval import ResearchService
from quant_research.domain.ports import Embedder
from quant_research.domain.vocabulary import (
    Vocabulary,
    has_firm_terms,
    load_vocabulary,
    merge_vocabulary,
)
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
    embedder: Embedder | None = None,
    read_only: bool = False,
    schema: str = DEFAULT_SCHEMA,
) -> ResearchApp:
    """Open (or create) a knowledge store and build the services over it.

    ``vocabulary_path`` is a firm's vocabulary, merged over the bundled one for ingestion. The
    store keeps what it was ingested with, so without a path a store that holds firm terms
    goes on using them, and serving matches queries as ingestion matched the corpus.

    ``store_path`` is a SQLite file, or a ``postgresql://`` URL for the PostgreSQL store (the
    ``postgres`` extra); give the password through ``PGPASSWORD`` or ``.pgpass``, not the URL.
    ``schema`` names the PostgreSQL schema that holds the store, so one database can hold
    several corpora.

    ``embedder`` (from `quant_research.config.make_embedder`) enables the vector channel; the
    store doubles as the vector index. It is off by default. ``read_only`` opens an existing store
    without creating, migrating, or writing anything. Text and Markdown always load; PDFs load
    when the ``pdf`` extra is installed and are otherwise skipped with an installation hint.
    """
    firm = load_vocabulary(vocabulary_path) if vocabulary_path else None  # before creating a store
    store: Store = (
        PostgresKnowledgeStore(store_path, read_only=read_only, schema=schema)
        if is_postgres_url(store_path)
        else SqliteKnowledgeStore(store_path, read_only=read_only)
    )
    vocabulary = merge_vocabulary(load_vocabulary(), firm) if firm else _vocabulary_of(store)
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


def _vocabulary_of(store: Store) -> Vocabulary:
    """Return the firm vocabulary ``store`` was ingested with, or else the bundled one."""
    snapshot = store.current_snapshot()
    if snapshot is None or not has_firm_terms(snapshot.vocabulary_version):
        return load_vocabulary()
    return Vocabulary(version=snapshot.vocabulary_version, entities=store.all_entities())
