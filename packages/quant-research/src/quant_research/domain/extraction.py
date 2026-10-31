"""Deterministic, rule-based extraction of mentions and relations from passages.

Relations come only from two entities in the same sentence, judged by the text between them:
- A cue phrase sets the predicate. Passive and inverse cues ("is predicted by", "explains")
  swap subject and object, so the edge points the right way.
- A negation in the few words right before the cue ("does not predict", "fails to explain",
  "isn't correlated") suppresses the pair entirely, with no typed edge and no ``related_to``
  edge. A negation elsewhere, as in "X, which is not small, predicts Y", does not, and neither
  does "not only". Without any cue, a negation anywhere between the two entities suppresses
  the ``related_to`` edge ("X is unrelated to Y"). An explicit "contradicts" is a relation.
- Otherwise the pair becomes a low-confidence ``related_to`` edge.

Every relation keeps the chunk that supports it, so each edge can be cited.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from quant_research.domain.models import Chunk, Entity, Mention, Predicate, Relation
from quant_research.domain.vocabulary import EntityMatcher, Match

EXTRACTOR = "rules"
EXTRACTOR_VERSION = "rules-v4"

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class _Cue:
    pattern: re.Pattern[str]
    predicate: Predicate
    confidence: float
    object_types: frozenset[str] | None = None
    swap: bool = False  # the cue names the object first ("Y is predicted by X")


_NEGATION = re.compile(
    r"\b(?:not|never|no|neither|nor|cannot|fails? to|failed to|unrelated to)\b|n't\b", re.I
)
_AFFIRMATIVE = re.compile(r"\bnot\s+(?:only|just|merely)\b", re.I)
NEGATION_WINDOW_WORDS = 3  # words before the cue that can negate it
# Ordered: passive and inverse forms come before the active cues they contain.
_CUES = (
    _Cue(
        re.compile(r"\b(?:explained|subsumed|absorbed|captured)\s+by\b", re.I), "explained_by", 0.75
    ),
    _Cue(
        re.compile(r"\b(?:explains?|captures?|subsumes?|absorbs?)\b", re.I),
        "explained_by",
        0.75,
        swap=True,
    ),
    _Cue(re.compile(r"\b(?:contradicts?|refutes?|inconsistent with)", re.I), "contradicts", 0.7),
    _Cue(re.compile(r"\b(?:predicted|forecast(?:ed)?)\s+by\b", re.I), "predicts", 0.8, swap=True),
    _Cue(re.compile(r"\b(?:predict|forecast)", re.I), "predicts", 0.8),
    _Cue(re.compile(r"\bcorrelat", re.I), "correlates_with", 0.8),
    _Cue(re.compile(r"\bextended\s+by\b", re.I), "extends", 0.75, swap=True),
    _Cue(re.compile(r"\b(?:extends?|extending|builds? on|refines?)\b", re.I), "extends", 0.75),
    _Cue(re.compile(r"\b(?:used|employed)\s+by\b", re.I), "uses", 0.6, swap=True),
    _Cue(
        re.compile(r"\b(?:using|uses?|with|via|estimated)\b", re.I),
        "uses",
        0.6,
        frozenset({"method", "dataset", "metric"}),
    ),
    _Cue(
        re.compile(r"\b(?:using|uses?|via|relies on)\b", re.I),
        "uses",
        0.6,
        frozenset({"signal", "factor"}),
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
        for index, first in enumerate(matches):
            for second in matches[index + 1 :]:
                if second.entity_id == first.entity_id:
                    continue
                judged = self._classify(sentence[first.end : second.start], first, second)
                if judged is None:
                    continue
                subject, predicate, target, confidence = judged
                key = f"{subject}|{predicate}|{target}|{chunk_id}"
                found.append(
                    Relation(
                        id="rel:" + hashlib.sha256(key.encode()).hexdigest()[:16],
                        subject_id=subject,
                        predicate=predicate,
                        object_id=target,
                        chunk_id=chunk_id,
                        extractor=EXTRACTOR,
                        extractor_version=EXTRACTOR_VERSION,
                        confidence=confidence,
                    )
                )
        return found

    def _classify(
        self, between: str, first: Match, second: Match
    ) -> tuple[str, Predicate, str, float] | None:
        """Return ``(subject, predicate, object, confidence)``, or None for a negated pair."""
        for cue in _CUES:
            subject, target = (second, first) if cue.swap else (first, second)
            if (
                cue.object_types is not None
                and self._types.get(target.entity_id) not in cue.object_types
            ):
                continue
            found = cue.pattern.search(between)
            if found is None:
                continue
            words_before_cue = " ".join(between[: found.start()].split()[-NEGATION_WINDOW_WORDS:])
            if _negated(words_before_cue):
                return None
            return subject.entity_id, cue.predicate, target.entity_id, cue.confidence
        if _negated(between):
            return None
        return first.entity_id, "related_to", second.entity_id, _COOCCURRENCE


def _negated(text: str) -> bool:
    """Return whether ``text`` negates, ignoring affirmative "not only" and "not just"."""
    return _NEGATION.search(_AFFIRMATIVE.sub(" ", text)) is not None
