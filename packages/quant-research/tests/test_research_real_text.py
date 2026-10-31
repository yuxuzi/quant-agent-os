"""Guards for real papers, each found by the real-corpus evaluation (plan steps C2-C4).

The real corpus itself is opt-in (``real_corpus.py``); these tests pin each behaviour with a
small hand-written case, so they run offline.
"""

from pathlib import Path

import pytest
from pdf_fixtures import write_pdf
from real_corpus import GOLD, LABELS, PAPERS, labels, papers, questions

from quant_research import ResearchQuery, open_research
from quant_research.domain.chunking import (
    APPENDIX,
    REFERENCES,
    is_reference_list,
    split_pages,
)
from quant_research.domain.extraction import (
    MAX_RELATED_GAP_WORDS,
    MAX_TYPED_GAP_WORDS,
    RuleExtractor,
    is_prose,
)
from quant_research.domain.models import Chunk
from quant_research.domain.vocabulary import EntityMatcher, load_vocabulary
from quant_research.infrastructure.pdf import extract_pages, normalize_pages
from quant_research.infrastructure.sqlite_store import SqliteKnowledgeStore

VOCABULARY = load_vocabulary()
EXTRACTOR = RuleExtractor(
    EntityMatcher(VOCABULARY), {entity.id: entity for entity in VOCABULARY.entities}
)


def _relations(text: str, section: str | None = None) -> list[tuple[str, str, str]]:
    chunk = Chunk(
        id="c", document_id="d", ordinal=0, text=text, line_start=1, line_end=1, section=section
    )
    return [(r.subject_id, r.predicate, r.object_id) for r in EXTRACTOR.extract(chunk)[1]]


# PDF text normalisation (C2)


def test_ligatures_become_letters() -> None:
    assert normalize_pages(["coeﬃcient of the ﬁrst factor"]) == ("coefficient of the first factor",)


def test_hyphenated_line_breaks_are_rejoined_without_moving_lines() -> None:
    page = "we build time-series mo-\nmentum strategies, and their perfor-\nmance.\nEnd"

    (normalized,) = normalize_pages([page])

    assert normalized.splitlines() == [
        "we build time-series momentum",
        "strategies, and their performance.",
        "",
        "End",
    ]


def test_hyphens_that_belong_to_the_word_stay() -> None:
    pages = ["a cross-sectional test", "the cross-\nsectional and t-\nstatistic results"]

    assert normalize_pages(pages)[1].splitlines() == [
        "the cross-sectional",
        "and t-statistic",
        "results",
    ]


def test_vocabulary_matches_across_a_rejoined_line_break(tmp_path: Path) -> None:
    pdf = write_pdf(
        tmp_path / "paper.pdf",
        [["Time-series mo-", "mentum is tested on futures in this paper."]],
    )

    (page,) = extract_pages(pdf)

    assert "signal:time-series-momentum" in {
        m.entity_id for m in EntityMatcher(VOCABULARY).find(page)
    }


# Reference lists (C3)


def test_the_reference_list_section_carries_across_pages_until_an_appendix() -> None:
    pages = [
        "Findings text.\n7 References",
        "Rallis, Momentum strategies in commodity futures markets, J.",
        "Appendix A. Extra tables\nMore text.",
    ]

    sections = [(page, passage.section) for page, passage in split_pages(pages)]

    assert sections == [(1, None), (1, REFERENCES), (2, REFERENCES), (3, APPENDIX)]


def test_a_sentence_mentioning_references_does_not_open_the_list() -> None:
    passages = split_pages(["See the references below for momentum in futures."])

    assert [passage.section for _, passage in passages] == [None]


def test_reference_entries_yield_mentions_but_no_relations() -> None:
    entry = "Momentum strategies in commodity futures markets."

    assert _relations(entry) == [("factor:momentum", "tested_on", "asset_class:futures")]
    assert _relations(entry, section="References") == []
    assert is_reference_list("12. Bibliography")
    assert not is_reference_list("Results")


# Prose, signatures, and distance (C3)


