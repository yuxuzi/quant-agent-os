"""The mock world's answer key is consistent with its own text (plan G1)."""

from pathlib import Path

import pytest
from mock_world import DOCUMENTS, Distractor, Fact, Negation, facts, true_triples, write_world

from quant_research.domain.vocabulary import EntityMatcher, load_vocabulary

VOCABULARY = load_vocabulary()
MATCHER = EntityMatcher(VOCABULARY)
LINES = [line for doc in DOCUMENTS for _, lines in doc.parts for line in lines]


def _entities(text: str) -> set[str]:
    return {match.entity_id for match in MATCHER.find(text)}


@pytest.mark.parametrize(
    "line",
    [line for line in LINES if not isinstance(line, str)],
    ids=lambda line: line.sentence[:40],
)
def test_each_statement_names_exactly_its_two_entities(line: Fact | Negation | Distractor) -> None:
    pair = {line.subject, line.object} if isinstance(line, Fact) else {line.first, line.second}

    assert _entities(line.sentence) == pair


def test_filler_lines_name_no_entity() -> None:
    filler = [line for line in LINES if isinstance(line, str)]

    assert filler
    assert all(not _entities(line) for line in filler)


def test_every_entity_in_the_key_is_in_the_vocabulary() -> None:
    known = {entity.id for entity in VOCABULARY.entities}

    assert {e for triple in true_triples() for e in (triple[0], triple[2])} <= known


def test_the_world_renders_deterministically(tmp_path: Path) -> None:
    first = write_world(tmp_path / "a")
    second = write_world(tmp_path / "b")

    files = sorted(p.relative_to(first) for p in first.rglob("*") if p.is_file())
    assert files == sorted(p.relative_to(second) for p in second.rglob("*") if p.is_file())
    assert all((first / f).read_bytes() == (second / f).read_bytes() for f in files)
    assert {f.suffix for f in files} == {".md", ".txt", ".pdf"}
    assert len(facts()) > len(true_triples())  # some truths are stated twice, in other voices
