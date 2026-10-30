"""Deterministic, rule-based extraction of mentions and relations from passages.

Relations come only from two entities in the same sentence. A cue phrase between them sets the
predicate; otherwise the pair becomes a low-confidence ``related_to`` edge. Every relation keeps
the chunk that supports it, so each edge can be cited.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from quant_research.domain.models import Chunk, Entity, Mention, Predicate, Relation
from quant_research.domain.vocabulary import EntityMatcher, Match

EXTRACTOR = "rules"
EXTRACTOR_VERSION = "rules-v1"

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class _Cue:
    pattern: re.Pattern[str]
    predicate: Predicate
    confidence: float
    object_types: frozenset[str] | None = None


_CUES = (
    _Cue(
        re.compile(r"\b(?:explained|subsumed|absorbed|captured)\s+by\b", re.I), "explained_by", 0.75
    ),
    _Cue(
        re.compile(r"\b(?:contradict|refute|does not survive|fails? to)", re.I), "contradicts", 0.7
    ),
    _Cue(re.compile(r"\b(?:predict|forecast)", re.I), "predicts", 0.8),
    _Cue(re.compile(r"\bcorrelat", re.I), "correlates_with", 0.8),
    _Cue(re.compile(r"\b(?:extends?|extending|builds? on|refines?)\b", re.I), "extends", 0.75),
    _Cue(
        re.compile(r"\b(?:using|uses?|with|via|estimated)\b", re.I),
        "uses",
        0.6,
        frozenset({"method", "dataset", "metric"}),
    ),
    _Cue(
        re.compile(r"\b(?:on|in|across|for)\b", re.I),
        "tested_on",
        0.6,
        frozenset({"asset_class", "dataset"}),
    ),
)
_COOCCURRENCE = 0.4


class RuleExtractor:
    """Extract mentions and relations with a vocabulary and fixed cue phrases."""

    name = EXTRACTOR
    version = EXTRACTOR_VERSION

    def __init__(self, matcher: EntityMatcher, entities: dict[str, Entity]) -> None:
        self._matcher = matcher
        self._types = {entity_id: entity.type for entity_id, entity in entities.items()}

    def extract(self, chunk: Chunk) -> tuple[list[Mention], list[Relation]]:
        """Return the mentions and relations found in ``chunk``."""
        mentions: list[Mention] = []
        relations: dict[str, Relation] = {}
        offset = 0
        for sentence in _SENTENCE_END.split(chunk.text):
            offset = chunk.text.index(sentence, offset)
            matches = self._matcher.find(sentence)
            mentions += [
                Mention(
                    entity_id=match.entity_id,
                    chunk_id=chunk.id,
                    start=offset + match.start,
                    end=offset + match.end,
                    extractor=EXTRACTOR,
                    confidence=1.0,
                )
                for match in matches
            ]
            for relation in self._relations(chunk.id, sentence, matches):
                relations.setdefault(relation.id, relation)
            offset += len(sentence)
        return mentions, list(relations.values())

    def _relations(self, chunk_id: str, sentence: str, matches: list[Match]) -> list[Relation]:
        found: list[Relation] = []
        for index, subject in enumerate(matches):
            for target in matches[index + 1 :]:
                if target.entity_id == subject.entity_id:
                    continue
                predicate, confidence = self._classify(
                    sentence[subject.end : target.start], target.entity_id
                )
                key = f"{subject.entity_id}|{predicate}|{target.entity_id}|{chunk_id}"
                found.append(
                    Relation(
                        id="rel:" + hashlib.sha256(key.encode()).hexdigest()[:16],
                        subject_id=subject.entity_id,
                        predicate=predicate,
                        object_id=target.entity_id,
                        chunk_id=chunk_id,
                        extractor=EXTRACTOR,
                        extractor_version=EXTRACTOR_VERSION,
                        confidence=confidence,
                    )
                )
        return found

    def _classify(self, between: str, object_id: str) -> tuple[Predicate, float]:
        for cue in _CUES:
            if cue.object_types is not None and self._types.get(object_id) not in cue.object_types:
                continue
            if cue.pattern.search(between):
                return cue.predicate, cue.confidence
        return "related_to", _COOCCURRENCE
