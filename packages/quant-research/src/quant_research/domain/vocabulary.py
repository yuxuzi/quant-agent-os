"""A curated, versioned vocabulary of quantitative entities and their aliases."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from pydantic import BaseModel, Field

from quant_research.domain.models import Entity


class VocabularyError(ValueError):
    """Raised when a vocabulary file is invalid."""


class Vocabulary(BaseModel):
    """Entities with aliases; ``version`` is recorded in every corpus snapshot."""

    version: str = Field(min_length=1)
    entities: list[Entity] = Field(min_length=1)


@dataclass(frozen=True)
class Match:
    """An entity matched at ``[start, end)`` of a text."""

    entity_id: str
    start: int
    end: int


BUNDLED = "quant-v1"


def load_vocabulary(path: Path | None = None) -> Vocabulary:
    """Load a vocabulary TOML file, or the bundled ``quant-v1`` vocabulary by default."""
    if path is None:
        text = files("quant_research").joinpath(f"vocabularies/{BUNDLED}.toml").read_text("utf-8")
    else:
        text = path.read_text("utf-8")
    try:
        vocabulary = Vocabulary.model_validate(tomllib.loads(text))
    except (tomllib.TOMLDecodeError, ValueError) as error:
        raise VocabularyError(f"Invalid vocabulary {path or BUNDLED}: {error}") from error
    ids = [entity.id for entity in vocabulary.entities]
    duplicates = sorted({entity_id for entity_id in ids if ids.count(entity_id) > 1})
    if duplicates:
        raise VocabularyError(f"Duplicate vocabulary ids: {', '.join(duplicates)}.")
    return vocabulary


class EntityMatcher:
    """Find vocabulary entities in text, preferring the longest non-overlapping match.

    Short all-capital aliases such as ``HML`` match case-sensitively; everything else matches
    case-insensitively on word boundaries.
    """

    def __init__(self, vocabulary: Vocabulary) -> None:
        terms: list[tuple[str, str]] = []
        for entity in vocabulary.entities:
            for term in {entity.name, *entity.aliases}:
                terms.append((term, entity.id))
        terms.sort(key=lambda item: len(item[0]), reverse=True)
        self._patterns = [(_pattern(term), entity_id) for term, entity_id in terms]

    def find(self, text: str) -> list[Match]:
        """Return matches in text order."""
        taken: list[tuple[int, int]] = []
        matches: list[Match] = []
        for pattern, entity_id in self._patterns:
            for found in pattern.finditer(text):
                start, end = found.span()
                if any(start < other_end and other_start < end for other_start, other_end in taken):
                    continue
                taken.append((start, end))
                matches.append(Match(entity_id, start, end))
        return sorted(matches, key=lambda match: match.start)


def _pattern(term: str) -> re.Pattern[str]:
    flags = 0 if term.isupper() and len(term) <= 5 else re.IGNORECASE
    body = r"[\s-]+".join(re.escape(part) for part in re.split(r"[\s-]+", term.strip()))
    return re.compile(rf"(?<![\w-]){body}s?(?![\w-])", flags)
