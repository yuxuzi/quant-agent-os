"""Unit tests for hybrid retrieval and evidence packets."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from quant_research import ResearchApp, ResearchQuery, open_research
from quant_research.application.retrieval import (
    ALL_CHANNELS,
    ResearchService,
    UnknownPassageError,
    _fuse,
    _shortest_paths,
)
from quant_research.domain.models import Relation
from quant_research.domain.ports import GraphEdge


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


def _relation(rid: str, subject: str, obj: str, confidence: float) -> Relation:
    return Relation(
        id=rid,
        subject_id=subject,
        predicate="related_to",
        object_id=obj,
        chunk_id="c",
        extractor="rules",
        extractor_version="v",
        confidence=confidence,
    )


def test_shortest_paths_prefer_confident_edges_and_ignore_direction() -> None:
    relations = [
        _relation("r1", "a", "b", 0.5),
        _relation("r2", "c", "a", 0.9),  # pointing at the seed still connects it
        _relation("r3", "b", "d", 0.6),
        _relation("r4", "c", "d", 0.6),
    ]

    paths = _shortest_paths(["a"], relations)

    assert [r.id for r in paths["d"]] == ["r2", "r4"]  # via the more confident first hop
    assert [r.id for r in paths["b"]] == ["r1"]
    assert "a" not in paths


def test_related_entities_carry_paths_matching_their_hops(research: ResearchApp) -> None:
    packet = research.research.find_related(ResearchQuery(query="PEAD", hops=2))

    graph_entities = [entity for entity in packet.related if entity.hops > 0]
    assert graph_entities
    assert all(len(entity.path) == entity.hops for entity in graph_entities)


def test_equally_short_paths_prefer_confidence_regardless_of_seed_order() -> None:
    relations = [
        _relation("r1", "a", "d", 0.4),  # seed a reaches d weakly
        _relation("r2", "b", "d", 0.9),  # seed b reaches d strongly
    ]

    for seeds in (["a", "b"], ["b", "a"]):
        assert [r.id for r in _shortest_paths(seeds, relations)["d"]] == ["r2"]


def test_equally_short_two_hop_paths_prefer_the_stronger_branch() -> None:
    relations = [
        _relation("a1", "s", "m1", 0.9),
        _relation("a2", "m1", "t", 0.4),  # total 1.3
        _relation("b1", "s", "m2", 0.6),
        _relation("b2", "m2", "t", 0.9),  # total 1.5
    ]

    assert [r.id for r in _shortest_paths(["s"], relations)["t"]] == ["b1", "b2"]


def test_every_path_edge_is_cited_even_with_one_passage(research: ResearchApp) -> None:
    packet = research.research.find_related(ResearchQuery(query="PEAD", hops=2, k=1))

    assert len(packet.passages) == 1
    cited = {item.statement for item in packet.evidence if item.sources}
    for entity in packet.related:
        for link in entity.path:
            assert any(link.predicate.replace("_", " ") in statement for statement in cited)
    assert all(item.sources for item in packet.evidence)


def test_excluding_the_vector_channel_skips_query_embedding(corpus_dir: Path) -> None:
    class ExplodingEmbedder:
        model_id = "boom"
        dimensions = 8
        requires_network = True

        def embed(self, texts: object, **options: object) -> list[list[float]]:
            raise AssertionError("must not embed when the vector channel is off")

    with open_research(":memory:") as research:
        research.ingest.ingest(corpus_dir)
        service = ResearchService(
            research.store,
            research.vocabulary,
            embedder=ExplodingEmbedder(),  # type: ignore[arg-type]
            vectors=research.store,
            channels=ALL_CHANNELS - {"vector"},
        )
        packet = service.find_related(ResearchQuery(query="carry"))

    assert packet.passages


def test_graph_edges_require_positive_support_and_hops() -> None:
    relation = _relation("r", "a", "b", 0.5)

    with pytest.raises(ValidationError):
        GraphEdge(relation=relation, hops=1, support=0)
    with pytest.raises(ValidationError):
        GraphEdge(relation=relation, hops=0)
