"""Hybrid Graph RAG retrieval: seed entities, expand the graph, fuse ranked passages.

The result is an `EvidencePacket`: cited passages, graph paths whose edges cite their supporting
chunks, and the corpus snapshot used. The client (or a later synthesis tool) writes the answer;
nothing here calls a model.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, Field

from quant_agent_os.evidence import Evidence, SourceRef
from quant_research.domain.models import Chunk, Entity, EntityType, Relation
from quant_research.domain.ports import Embedder, GraphEdge, KnowledgeStore, VectorIndex
from quant_research.domain.text import content_terms
from quant_research.domain.vocabulary import EntityMatcher, Vocabulary

RRF_K = 60
PASSAGE_PREVIEW_CHARS = 700
SIGNAL_TYPES: tuple[EntityType, ...] = ("signal", "factor", "strategy")
MAX_RELATED = 10
MAX_LINKS_PER_ENTITY = 3
MAX_FALLBACK_SEEDS = 3
MIN_VECTOR_SCORE = 0.2

Channel = Literal["keyword", "mention", "graph", "vector"]


class ResearchQuery(BaseModel):
    """A question about the corpus and how widely to search."""

    query: str = Field(min_length=1, max_length=2000)
    entity_types: list[EntityType] = Field(default_factory=lambda: list(SIGNAL_TYPES))
    k: int = Field(default=6, ge=1, le=25, description="Maximum passages to return")
    hops: int = Field(default=1, ge=1, le=2, description="Graph expansion depth")
    min_confidence: float = Field(default=0.5, ge=0, le=1)


class GraphLink(BaseModel):
    """One edge on a path, citing the chunk that supports it."""

    subject: str
    predicate: str
    object: str
    confidence: float
    chunk_id: str
    extractor: str


class RelatedEntity(BaseModel):
    """An entity related to the query, with the edges that connect it.

    ``hops == 0`` means the entity was found directly in the best keyword passage (when the
    query names no vocabulary entity); its evidence is that passage rather than a graph edge.
    """

    id: str
    name: str
    type: str
    score: float
    hops: int
    links: list[GraphLink]
    mentioned_in: list[str] = Field(default_factory=list)


class PassageResult(BaseModel):
    """A ranked passage with its source location; ``text`` may be a preview."""

    chunk_id: str
    source: SourceRef
    text: str
    truncated: bool
    score: float
    channels: list[Channel]


class EvidencePacket(BaseModel):
    """Everything a client needs to write a cited answer."""

    query: str
    seed_entities: list[str]
    related: list[RelatedEntity]
    passages: list[PassageResult]
    corpus_snapshot: str | None
    warnings: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    data_versions: dict[str, str] = Field(default_factory=dict)


class SourcePassage(BaseModel):
    """The full text of one chunk with its source."""

    chunk_id: str
    source: SourceRef
    text: str
    section: str | None
    corpus_snapshot: str | None
    warnings: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    data_versions: dict[str, str] = Field(default_factory=dict)


class UnknownPassageError(LookupError):
    """Raised when a chunk id is not in the store."""


class ResearchService:
    """Read-only queries over a knowledge store, with an optional vector channel.

    With an embedder and a vector index, chunks whose cosine similarity to the query is at
    least `MIN_VECTOR_SCORE` form a fourth ranking in the fusion.
    """

    def __init__(
        self,
        store: KnowledgeStore,
        vocabulary: Vocabulary,
        *,
        embedder: Embedder | None = None,
        vectors: VectorIndex | None = None,
    ) -> None:
        if (embedder is None) != (vectors is None):
            raise ValueError("Pass both an embedder and a vector index, or neither.")
        self.store = store
        self.matcher = EntityMatcher(vocabulary)
        self.embedder = embedder
        self.vectors = vectors

    def find_related(self, request: ResearchQuery) -> EvidencePacket:
        """Return entities of ``request.entity_types`` related to the query, with evidence."""
        snapshot = self.store.current_snapshot()
        warnings: list[str] = []
        if snapshot is None or snapshot.document_count == 0:
            warnings.append("The knowledge store is empty; ingest a corpus first.")
        terms = content_terms(request.query)
        keyword_hits = self.store.keyword_search(" ".join(terms), request.k * 3) if terms else []
        seeds = list(dict.fromkeys(match.entity_id for match in self.matcher.find(request.query)))
        direct: list[str] = []
        if not seeds and keyword_hits:
            best = keyword_hits[0].chunk_id
            direct = self.store.mentioned_entities([best]).get(best, [])[:MAX_FALLBACK_SEEDS]
            seeds = direct
            if seeds:
                warnings.append(
                    "No vocabulary entity in the query; seeded from the best keyword passage."
                )
        edges = self.store.expand(seeds, hops=request.hops, min_confidence=request.min_confidence)
        entities = self.store.get_entities(
            {*seeds}
            | {edge.relation.subject_id for edge in edges}
            | {edge.relation.object_id for edge in edges}
        )
        types: set[str] = set(request.entity_types)
        related = [
            RelatedEntity(
                id=entity_id,
                name=entities[entity_id].name,
                type=entities[entity_id].type,
                score=1.0,
                hops=0,
                links=[],
                mentioned_in=[keyword_hits[0].chunk_id],
            )
            for entity_id in direct
            if entity_id in entities and entities[entity_id].type in types
        ]
        related += _rank_related(seeds, edges, entities, types)
        related = related[:MAX_RELATED]
        if seeds and not related:
            warnings.append("No related entities of the requested types were found.")

        rankings: dict[Channel, list[str]] = {
            "keyword": [hit.chunk_id for hit in keyword_hits],
            "mention": self.store.chunks_mentioning(seeds, request.k * 3) if seeds else [],
            "graph": list(
                dict.fromkeys(link.chunk_id for entity in related for link in entity.links)
            ),
            "vector": self._vector_ranking(request.query, request.k * 3),
        }
        fused = _fuse(rankings)[: request.k]
        passages = [p for p in (self._passage(cid, score, ch) for cid, score, ch in fused) if p]
        if not passages and not seeds:
            warnings.append(
                "Nothing in the corpus matched the query; try the names of factors, signals, "
                "or strategies, or ingest more documents."
            )

        names = {entity_id: entity.name for entity_id, entity in entities.items()}
        sources = {passage.chunk_id: passage.source for passage in passages}
        evidence: list[Evidence] = []
        for entity in related:
            for chunk_id in entity.mentioned_in:
                source = self._source_for(chunk_id, sources)
                if source is not None:
                    evidence.append(
                        Evidence(
                            kind="retrieved",
                            statement=f"The passage mentions {entity.name}.",
                            sources=[source],
                        )
                    )
            for link in entity.links:
                source = self._source_for(link.chunk_id, sources)
                if source is None:
                    continue
                predicate = link.predicate.replace("_", " ")
                evidence.append(
                    Evidence(
                        kind="retrieved",
                        statement=(
                            f"{names.get(link.subject, link.subject)} {predicate} "
                            f"{names.get(link.object, link.object)} "
                            f"({link.extractor} extraction, confidence {link.confidence:.2f})."
                        ),
                        sources=[source],
                    )
                )
        return EvidencePacket(
            query=request.query,
            seed_entities=seeds,
            related=related,
            passages=passages,
            corpus_snapshot=snapshot.id if snapshot else None,
            warnings=warnings,
            evidence=evidence,
            data_versions={"corpus_snapshot": snapshot.id} if snapshot else {},
        )

    def _vector_ranking(self, query: str, limit: int) -> list[str]:
        if self.embedder is None or self.vectors is None:
            return []
        (vector,) = self.embedder.embed([query])
        hits = self.vectors.vector_search(self.embedder.model_id, vector, limit)
        return [hit.chunk_id for hit in hits if hit.score >= MIN_VECTOR_SCORE]

    def get_passage(self, chunk_id: str) -> SourcePassage:
        """Return the full text of one chunk; raise `UnknownPassageError` if it is unknown."""
        chunk = self.store.get_chunk(chunk_id)
        if chunk is None:
            raise UnknownPassageError(f"No passage with id '{chunk_id}'.")
        source = self._source(chunk)
        snapshot = self.store.current_snapshot()
        return SourcePassage(
            chunk_id=chunk.id,
            source=source,
            text=chunk.text,
            section=chunk.section,
            corpus_snapshot=snapshot.id if snapshot else None,
            evidence=[Evidence(kind="retrieved", statement=chunk.text, sources=[source])],
            data_versions={"corpus_snapshot": snapshot.id} if snapshot else {},
        )

    def _passage(
        self, chunk_id: str, score: float, channels: list[Channel]
    ) -> PassageResult | None:
        chunk = self.store.get_chunk(chunk_id)
        if chunk is None:
            return None
        truncated = len(chunk.text) > PASSAGE_PREVIEW_CHARS
        return PassageResult(
            chunk_id=chunk.id,
            source=self._source(chunk),
            text=chunk.text[:PASSAGE_PREVIEW_CHARS] + ("…" if truncated else ""),
            truncated=truncated,
            score=round(score, 6),
            channels=channels,
        )

    def _source_for(self, chunk_id: str, cache: dict[str, SourceRef]) -> SourceRef | None:
        if chunk_id not in cache:
            chunk = self.store.get_chunk(chunk_id)
            if chunk is None:
                return None
            cache[chunk_id] = self._source(chunk)
        return cache[chunk_id]

    def _source(self, chunk: Chunk) -> SourceRef:
        document = self.store.get_document(chunk.document_id)
        return SourceRef(
            source_id=document.uri if document else chunk.document_id,
            location=chunk.location,
            title=document.title if document else None,
        )


def _rank_related(
    seeds: list[str],
    edges: list[GraphEdge],
    entities: dict[str, Entity],
    types: set[str],
) -> list[RelatedEntity]:
    seed_set = set(seeds)
    links: dict[str, list[tuple[GraphEdge, Relation]]] = defaultdict(list)
    for edge in edges:
        relation = edge.relation
        for endpoint in (relation.subject_id, relation.object_id):
            if endpoint not in seed_set:
                links[endpoint].append((edge, relation))
    ranked: list[RelatedEntity] = []
    for entity_id, found in links.items():
        entity = entities.get(entity_id)
        if entity is None or entity.type not in types:
            continue
        hops = min(edge.hops for edge, _ in found)
        score = sum(relation.confidence / edge.hops for edge, relation in found)
        ranked.append(
            RelatedEntity(
                id=entity.id,
                name=entity.name,
                type=entity.type,
                score=round(score, 6),
                hops=hops,
                links=[
                    GraphLink(
                        subject=relation.subject_id,
                        predicate=relation.predicate,
                        object=relation.object_id,
                        confidence=relation.confidence,
                        chunk_id=relation.chunk_id,
                        extractor=relation.extractor,
                    )
                    for _, relation in sorted(
                        found, key=lambda item: (-item[1].confidence, item[1].id)
                    )[:MAX_LINKS_PER_ENTITY]
                ],
            )
        )
    ranked.sort(key=lambda entity: (-entity.score, entity.id))
    return ranked


def _fuse(rankings: dict[Channel, list[str]]) -> list[tuple[str, float, list[Channel]]]:
    """Reciprocal rank fusion; ties break by chunk id for determinism."""
    scores: dict[str, float] = defaultdict(float)
    channels: dict[str, list[Channel]] = defaultdict(list)
    for channel, ranking in rankings.items():
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] += 1 / (RRF_K + rank)
            channels[chunk_id].append(channel)
    ordered = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))
    return [(chunk_id, scores[chunk_id], channels[chunk_id]) for chunk_id in ordered]
