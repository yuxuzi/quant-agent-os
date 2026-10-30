"""Ports for corpus sources and knowledge-store adapters (decision 0004)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel

from quant_research.domain.models import (
    Chunk,
    CorpusSnapshot,
    Document,
    Entity,
    Mention,
    Relation,
)


class SourceReadError(ValueError):
    """Raised when a source cannot be read; the message says what to do about it."""


@dataclass(frozen=True)
class SourceFile:
    """A loaded source: text, metadata, and where the text starts in the original.

    Text sources set ``body`` and ``body_first_line``. Paged sources (PDF) set ``pages`` instead,
    one string per page, and leave ``body`` empty.
    """

    uri: str
    sha256: str
    body: str
    body_first_line: int
    metadata: dict[str, str] = field(default_factory=dict)
    pages: tuple[str, ...] | None = None
    loader_version: str = ""


class SourceLoader(Protocol):
    """Reads one kind of corpus source, chosen by file suffix."""

    version: str
    suffixes: frozenset[str]

    def load(self, path: Path, root: Path) -> SourceFile:
        """Read one source; raise `SourceReadError` when it cannot be read."""
        ...


class Embedder(Protocol):
    """Turns texts into unit-length vectors; ``model_id`` is part of every corpus snapshot.

    ``kind`` distinguishes stored passages from search queries, which some models embed
    differently. ``requires_network`` is true when embedding sends text to a remote service, so
    tools that embed queries must declare the ``network`` permission.
    """

    model_id: str
    dimensions: int
    requires_network: bool

    def embed(
        self, texts: Sequence[str], *, kind: Literal["document", "query"] = "document"
    ) -> list[list[float]]:
        """Return one unit-length vector per text, in order."""
        ...


class VectorHit(BaseModel):
    """A chunk matched by vector similarity (cosine, higher is better)."""

    chunk_id: str
    score: float


class VectorIndex(Protocol):
    """Stores chunk embeddings per model and searches them by cosine similarity."""

    def upsert_embeddings(self, model_id: str, vectors: Sequence[tuple[str, list[float]]]) -> None:
        """Store ``(chunk_id, vector)`` pairs for ``model_id``."""
        ...

    def has_embeddings(self, model_id: str) -> bool:
        """Return whether any chunk has an embedding for ``model_id``."""
        ...

    def chunks_without_embeddings(self, model_id: str) -> list[tuple[str, str]]:
        """Return ``(chunk_id, text)`` for chunks that have no ``model_id`` embedding yet."""
        ...

    def vector_search(self, model_id: str, vector: list[float], limit: int) -> list[VectorHit]:
        """Return the most similar chunks, best first."""
        ...


class KeywordHit(BaseModel):
    """A chunk matched by keyword search; higher scores are better."""

    chunk_id: str
    score: float


class GraphEdge(BaseModel):
    """A relation reached during graph expansion, ``hops`` steps from a seed entity.

    ``support`` counts the chunks that state the same subject, predicate, and object; only the
    strongest few of those parallel edges are returned.
    """

    relation: Relation
    hops: int
    support: int = 1


class KnowledgeStore(Protocol):
    """Persistence for the knowledge graph and its passages."""

    def has_document(self, sha256: str) -> bool:
        """Return whether a document with this content hash is stored."""
        ...

    def replace_document(
        self,
        document: Document,
        chunks: Sequence[Chunk],
        mentions: Sequence[Mention],
        relations: Sequence[Relation],
    ) -> None:
        """Store a document and its derived records, replacing any document at the same URI."""
        ...

    def upsert_entities(self, entities: Iterable[Entity]) -> None:
        """Insert or update canonical entities."""
        ...

    def record_snapshot(self, snapshot: CorpusSnapshot) -> None:
        """Record the current corpus snapshot."""
        ...

    def current_snapshot(self) -> CorpusSnapshot | None:
        """Return the most recently recorded snapshot, if any."""
        ...

    def document_hashes(self) -> list[str]:
        """Return the content hashes of every stored document, sorted."""
        ...

    def get_document(self, document_id: str) -> Document | None:
        """Return a document by id."""
        ...

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        """Return a chunk by id."""
        ...

    def get_entities(self, entity_ids: Iterable[str]) -> dict[str, Entity]:
        """Return the known entities among ``entity_ids``."""
        ...

    def mentioned_entities(self, chunk_ids: Iterable[str]) -> dict[str, list[str]]:
        """Return entity ids mentioned in each chunk."""
        ...

    def chunks_mentioning(self, entity_ids: Iterable[str], limit: int) -> list[str]:
        """Return chunk ids that mention the most of ``entity_ids``, best first."""
        ...

    def keyword_search(self, query: str, limit: int) -> list[KeywordHit]:
        """Return the best keyword matches for ``query``."""
        ...

    def expand(
        self, entity_ids: Iterable[str], *, hops: int, min_confidence: float
    ) -> list[GraphEdge]:
        """Return relations reachable from ``entity_ids`` within ``hops`` steps, either way."""
        ...
