"""Ingest a corpus into a knowledge store: load, chunk, extract, and snapshot.

Ingestion writes to the store, so it is an operator command (CLI or SDK), never an agent tool.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from pydantic import BaseModel, Field

from quant_research.domain.chunking import CHUNKER_VERSION, split_passages
from quant_research.domain.extraction import RuleExtractor
from quant_research.domain.models import Chunk, CorpusSnapshot, Document, Mention, Relation
from quant_research.domain.ports import KnowledgeStore, SourceFile, SourceLoader
from quant_research.domain.vocabulary import EntityMatcher, Vocabulary

_TITLE = re.compile(r"^#\s+(.*\S)\s*$", re.MULTILINE)


class IngestResult(BaseModel):
    """What one ingestion run changed."""

    added: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    chunks: int = 0
    mentions: int = 0
    relations: int = 0
    snapshot: CorpusSnapshot
    warnings: list[str] = Field(default_factory=list)


class IngestService:
    """Turn files into documents, chunks, mentions, and relations."""

    def __init__(self, store: KnowledgeStore, vocabulary: Vocabulary, loader: SourceLoader) -> None:
        self.store = store
        self.vocabulary = vocabulary
        self.loader = loader
        entities = {entity.id: entity for entity in vocabulary.entities}
        self.extractor = RuleExtractor(EntityMatcher(vocabulary), entities)

    def ingest(self, root: Path) -> IngestResult:
        """Ingest every supported file under ``root``; unchanged files are skipped by hash."""
        if not root.exists():
            raise FileNotFoundError(f"Corpus path '{root}' does not exist.")
        self.store.upsert_entities(self.vocabulary.entities)
        supported, unsupported = self.loader.discover(root)
        added: list[str] = []
        unchanged: list[str] = []
        warnings = [
            f"Skipped {path.name}: only .md, .markdown, and .txt are supported."
            for path in unsupported
        ]
        counts = [0, 0, 0]
        for path in supported:
            try:
                source = self.loader.load(path, root)
            except UnicodeDecodeError:
                warnings.append(f"Skipped {path.name}: not valid UTF-8.")
                continue
            if self.store.has_document(source.sha256):
                unchanged.append(source.uri)
                continue
            document, chunks, mentions, relations = self._build(source)
            if not chunks:
                warnings.append(f"{source.uri}: no text to index.")
            self.store.replace_document(document, chunks, mentions, relations)
            added.append(source.uri)
            counts = [
                counts[0] + len(chunks),
                counts[1] + len(mentions),
                counts[2] + len(relations),
            ]
        snapshot = self.snapshot()
        self.store.record_snapshot(snapshot)
        return IngestResult(
            added=added,
            unchanged=unchanged,
            skipped=[path.name for path in unsupported],
            chunks=counts[0],
            mentions=counts[1],
            relations=counts[2],
            snapshot=snapshot,
            warnings=warnings,
        )

    def snapshot(self) -> CorpusSnapshot:
        """Return the snapshot for the store's current documents and these extractor versions."""
        hashes = self.store.document_hashes()
        versions = {
            "chunker": CHUNKER_VERSION,
            "extractor": self.extractor.version,
            "loader": self.loader.version,
        }
        material = "|".join([*hashes, self.vocabulary.version, *sorted(versions.values())])
        return CorpusSnapshot(
            id="snap:" + hashlib.sha256(material.encode()).hexdigest()[:16],
            document_count=len(hashes),
            vocabulary_version=self.vocabulary.version,
            extractor_versions=versions,
        )

    def _build(
        self, source: SourceFile
    ) -> tuple[Document, list[Chunk], list[Mention], list[Relation]]:
        document_id = "doc:" + source.sha256[:16]
        heading = _TITLE.search(source.body)
        authors = source.metadata.get("authors", "")
        document = Document(
            id=document_id,
            sha256=source.sha256,
            uri=source.uri,
            title=source.metadata.get("title") or (heading.group(1) if heading else source.uri),
            authors=[name.strip() for name in authors.split(",") if name.strip()],
            published=source.metadata.get("published"),
            license_note=source.metadata.get("license"),
            loader_version=self.loader.version,
        )
        chunks = [
            Chunk(
                id=f"{document_id}#{ordinal}",
                document_id=document_id,
                ordinal=ordinal,
                text=passage.text,
                line_start=passage.line_start,
                line_end=passage.line_end,
                section=passage.section,
            )
            for ordinal, passage in enumerate(
                split_passages(source.body, first_line=source.body_first_line)
            )
        ]
        mentions: list[Mention] = []
        relations: list[Relation] = []
        for chunk in chunks:
            found_mentions, found_relations = self.extractor.extract(chunk)
            mentions += found_mentions
            relations += found_relations
        return document, chunks, mentions, relations
