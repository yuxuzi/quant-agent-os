"""Ports for corpus sources and knowledge-store adapters (decision 0004)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from quant_research.domain.models import (
    Chunk,
    CorpusSnapshot,
    Document,
    Entity,
    Mention,
    Relation,
)


@dataclass(frozen=True)
class SourceFile:
    """A loaded source: body text, metadata, and where the body starts in the original."""

    uri: str
    sha256: str
    body: str
    body_first_line: int
    metadata: dict[str, str] = field(default_factory=dict)


class SourceLoader(Protocol):
    """Finds and reads corpus sources."""

    version: str

    def discover(self, root: Path) -> tuple[list[Path], list[Path]]:
        """Return supported and unsupported paths under ``root``, sorted."""
        ...

    def load(self, path: Path, root: Path) -> SourceFile:
        """Read one source; raise `UnicodeDecodeError` for undecodable text."""
        ...


class KeywordHit(BaseModel):
    """A chunk matched by keyword search; higher scores are better."""

    chunk_id: str
    score: float


class GraphEdge(BaseModel):
    """A relation reached during graph expansion, ``hops`` steps from a seed entity."""

    relation: Relation
    hops: int


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
