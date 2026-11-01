"""Strategy specs from research evidence (B-17): known answers on the fixture corpus."""

import pytest

from quant_research import ResearchApp
from quant_research.application.strategy import (
    NoEvidenceError,
    SpecItem,
    StrategyRequest,
    StrategyService,
    StrategySpec,
    UnknownFocusError,
)


@pytest.fixture
def strategies(research: ResearchApp) -> StrategyService:
    return StrategyService(research.store, research.research.matcher)


SECTIONS = ("signals", "universe", "data", "methods", "metrics", "risks", "context")


def _all(spec: StrategySpec) -> list[tuple[str, SpecItem]]:
    return [(section, item) for section in SECTIONS for item in getattr(spec, section)]


def _items(spec: StrategySpec) -> list[tuple[str, str]]:
    return [(section, item.statement) for section, item in _all(spec)]


def test_a_strategy_spec_lists_the_signals_and_universe_its_sources_state(
    strategies: StrategyService,
) -> None:
    draft = strategies.generate(StrategyRequest(focus="trend following"))
    spec = draft.spec

    assert spec.name == "Trend following"
    assert spec.focus.id == "strategy:trend-following"
    assert _items(spec) == [
        ("signals", "Trend following uses Time-series momentum."),
        ("universe", "Trend following tested on Futures."),
    ]
    (uses,) = spec.signals
    assert uses.direction == "outgoing" and uses.entity.id == "signal:time-series-momentum"
    assert {source.source_id for source in uses.sources} == {
        "momentum-crashes.md",
        "papers/low-volatility.pdf",
    }
    assert draft.warnings == []


def test_contradictions_and_explanations_of_the_focus_are_risks(
    strategies: StrategyService,
) -> None:
    spec = strategies.generate(StrategyRequest(focus="signal:short-term-reversal")).spec

    assert [(item.statement, item.direction) for item in spec.risks] == [
        ("Short-term reversal explained by Bid-ask bounce.", "outgoing"),
        ("Momentum contradicts Short-term reversal.", "incoming"),
    ]
    assert ("signals", "Order-flow imbalance predicts Short-term reversal.") in _items(spec)
    assert not any("contradicts or explains away" in q for q in spec.open_questions)


def test_every_item_links_passages_that_exist(
    strategies: StrategyService, research: ResearchApp
) -> None:
    for focus in ("trend following", "short-term reversal", "low volatility", "carry"):
        draft = strategies.generate(StrategyRequest(focus=focus))
        items = [item for _, item in _all(draft.spec)]

        assert items, focus
        for item in items:
            assert len(item.sources) == len(item.chunk_ids) >= 1
            for chunk_id, source in zip(item.chunk_ids, item.sources, strict=True):
                assert research.research.get_passage(chunk_id).source == source
        assert all(evidence.kind == "retrieved" and evidence.sources for evidence in draft.evidence)
        assert len(draft.evidence) == len(items) + len(draft.spec.key_passages)


def test_what_the_corpus_leaves_open_becomes_questions_not_defaults(
    strategies: StrategyService,
) -> None:
    spec = strategies.generate(StrategyRequest(focus="trend following")).spec

    assert spec.data == spec.methods == spec.metrics == spec.risks == []
    assert len(spec.open_questions) == 5
    assert spec.open_questions[-1].startswith("Parameters (formation and holding periods")
    assert "absence of evidence" in spec.open_questions[-2]


def test_key_passages_mention_the_focus_and_the_spec_records_the_snapshot(
    strategies: StrategyService, research: ResearchApp
) -> None:
    draft = strategies.generate(StrategyRequest(focus="short-term reversal", max_passages=2))
    snapshot = research.store.current_snapshot()

    assert snapshot is not None
    assert len(draft.spec.key_passages) == 2
    assert all("reversal" in passage.text.lower() for passage in draft.spec.key_passages)
    assert draft.spec.corpus_snapshot == snapshot.id
    assert draft.data_versions == {"corpus_snapshot": snapshot.id}


def test_specs_are_deterministic(strategies: StrategyService) -> None:
    request = StrategyRequest(focus="low volatility")

    assert strategies.generate(request) == strategies.generate(request)


def test_weak_evidence_leaves_key_passages_and_a_warning(strategies: StrategyService) -> None:
    draft = strategies.generate(StrategyRequest(focus="short-term reversal", min_confidence=0.95))

    assert _items(draft.spec) == []
    assert draft.spec.key_passages
    assert "states no relation" in draft.warnings[0]


def test_several_named_signals_build_around_the_first_with_a_warning(
    strategies: StrategyService,
) -> None:
    draft = strategies.generate(StrategyRequest(focus="momentum versus short-term reversal"))

    assert draft.spec.focus.id == "factor:momentum"
    assert "also names Short-term reversal" in draft.warnings[0]


def test_unknown_and_unsupported_focuses_fail_with_actionable_messages(
    strategies: StrategyService,
) -> None:
    with pytest.raises(UnknownFocusError, match="find_related_signals"):
        strategies.generate(StrategyRequest(focus="protein folding"))
    with pytest.raises(UnknownFocusError, match="names no signal"):
        strategies.generate(StrategyRequest(focus="US equities"))
    with pytest.raises(NoEvidenceError, match="ingest sources about it first"):
        strategies.generate(StrategyRequest(focus="pairs trading"))
