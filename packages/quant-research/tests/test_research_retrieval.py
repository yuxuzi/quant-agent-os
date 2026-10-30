"""Unit tests for hybrid retrieval and evidence packets."""

import pytest

from quant_research import ResearchApp, ResearchQuery, open_research
from quant_research.application.retrieval import UnknownPassageError, _fuse


def _related(research: ResearchApp, query: str, **options: object) -> list[str]:
    packet = research.research.find_related(ResearchQuery(query=query, **options))  # type: ignore[arg-type]
    return [entity.id for entity in packet.related]


def test_graph_links_find_signals_related_to_the_query(research: ResearchApp) -> None:
    related = _related(research, "What is related to short-term reversal?")

    assert {"signal:order-flow-imbalance", "signal:liquidity-provision"} <= set(related)


def test_every_graph_link_is_cited_as_retrieved_evidence(research: ResearchApp) -> None:
    packet = research.research.find_related(ResearchQuery(query="short-term reversal"))
    links = [link for entity in packet.related for link in entity.links]

    assert len(packet.evidence) == len(links)
    assert all(item.kind == "retrieved" and item.sources for item in packet.evidence)
    assert all(item.sources[0].location for item in packet.evidence)


def test_packets_record_the_corpus_snapshot(research: ResearchApp) -> None:
    packet = research.research.find_related(ResearchQuery(query="PEAD"))
    snapshot = research.store.current_snapshot()

    assert snapshot is not None
    assert packet.corpus_snapshot == snapshot.id
    assert packet.data_versions == {"corpus_snapshot": snapshot.id}


def test_retrieval_is_deterministic(research: ResearchApp) -> None:
    query = ResearchQuery(query="momentum and reversal", hops=2)

    assert research.research.find_related(query) == research.research.find_related(query)


def test_entity_types_filter_the_related_entities(research: ResearchApp) -> None:
    related = _related(research, "short-term reversal", entity_types=["concept"])

    assert related == ["concept:bid-ask-bounce"]


def test_an_empty_store_warns() -> None:
    with open_research(":memory:") as research:
        packet = research.research.find_related(ResearchQuery(query="momentum"))

    assert packet.corpus_snapshot is None
    assert any("empty" in warning for warning in packet.warnings)


def test_passages_can_be_fetched_in_full(research: ResearchApp) -> None:
    packet = research.research.find_related(ResearchQuery(query="PEAD"))

    passage = research.research.get_passage(packet.passages[0].chunk_id)

    assert "analyst revisions" in passage.text
    assert passage.source.source_id == "notes/earnings.txt"
    with pytest.raises(UnknownPassageError, match="nope"):
        research.research.get_passage("nope")


def test_rank_fusion_rewards_agreement_and_breaks_ties_by_id() -> None:
    fused = _fuse({"keyword": ["a", "b"], "graph": ["b", "c"]})

    assert [chunk_id for chunk_id, _, _ in fused] == ["b", "a", "c"]
    assert fused[0][2] == ["keyword", "graph"]


def test_definitions_rank_through_the_mention_channel(research: ResearchApp) -> None:
    packet = research.research.find_related(ResearchQuery(query="short-term reversal"))

    assert any("mention" in passage.channels for passage in packet.passages)
    assert packet.passages[0].source.source_id == "reversal-and-microstructure.md"


def test_queries_without_entities_seed_from_the_best_passage(research: ResearchApp) -> None:
    packet = research.research.find_related(
        ResearchQuery(query="why do market makers earn the spread")
    )

    assert 0 < len(packet.seed_entities) <= 3
    direct = [entity for entity in packet.related if entity.hops == 0]
    assert "signal:liquidity-provision" in {entity.id for entity in direct}
    assert all(entity.mentioned_in for entity in direct)
    assert any("best keyword passage" in warning for warning in packet.warnings)


def test_unanswerable_queries_return_nothing_and_say_so(research: ResearchApp) -> None:
    packet = research.research.find_related(ResearchQuery(query="crypto funding rates"))

    assert packet.passages == [] and packet.related == []
    assert any("Nothing in the corpus matched" in warning for warning in packet.warnings)


def test_stopwords_do_not_pull_in_unrelated_passages(research: ResearchApp) -> None:
    packet = research.research.find_related(
        ResearchQuery(query="What is related to short-term reversal?")
    )

    assert "notes/injection.md" not in {p.source.source_id for p in packet.passages}
