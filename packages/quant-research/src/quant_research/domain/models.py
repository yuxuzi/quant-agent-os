"""Knowledge-graph records: documents, chunks, entities, mentions, relations, and snapshots."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EntityType = Literal[
    "paper",
    "author",
    "concept",
    "factor",
    "signal",
    "strategy",
    "instrument",
    "asset_class",
    "method",
    "dataset",
    "metric",
    "empirical_result",
]

Predicate = Literal[
    "predicts",
    "correlates_with",
    "explained_by",
    "extends",
    "contradicts",
    "uses",
    "tested_on",
    "related_to",
]


class Document(BaseModel):
    """One ingested source, identified by the SHA-256 of its content."""

    id: str
    sha256: str
    uri: str = Field(description="Source location, such as a path relative to the corpus root")
    title: str
    authors: list[str] = Field(default_factory=list)
    published: str | None = None
    license_note: str | None = None
    loader_version: str


class Chunk(BaseModel):
    """A passage of a document with its exact location."""

    id: str
    document_id: str
    ordinal: int = Field(ge=0)
    text: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    section: str | None = None

    @property
    def location(self) -> str:
        """Return a human-readable location such as ``lines 3-9, § Results``."""
        lines = f"lines {self.line_start}-{self.line_end}"
        return f"{lines}, § {self.section}" if self.section else lines


class Entity(BaseModel):
    """A canonical quantitative concept, such as ``signal:short-term-reversal``."""

    id: str
    type: EntityType
    name: str
    aliases: list[str] = Field(default_factory=list)


class Mention(BaseModel):
    """An entity occurring at a character span of a chunk."""

    entity_id: str
    chunk_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    extractor: str
    confidence: float = Field(ge=0, le=1)


class Relation(BaseModel):
    """A typed edge between two entities, supported by exactly one chunk."""

    id: str
    subject_id: str
    predicate: Predicate
    object_id: str
    chunk_id: str
    extractor: str
    extractor_version: str
    confidence: float = Field(ge=0, le=1)


class CorpusSnapshot(BaseModel):
    """A reproducible identifier for the corpus contents and the extractors that read it."""

    id: str
    document_count: int = Field(ge=0)
    vocabulary_version: str
    extractor_versions: dict[str, str]
