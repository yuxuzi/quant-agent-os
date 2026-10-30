"""Unit tests for the vocabulary matcher, chunker, and rule extractor."""

import pytest

from quant_research.domain.chunking import MAX_CHUNK_CHARS, MIN_CHUNK_CHARS, split_passages
from quant_research.domain.extraction import RuleExtractor
from quant_research.domain.models import Chunk
from quant_research.domain.text import content_terms
from quant_research.domain.vocabulary import (
    EntityMatcher,
    VocabularyError,
    load_vocabulary,
)

VOCABULARY = load_vocabulary()
MATCHER = EntityMatcher(VOCABULARY)
EXTRACTOR = RuleExtractor(MATCHER, {entity.id: entity for entity in VOCABULARY.entities})


def _ids(text: str) -> list[str]:
    return [match.entity_id for match in MATCHER.find(text)]


def test_bundled_vocabulary_has_unique_typed_ids() -> None:
    assert VOCABULARY.version == "quant-v1"
    assert all(entity.id.startswith(f"{entity.type}:") for entity in VOCABULARY.entities)


def test_matcher_prefers_the_longest_alias() -> None:
    assert _ids("Time-series momentum differs from momentum.") == [
        "signal:time-series-momentum",
        "factor:momentum",
    ]


def test_short_capital_aliases_match_case_sensitively() -> None:
    assert _ids("HML and PEAD") == ["factor:value", "signal:earnings-drift"]
    assert _ids("the hml of a pead") == []


def test_matcher_accepts_plurals_spaces_for_hyphens_and_any_case() -> None:
    assert _ids("Pairs Trading strategies and short term reversals") == [
        "strategy:pairs-trading",
        "signal:short-term-reversal",
    ]


def test_matcher_respects_word_boundaries() -> None:
    assert _ids("carryover and turnovers") == ["metric:turnover"]


def test_invalid_vocabulary_files_fail_with_the_path(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "bad.toml"
    path.write_text('version = "x"\nentities = []\n')

    with pytest.raises(VocabularyError, match=r"bad\.toml"):
        load_vocabulary(path)


def test_duplicate_vocabulary_ids_are_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    entity = '[[entities]]\nid = "factor:a"\ntype = "factor"\nname = "A"\n'
    path = tmp_path / "dup.toml"
    path.write_text('version = "x"\n' + entity + entity)

    with pytest.raises(VocabularyError, match="factor:a"):
        load_vocabulary(path)


def test_passages_keep_line_numbers_and_sections() -> None:
    text = "# Title\n\nFirst para\ncontinues.\n\n## Results\n\nSecond para."

    passages = split_passages(text, first_line=5)

    assert [(p.line_start, p.line_end, p.section) for p in passages] == [
        (7, 8, "Title"),
        (12, 12, "Results"),
    ]
    assert passages[0].text == "First para\ncontinues."


def test_passages_never_exceed_the_limit_unless_one_paragraph_does() -> None:
    paragraph = "word " * 100
    passages = split_passages("\n\n".join([paragraph] * 6) + "\n\n" + "x" * (MAX_CHUNK_CHARS + 5))

    assert all(len(p.text) <= MAX_CHUNK_CHARS for p in passages[:-1])
    assert len(passages[-1].text) == MAX_CHUNK_CHARS + 5


def _relations(text: str) -> list[tuple[str, str, str]]:
    chunk = Chunk(id="c", document_id="d", ordinal=0, text=text, line_start=1, line_end=1)
    _, relations = EXTRACTOR.extract(chunk)
    return [(r.subject_id, r.predicate, r.object_id) for r in relations]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Order-flow imbalance predicts short-term reversal.",
            ("signal:order-flow-imbalance", "predicts", "signal:short-term-reversal"),
        ),
        (
            "Short-term reversal correlates with liquidity provision.",
            ("signal:short-term-reversal", "correlates_with", "signal:liquidity-provision"),
        ),
        (
            "The reversal effect is explained by bid-ask bounce.",
            ("signal:short-term-reversal", "explained_by", "concept:bid-ask-bounce"),
        ),
        (
            "Momentum contradicts the reversal effect.",
            ("factor:momentum", "contradicts", "signal:short-term-reversal"),
        ),
        (
            "Time-series momentum extends cross-sectional momentum.",
            ("signal:time-series-momentum", "extends", "factor:momentum"),
        ),
        (
            "Accruals are estimated with Fama-MacBeth regressions.",
            ("signal:accruals", "uses", "method:fama-macbeth"),
        ),
        (
            "Carry is tested on currencies.",
            ("signal:carry", "tested_on", "asset_class:fx"),
        ),
        (
            "Carry and crowding.",
            ("signal:carry", "related_to", "concept:crowding"),
        ),
    ],
)
def test_cue_phrases_set_the_predicate(text: str, expected: tuple[str, str, str]) -> None:
    assert _relations(text) == [expected]


def test_relations_need_the_same_sentence_and_distinct_entities() -> None:
    assert _relations("Carry is a signal. Crowding is a risk.") == []
    assert _relations("Momentum and 12-1 momentum.") == []


def test_mentions_point_at_the_exact_span() -> None:
    text = "Intro sentence. Then PEAD appears."
    chunk = Chunk(id="c", document_id="d", ordinal=0, text=text, line_start=1, line_end=1)

    mentions, _ = EXTRACTOR.extract(chunk)

    assert [text[m.start : m.end] for m in mentions] == ["PEAD"]


def test_content_terms_drop_stopwords_and_duplicates() -> None:
    assert content_terms("What is related to the short-term Reversal, reversal?") == [
        "related",
        "short",
        "term",
        "reversal",
    ]
    assert content_terms("is it?") == []


def test_short_paragraphs_merge_but_long_ones_stand_alone() -> None:
    long = "x" * MIN_CHUNK_CHARS
    passages = split_passages(f"Short.\n\n{long}\n\n{long}")

    assert [(p.line_start, p.line_end) for p in passages] == [(1, 3), (5, 5)]


def test_strategies_using_a_signal_get_a_uses_edge() -> None:
    assert _relations("Trend following uses time-series momentum as its signal.") == [
        ("strategy:trend-following", "uses", "signal:time-series-momentum")
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Short-term reversal is predicted by order-flow imbalance.",
            ("signal:order-flow-imbalance", "predicts", "signal:short-term-reversal"),
        ),
        ("Carry is forecast by crowding.", ("concept:crowding", "predicts", "signal:carry")),
        ("Profitability explains HML.", ("factor:value", "explained_by", "factor:profitability")),
        (
            "Profitability captures the value premium.",
            ("factor:value", "explained_by", "factor:profitability"),
        ),
        (
            "Momentum is extended by time-series momentum.",
            ("signal:time-series-momentum", "extends", "factor:momentum"),
        ),
        (
            "Momentum is used by factor timing.",
            ("strategy:factor-timing", "uses", "factor:momentum"),
        ),
    ],
)
def test_passive_and_inverse_cues_point_edges_the_right_way(
    text: str, expected: tuple[str, str, str]
) -> None:
    assert _relations(text) == [expected]


@pytest.mark.parametrize(
    "text",
    [
        "Order-flow imbalance does not predict carry.",
        "Profitability fails to explain carry.",
        "Momentum does not contradict carry.",
        "Carry is unrelated to crowding.",
        "Carry isn't correlated with crowding.",
        "Momentum never predicts carry.",
        "Momentum is not robust to transaction costs.",
    ],
)
def test_negated_pairs_produce_no_edge(text: str) -> None:
    assert _relations(text) == []