def test_table_rows_and_run_on_text_are_not_prose() -> None:
    row = "SMB 0.032 0.249 1.000 -0.120 HML -0.281 -0.208 -0.120 1.000 RMW 0.035 CMA 0.622"

    assert not is_prose(row)
    assert not is_prose("word " * 81)
    assert is_prose("Value correlates with momentum in 2 of 3 markets.")
    assert _relations(row) == []


@pytest.mark.parametrize(
    "text",
    [
        "Futures across currencies are studied here.",  # an asset class is not tested on one
        "CRSP data with a transaction cost model.",  # a dataset uses nothing
        "The Sharpe ratio across FX markets is high.",  # a metric is not tested on markets
    ],
)
def test_typed_predicates_need_entity_types_they_can_hold(text: str) -> None:
    relations = _relations(text)

    assert relations
    assert all(predicate == "related_to" for _, predicate, _ in relations)


def test_a_failed_signature_never_retries_a_shorter_cue() -> None:
    # "forecast by" fails (CRSP cannot predict); "forecast" alone would reverse the edge.
    assert _relations("Momentum is forecast by CRSP.") == [
        ("factor:momentum", "related_to", "dataset:crsp")
    ]


def test_distant_mentions_get_weaker_or_no_edges() -> None:
    near = "Momentum predicts " + "very " * (MAX_TYPED_GAP_WORDS - 1) + "short-term reversal."
    far = "Momentum predicts " + "very " * MAX_TYPED_GAP_WORDS + "short-term reversal."
    unrelated = "Momentum " + "word " * (MAX_RELATED_GAP_WORDS + 1) + "short-term reversal."

    assert [p for _, p, _ in _relations(near)] == ["predicts"]
    assert [p for _, p, _ in _relations(far)] == ["related_to"]
    assert _relations(unrelated) == []


# Abstention (C4)


def _store_with(tmp_path: Path) -> Path:
    (tmp_path / "note.md").write_text(
        "Prediction accuracy of the model improves out of sample.\n\n"
        "Momentum predicts returns in the cross-section of stocks.\n"
    )
    return tmp_path


def test_a_query_the_corpus_cannot_answer_returns_no_passages(tmp_path: Path) -> None:
    with open_research(":memory:") as research:
        research.ingest.ingest(_store_with(tmp_path))

        packet = research.research.find_related(
            ResearchQuery(query="protein folding prediction accuracy")
        )

    assert packet.passages == []
    assert any("match too little of the query" in warning for warning in packet.warnings)


def test_a_query_naming_an_entity_is_never_filtered(tmp_path: Path) -> None:
    with open_research(":memory:") as research:
        research.ingest.ingest(_store_with(tmp_path))

        packet = research.research.find_related(ResearchQuery(query="momentum folding protein"))

    assert packet.passages


@pytest.mark.parametrize("fts", [True, False])
def test_term_frequencies_count_chunks_per_term(tmp_path: Path, fts: bool) -> None:
    store = SqliteKnowledgeStore(":memory:")
    store.has_fts = fts
    with open_research(":memory:") as research:
        research.ingest.store = store
        research.ingest.ingest(_store_with(tmp_path))

    total, frequencies = store.term_frequencies(["prediction", "momentum", "protein"])
    hits = store.keyword_search("prediction protein", 5)
    store.close()

    assert total == 1  # both short paragraphs merge into one chunk
    assert frequencies == {"prediction": 1, "momentum": 1, "protein": 0}
    assert hits[0].matched == ["prediction"]


# The real corpus's own files (the evaluation itself is opt-in)


def test_real_corpus_files_are_consistent() -> None:
    ids = [paper.id for paper in papers()]
    asked = {doc for question in questions() for doc in question["documents"]}
    judged = labels()

    assert len(ids) == len(set(ids)) == 21
    assert all(len(paper.sha256) == 64 for paper in papers())
    assert asked <= set(ids)
    assert {paper for paper, *_ in judged} <= set(ids)
    assert {question["kind"] for question in questions()} == {"topic", "paraphrase", "unanswerable"}
    assert all(
        question["documents"] for question in questions() if question["kind"] != "unanswerable"
    )
    assert all(path.is_file() for path in (PAPERS, GOLD, LABELS))
