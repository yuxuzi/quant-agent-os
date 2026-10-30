"""Wire Quant Research's services to concrete adapters (the composition root)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from quant_research.application.ingest import IngestService
from quant_research.application.retrieval import ResearchService
from quant_research.domain.vocabulary import Vocabulary, load_vocabulary
from quant_research.infrastructure.files import TextFileLoader
from quant_research.infrastructure.sqlite_store import SqliteKnowledgeStore

DEFAULT_STORE = Path(".quant-research") / "knowledge.db"


@dataclass(frozen=True)
class ResearchApp:
    """A store with its ingestion and retrieval services."""

    store: SqliteKnowledgeStore
    vocabulary: Vocabulary
    ingest: IngestService
    research: ResearchService

    def close(self) -> None:
        """Close the store."""
        self.store.close()


def open_research(
    store_path: str | Path = DEFAULT_STORE, vocabulary_path: Path | None = None
) -> ResearchApp:
    """Open (or create) a SQLite store and build the services over it."""
    store = SqliteKnowledgeStore(store_path)
    vocabulary = load_vocabulary(vocabulary_path)
    return ResearchApp(
        store=store,
        vocabulary=vocabulary,
        ingest=IngestService(store, vocabulary, TextFileLoader()),
        research=ResearchService(store, vocabulary),
    )
