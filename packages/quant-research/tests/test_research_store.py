"""Unit tests for ingestion and the SQLite knowledge store."""

import shutil
from pathlib import Path

import pytest
from conftest import CORPUS
from typer.testing import CliRunner

from quant_research import ResearchApp, open_research
from quant_research.cli import app as cli
from quant_research.domain.extraction import EXTRACTOR_VERSION
from quant_research.infrastructure.sqlite_store import SqliteKnowledgeStore


def test_ingestion_reports_counts_and_skips_unsupported_files() -> None:
    with open_research(":memory:") as research:
        result = research.ingest.ingest(CORPUS)

    assert len(result.added) == 6
    assert result.skipped == ["chart.png"]
    assert result.relations > 0
    assert result.snapshot.document_count == 6
    assert result.snapshot.extractor_versions["extractor"] == EXTRACTOR_VERSION


def test_reingesting_is_idempotent_and_keeps_the_snapshot(research: ResearchApp) -> None:
    before = research.store.current_snapshot()

    again = research.ingest.ingest(CORPUS)

    assert again.added == []
    assert len(again.unchanged) == 6
    assert again.snapshot == before


def test_a_changed_file_replaces_its_records_and_the_snapshot(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    shutil.copytree(CORPUS, corpus)
    with open_research(tmp_path / "store.db") as research:
        first = research.ingest.ingest(corpus).snapshot
        (corpus / "notes" / "earnings.txt").write_text("Carry correlates with crowding.\n")

        result = research.ingest.ingest(corpus)

        assert result.added == ["notes/earnings.txt"]
        assert result.snapshot.id != first.id
        assert result.snapshot.document_count == 6
        assert research.store.keyword_search("analyst revisions", 5) == []
        assert research.store.keyword_search("crowding", 5)


def test_missing_corpus_raises_an_actionable_error(tmp_path: Path) -> None:
    with (
        open_research(":memory:") as research,
        pytest.raises(FileNotFoundError, match="absent"),
    ):
        research.ingest.ingest(tmp_path / "absent")


def test_chunk_locations_reproduce_the_source_lines(research: ResearchApp) -> None:
    for hit in research.store.keyword_search("reversal momentum value carry PEAD", 50):
        chunk = research.store.get_chunk(hit.chunk_id)
        document = research.store.get_document(chunk.document_id)  # type: ignore[union-attr]
        lines = (CORPUS / document.uri).read_text().splitlines()  # type: ignore[union-attr]
        source = "\n".join(lines[chunk.line_start - 1 : chunk.line_end]).strip()  # type: ignore[union-attr]

        assert source == chunk.text  # type: ignore[union-attr]


def test_front_matter_becomes_document_metadata(research: ResearchApp) -> None:
    hit = research.store.keyword_search("bounce", 1)[0]
    chunk = research.store.get_chunk(hit.chunk_id)
    document = research.store.get_document(chunk.document_id)  # type: ignore[union-attr]

    assert document is not None
    assert document.title == "Short-term reversal and market microstructure"
    assert document.authors == ["Synthetic Author A", "Synthetic Author B"]
    assert document.license_note == "CC0 synthetic fixture"


def test_expansion_respects_hops_and_confidence(research: ResearchApp) -> None:
    seed = ["signal:short-term-reversal"]

    one = research.store.expand(seed, hops=1, min_confidence=0.5)
    two = research.store.expand(seed, hops=2, min_confidence=0.5)
    strict = research.store.expand(seed, hops=1, min_confidence=0.79)

    assert one and {edge.hops for edge in one} == {1}
    assert {edge.relation.id for edge in one} < {edge.relation.id for edge in two}
    assert max(edge.hops for edge in two) == 2
    assert all(edge.relation.confidence >= 0.79 for edge in strict)
    assert research.store.expand(seed, hops=0, min_confidence=0) == []
    assert research.store.expand([], hops=2, min_confidence=0) == []


def test_keyword_search_without_fts5_still_ranks_matches() -> None:
    with open_research(":memory:") as research:
        store = SqliteKnowledgeStore(":memory:")
        store.has_fts = False
        research.ingest.store = store
        research.ingest.ingest(CORPUS)

        hits = store.keyword_search("bid-ask bounce", 3)

        assert hits
        assert "bounce" in store.get_chunk(hits[0].chunk_id).text  # type: ignore[union-attr]
        assert store.keyword_search("!!!", 3) == []
        store.close()


def test_cli_ingests_and_searches(tmp_path: Path) -> None:
    runner = CliRunner()
    store = str(tmp_path / "k.db")

    ingested = runner.invoke(cli, ["ingest", str(CORPUS), "--store", store, "--json"])
    searched = runner.invoke(cli, ["search", "short-term reversal", "--store", store])
    missing = runner.invoke(cli, ["ingest", str(tmp_path / "absent"), "--store", store])

    assert ingested.exit_code == 0, ingested.output
    assert '"snapshot"' in ingested.output
    assert searched.exit_code == 0, searched.output
    assert "Order-flow imbalance" in searched.output
    assert missing.exit_code == 1
