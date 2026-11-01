"""Strategy specifications from research evidence (B-17): a spec that cites, never code.

`StrategyService.generate` resolves one signal, factor, or strategy and turns the cited
knowledge-graph edges that touch it into a `StrategySpec`. Each item says what the corpus
states and links the passages that state it. What the corpus does not say becomes an open
question rather than a default. Nothing here calls a model, so the same corpus snapshot and
request always give the same spec.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from quant_agent_os.evidence import Evidence, SourceRef
from quant_research.application.retrieval import PASSAGE_PREVIEW_CHARS, SIGNAL_TYPES, source_of
from quant_research.domain.models import Entity, Predicate
from quant_research.domain.ports import KnowledgeStore
from quant_research.domain.vocabulary import EntityMatcher

SPEC_SCHEMA: Literal["strategy-spec/1"] = "strategy-spec/1"
MAX_KEY_PASSAGES = 10

Section = Literal["signals", "universe", "data", "methods", "metrics", "risks", "context"]
SECTIONS: tuple[Section, ...] = (
    "signals",
    "universe",
    "data",
    "methods",
    "metrics",
    "risks",
    "context",
)
Direction = Literal["outgoing", "incoming"]

# Where an edge goes, by the type of the entity at its other end. Risk predicates win over this.
SECTION_BY_TYPE: dict[str, Section] = {
    "signal": "signals",
    "factor": "signals",
    "strategy": "signals",
    "asset_class": "universe",
    "instrument": "universe",
    "dataset": "data",
    "method": "methods",
    "metric": "metrics",
}

OPEN_QUESTIONS: dict[Section, str] = {
    "universe": "No source ties {name} to an asset class or instrument; choose the universe.",
    "data": "No source names a dataset for {name}; choose the price and reference data.",
    "methods": "No source names a test method for {name}; choose one (for example portfolio "
    "sorts or walk-forward).",
    "metrics": "No source names evaluation metrics for {name}; choose them before testing.",
    "risks": "The corpus records nothing that contradicts or explains away {name}. That is an "
    "absence of evidence, not evidence of robustness.",
}
PARAMETERS_QUESTION = (
    "Parameters (formation and holding periods, rebalance frequency, costs) are not extracted; "
    "set them from the key passages."
)


class StrategyRequest(BaseModel):
    """Which signal, factor, or strategy to specify, and how strong the evidence must be."""

    focus: str = Field(
        min_length=1,
        max_length=500,
        description="A signal, factor, or strategy name, such as 'trend following', or an "
        "entity id such as 'signal:carry'",
    )
    min_confidence: float = Field(default=0.5, ge=0, le=1)
    max_passages: int = Field(default=5, ge=1, le=MAX_KEY_PASSAGES)


class EntityRef(BaseModel):
    """A vocabulary entity."""

    id: str
    name: str
    type: str


class SpecItem(BaseModel):
    """One element of the spec and the passages that state it.

    ``statement`` reads subject, predicate, object; ``direction`` says whether the focus is
    the subject (``outgoing``) or the object (``incoming``). The same fact stated in several
    passages is one item with several sources.
    """

    entity: EntityRef
    predicate: Predicate
    direction: Direction
    statement: str
    confidence: float
    sources: list[SourceRef] = Field(min_length=1)
    chunk_ids: list[str] = Field(min_length=1)


class KeyPassage(BaseModel):
    """A passage that mentions the focus; ``text`` may be a preview."""

    chunk_id: str
    source: SourceRef
    text: str
    truncated: bool


class StrategySpec(BaseModel):
    """A structured strategy specification whose every item links its sources.

    It is a description to review and complete, not code and not a recommendation. Sections
    hold only what the corpus states; ``open_questions`` lists what it leaves undecided.
    """

    schema_version: Literal["strategy-spec/1"] = SPEC_SCHEMA
    name: str
    focus: EntityRef
    signals: list[SpecItem] = Field(default_factory=list)
    universe: list[SpecItem] = Field(default_factory=list)
    data: list[SpecItem] = Field(default_factory=list)
    methods: list[SpecItem] = Field(default_factory=list)
    metrics: list[SpecItem] = Field(default_factory=list)
    risks: list[SpecItem] = Field(default_factory=list)
    context: list[SpecItem] = Field(default_factory=list)
    key_passages: list[KeyPassage] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    corpus_snapshot: str | None


class StrategyDraft(BaseModel):
    """The tool result: the spec plus the standard warnings, evidence, and data versions."""

    spec: StrategySpec
    warnings: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    data_versions: dict[str, str] = Field(default_factory=dict)


class UnknownFocusError(LookupError):
    """Raised when the request names no signal, factor, or strategy in the vocabulary."""


class NoEvidenceError(LookupError):
    """Raised when the corpus says nothing about the focus."""


class StrategyService:
    """Build strategy specs from a knowledge store (read-only)."""

    def __init__(self, store: KnowledgeStore, matcher: EntityMatcher) -> None:
        self.store = store
        self.matcher = matcher

    def generate(self, request: StrategyRequest) -> StrategyDraft:
        """Return a spec for ``request.focus``; every read sees one version of the corpus.

        Raise `UnknownFocusError` if the focus names no signal, factor, or strategy, and
        `NoEvidenceError` if no passage mentions it.
        """
        with self.store.reading():
            return self._generate(request)

    def _generate(self, request: StrategyRequest) -> StrategyDraft:
        warnings: list[str] = []
        focus = self._resolve(request.focus, warnings)
        chunk_ids = self.store.chunks_mentioning([focus.id], request.max_passages)
        edges = [
            edge
            for edge in self.store.expand([focus.id], hops=1, min_confidence=request.min_confidence)
            if focus.id in (edge.relation.subject_id, edge.relation.object_id)
            and edge.relation.subject_id != edge.relation.object_id
        ]
        if not chunk_ids and not edges:
            raise NoEvidenceError(
                f"No passage in the corpus mentions {focus.name}; ingest sources about it first."
            )
        others = self.store.get_entities(
            {edge.relation.subject_id for edge in edges}
            | {edge.relation.object_id for edge in edges}
        )

        sources: dict[str, SourceRef] = {}
        grouped: dict[tuple[str, Predicate, Direction], SpecItem] = {}
        sections: dict[tuple[str, Predicate, Direction], Section] = {}
        for edge in sorted(edges, key=lambda edge: edge.relation.id):
            relation = edge.relation
            outgoing = relation.subject_id == focus.id
            other = others.get(relation.object_id if outgoing else relation.subject_id)
            source = self._source(relation.chunk_id, sources)
            if other is None or source is None:
                continue
            direction: Direction = "outgoing" if outgoing else "incoming"
            key = (other.id, relation.predicate, direction)
            item = grouped.get(key)
            if item is None:
                subject, obj = (focus.name, other.name) if outgoing else (other.name, focus.name)
                grouped[key] = SpecItem(
                    entity=_ref(other),
                    predicate=relation.predicate,
                    direction=direction,
                    statement=f"{subject} {relation.predicate.replace('_', ' ')} {obj}.",
                    confidence=relation.confidence,
                    sources=[source],
                    chunk_ids=[relation.chunk_id],
                )
                sections[key] = _section(relation.predicate, outgoing, other)
            elif relation.chunk_id not in item.chunk_ids:
                item.sources.append(source)
                item.chunk_ids.append(relation.chunk_id)
                item.confidence = max(item.confidence, relation.confidence)

        by_section: dict[Section, list[SpecItem]] = {}
        for key, item in grouped.items():
            by_section.setdefault(sections[key], []).append(item)
        for items in by_section.values():
            items.sort(key=lambda item: (-item.confidence, item.entity.id, item.predicate))

        passages = [p for p in (self._key_passage(cid, sources) for cid in chunk_ids) if p]
        open_questions = [
            question.format(name=focus.name)
            for section, question in OPEN_QUESTIONS.items()
            if not by_section.get(section)
        ]
        open_questions.append(PARAMETERS_QUESTION)
        if not edges:
            warnings.append(
                f"The corpus mentions {focus.name} but states no relation about it at confidence "
                f"{request.min_confidence} or above; the spec has only key passages."
            )

        snapshot = self.store.current_snapshot()
        spec = StrategySpec(
            name=focus.name if focus.type == "strategy" else f"{focus.name} strategy",
            focus=focus,
            signals=by_section.get("signals", []),
            universe=by_section.get("universe", []),
            data=by_section.get("data", []),
            methods=by_section.get("methods", []),
            metrics=by_section.get("metrics", []),
            risks=by_section.get("risks", []),
            context=by_section.get("context", []),
            key_passages=passages,
            open_questions=open_questions,
            corpus_snapshot=snapshot.id if snapshot else None,
        )
        evidence = [
            Evidence(kind="retrieved", statement=item.statement, sources=item.sources)
            for section in SECTIONS
            for item in by_section.get(section, [])
        ]
        evidence += [
            Evidence(
                kind="retrieved",
                statement=f"The passage mentions {focus.name}.",
                sources=[passage.source],
            )
            for passage in passages
        ]
        return StrategyDraft(
            spec=spec,
            warnings=warnings,
            evidence=evidence,
            data_versions={"corpus_snapshot": snapshot.id} if snapshot else {},
        )

    def _resolve(self, focus: str, warnings: list[str]) -> EntityRef:
        """Return the entity ``focus`` names: an exact id, else the first matched name."""
        text = focus.strip()
        exact = self.store.get_entities([text]).get(text)
        candidates = (
            [exact]
            if exact is not None
            else list(
                self.store.get_entities(
                    dict.fromkeys(match.entity_id for match in self.matcher.find(text))
                ).values()
            )
        )
        named = [entity for entity in candidates if entity.type in SIGNAL_TYPES]
        if not named:
            raise UnknownFocusError(
                f"'{focus}' names no signal, factor, or strategy in the vocabulary. Use "
                "find_related_signals to find one, or pass an id such as 'signal:carry'."
            )
        # get_entities does not keep the matcher's order, so put matches back in text order.
        order = {match.entity_id: match.start for match in self.matcher.find(text)}
        named.sort(key=lambda entity: order.get(entity.id, -1))
        if len(named) > 1:
            warnings.append(
                f"Built the spec around {named[0].name}; the request also names "
                f"{', '.join(entity.name for entity in named[1:])}. Generate one spec per signal."
            )
        return _ref(named[0])

    def _source(self, chunk_id: str, cache: dict[str, SourceRef]) -> SourceRef | None:
        if chunk_id not in cache:
            chunk = self.store.get_chunk(chunk_id)
            if chunk is None:
                return None
            cache[chunk_id] = source_of(self.store, chunk)
        return cache[chunk_id]

    def _key_passage(self, chunk_id: str, cache: dict[str, SourceRef]) -> KeyPassage | None:
        chunk = self.store.get_chunk(chunk_id)
        source = self._source(chunk_id, cache)
        if chunk is None or source is None:
            return None
        truncated = len(chunk.text) > PASSAGE_PREVIEW_CHARS
        return KeyPassage(
            chunk_id=chunk.id,
            source=source,
            text=chunk.text[:PASSAGE_PREVIEW_CHARS] + ("…" if truncated else ""),
            truncated=truncated,
        )


def _ref(entity: Entity) -> EntityRef:
    return EntityRef(id=entity.id, name=entity.name, type=entity.type)


def _section(predicate: Predicate, outgoing: bool, other: Entity) -> Section:
    """Place an edge: contradictions and explanations of the focus are risks, else by type."""
    if predicate == "contradicts" or (predicate == "explained_by" and outgoing):
        return "risks"
    return SECTION_BY_TYPE.get(other.type, "context")
