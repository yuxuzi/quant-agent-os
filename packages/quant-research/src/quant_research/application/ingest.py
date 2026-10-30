"""Ingest a corpus into a knowledge store: load, chunk, extract, embed, and snapshot.

Ingestion writes to the store, so it is an operator command (CLI or SDK), never an agent tool.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, Field

from quant_research.domain.chunking import CHUNKER_VERSION, Passage, split_page, split_passages
from quant_research.domain.extraction import RuleExtractor
from quant_research.domain.models import Chunk, CorpusSnapshot, Document, Mention, Relation
from quant_research.domain.ports import (
    Embedder,
    KnowledgeStore,
    SourceFile,
    SourceLoader,
    SourceReadError,
    VectorIndex,
)
from quant_research.domain.vocabulary import EntityMatcher, Vocabulary

_TITLE = re.compile(r"^#\s+(.*\S)\s*$", re.MULTILINE)
EMBED_BATCH = 64


class IngestResult(BaseModel):
    """What one ingestion run changed."""

    added: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    chunks: int = 0
    mentions: int = 0
    relations: int = 0
    embedded: int = 0
    snapshot: CorpusSnapshot
    warnings: list[str] = Field(default_factory=list)


class IngestService:
    """Turn files into documents, chunks, mentions, relations, and (optionally) embeddings.

    Each file goes to the loader that claims its suffix. With an embedder and a vector index,
    every chunk lacking an embedding for the embedder's model is embedded, so enabling vectors
    on an existing store, or switching models, backfills it.
    """

    def __init__(
        self,
        store: KnowledgeStore,
        vocabulary: Vocabulary,
        loaders: Sequence[SourceLoader],
        *,
        embedder: Embedder | None = None,
        vectors: VectorIndex | None = None,
    ) -> None:
        if (embedder is None) != (vectors is None):
            raise ValueError("Pass both an embedder and a vector index, or neither.")
        self.store = store
        self.vocabulary = vocabulary
        self.loaders = {suffix: loader for loader in loaders for suffix in loader.suffixes}
        self.embedder = embedder
        self.vectors = vectors
        entities = {entity.id: entity for entity in vocabulary.entities}
        self.extractor = RuleExtractor(EntityMatcher(vocabulary), entities)

    def ingest(self, root: Path) -> IngestResult:
        """Ingest every supported file under ``root``; unchanged files are skipped by hash."""
        if not root.exists():
            raise FileNotFoundError(f"Corpus path '{root}' does not exist.")
        self.store.upsert_entities(self.vocabulary.entities)
        found = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        files = [path for path in found if not path.name.startswith(".")]
        supported = ", ".join(sorted(self.loaders))
        added: list[str] = []
        unchanged: list[str] = []
        skipped: list[str] = []
        warnings: list[str] = []
        counts = [0, 0, 0]
        for path in files:
            loader = self.loaders.get(path.suffix.lower())
            if loader is None:
                skipped.append(path.name)
                warnings.append(f"Skipped {path.name}: supported types are {supported}.")
                continue
            try:
                source = loader.load(path, root)
            except SourceReadError as error:
                skipped.append(path.name)
                warnings.append(str(error))
                continue
            if self.store.has_document(source.sha256):
                unchanged.append(source.uri)
                continue
            document, chunks, mentions, relations = self._build(source)
            if not chunks:
                hint = " Scanned PDFs need OCR, which is not supported." if source.pages else ""
                warnings.append(f"{source.uri}: no text to index.{hint}")
            self.store.replace_document(document, chunks, mentions, relations)
            added.append(source.uri)
            counts = [
                counts[0] + len(chunks),
                counts[1] + len(mentions),
                counts[2] + len(relations),
            ]
        embedded = self._embed_missing()
        snapshot = self.store.publish_snapshot(self._snapshot_for)
        return IngestResult(
            added=added,
            unchanged=unchanged,
            skipped=skipped,
            chunks=counts[0],
            mentions=counts[1],
            relations=counts[2],
            embedded=embedded,
            snapshot=snapshot,
            warnings=warnings,
        )

    def snapshot(self) -> CorpusSnapshot:
        """Return the snapshot for the stored documents and the current component versions."""
        return self._snapshot_for(self.store.document_hashes())

    def _snapshot_for(self, hashes: list[str]) -> CorpusSnapshot:
        versions = {
            "chunker": CHUNKER_VERSION,
            "extractor": self.extractor.version,
            "loader": "+".join(sorted({loader.version for loader in self.loaders.values()})),
        }
        if self.embedder is not None:
            versions["embedder"] = self.embedder.model_id
        material = "|".join([*hashes, self.vocabulary.version, *sorted(versions.values())])
        return CorpusSnapshot(
            id="snap:" + hashlib.sha256(material.encode()).hexdigest()[:16],
            document_count=len(hashes),
            vocabulary_version=self.vocabulary.version,
            extractor_versions=versions,
        )

    def _embed_missing(self) -> int:
        if self.embedder is None or self.vectors is None:
            return 0
        model_id = self.embedder.model_id
        pending = self.vectors.chunks_without_embeddings(model_id)
        for start in range(0, len(pending), EMBED_BATCH):
            batch = pending[start : start + EMBED_BATCH]
            vectors = self.embedder.embed([text for _, text in batch], kind="document")
            pairs = [
                (chunk_id, vector) for (chunk_id, _), vector in zip(batch, vectors, strict=True)
            ]
            self.vectors.upsert_embeddings(model_id, pairs)
        return len(pending)

    def _build(
        self, source: SourceFile
    ) -> tuple[Document, list[Chunk], list[Mention], list[Relation]]:
        document_id = "doc:" + source.sha256[:16]
        first_text = source.body if source.pages is None else next(iter(source.pages), "")
        heading = _TITLE.search(first_text)
        authors = source.metadata.get("authors", "")
        document = Document(
            id=document_id,
            sha256=source.sha256,
            uri=source.uri,
            title=source.metadata.get("title") or (heading.group(1) if heading else source.uri),
            authors=[name.strip() for name in authors.split(",") if name.strip()],
            published=source.metadata.get("published"),
            license_note=source.metadata.get("license"),
            loader_version=source.loader_version,
        )
        located: list[tuple[int | None, Passage]]
        if source.pages is None:
            located = [
                (None, passage)
                for passage in split_passages(source.body, first_line=source.body_first_line)
            ]
        else:
            located = [
                (number, passage)
                for number, page in enumerate(source.pages, start=1)
                for passage in split_page(page)
            ]
        chunks = [
            Chunk(
                id=f"{document_id}#{ordinal}",
                document_id=document_id,
                ordinal=ordinal,
                text=passage.text,
                line_start=passage.line_start,
                line_end=passage.line_end,
                section=passage.section,
                page=page,
            )
            for ordinal, (page, passage) in enumerate(located)
        ]
        mentions: list[Mention] = []
        relations: list[Relation] = []
        for chunk in chunks:
            found_mentions, found_relations = self.extractor.extract(chunk)
            mentions += found_mentions
            relations += found_relations
        return document, chunks, mentions, relations
