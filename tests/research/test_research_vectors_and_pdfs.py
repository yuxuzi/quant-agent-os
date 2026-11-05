"""The mock vector channel and PDF ingestion (plan steps R7-R9)."""

import math
import sqlite3
from pathlib import Path

import pytest
from pdf_fixtures import LOW_VOLATILITY_PAGES, write_pdf

from quant_research import ResearchApp, ResearchQuery, open_research
from quant_research.application.ingest import IngestService
from quant_research.domain.chunking import MAX_CHUNK_CHARS, PAGE_CHUNK_CHARS, split_page
from quant_research.domain.ports import SourceReadError
from quant_research.domain.vocabulary import EntityMatcher, load_vocabulary
from quant_research.infrastructure.embedding import HashingEmbedder
from quant_research.infrastructure.files import TextFileLoader
from quant_research.infrastructure.pdf import PdfLoader, UnavailableLoader
from quant_research.infrastructure.sqlite_store import SCHEMA_VERSION, SqliteKnowledgeStore

VOCABULARY = load_vocabulary()
EMBEDDER = HashingEmbedder(EntityMatcher(VOCABULARY))


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_hashing_embeddings_are_deterministic_unit_vectors() -> None:
    first, again, empty = EMBEDDER.embed(["Momentum crashes", "Momentum crashes", "the of"])

    assert first == again
    assert math.isclose(math.sqrt(sum(v * v for v in first)), 1.0)
    assert empty == [0.0] * EMBEDDER.dimensions
    assert EMBEDDER.model_id == "hashing-v1-256-entities"


def test_aliases_of_one_entity_embed_close_together() -> None:
    hml, book, carry = EMBEDDER.embed(["HML", "book-to-market", "carry"])

    assert _cosine(hml, book) > 0.8  # shared entity feature, different words
    assert _cosine(hml, carry) < 0.3


def test_embedder_rejects_tiny_dimensions() -> None:
    with pytest.raises(ValueError, match="8 dimensions"):
        HashingEmbedder(dimensions=4)


def test_services_need_both_an_embedder_and_an_index() -> None:
    with SqliteKnowledgeStore() as store, pytest.raises(ValueError, match="both"):
        IngestService(store, VOCABULARY, [TextFileLoader()], embedder=EMBEDDER)


def test_vector_ingestion_embeds_every_chunk_and_backfills(corpus_dir: Path) -> None:
    with open_research(":memory:") as plain:
        plain.ingest.ingest(corpus_dir)
        store = plain.store
        assert store.chunks_without_embeddings(EMBEDDER.model_id)

        service = IngestService(
            store, VOCABULARY, [TextFileLoader()], embedder=EMBEDDER, vectors=store
        )
        result = service.ingest(corpus_dir)

        assert result.embedded > 0
        assert store.chunks_without_embeddings(EMBEDDER.model_id) == []
        assert result.snapshot.extractor_versions["embedder"] == EMBEDDER.model_id


def test_vector_search_ranks_by_cosine_and_ignores_zero_queries(corpus_dir: Path) -> None:
    with open_research(":memory:", vector=True) as research:
        research.ingest.ingest(corpus_dir)
        (query,) = EMBEDDER.embed(["betting against beta"])

        hits = research.store.vector_search(EMBEDDER.model_id, query, 3)
        top = research.store.get_chunk(hits[0].chunk_id)

        assert [hit.score for hit in hits] == sorted((hit.score for hit in hits), reverse=True)
        assert top is not None and top.page == 1
        assert research.store.vector_search(EMBEDDER.model_id, [0.0] * 256, 3) == []
        assert research.store.vector_search("other-model", query, 3) == []


def test_vector_channel_contributes_passages(corpus_dir: Path) -> None:
    with open_research(":memory:", vector=True) as research:
        research.ingest.ingest(corpus_dir)
        packet = research.research.find_related(ResearchQuery(query="betting against beta"))

    assert any("vector" in passage.channels for passage in packet.passages)


def test_replacing_a_document_drops_its_embeddings(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("Carry correlates with crowding.\n")
    with open_research(tmp_path / "kb.db", vector=True) as research:
        research.ingest.ingest(tmp_path)
        note.write_text("Momentum predicts momentum crashes.\n")
        research.ingest.ingest(tmp_path)
        rows = research.store._db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]

    assert rows == 1


def test_version_one_stores_are_migrated_in_place(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE chunks (id TEXT PRIMARY KEY, document_id TEXT NOT NULL, ordinal INTEGER "
        "NOT NULL, text TEXT NOT NULL, line_start INTEGER NOT NULL, line_end INTEGER NOT NULL, "
        "section TEXT)"
    )
    db.execute("PRAGMA user_version = 1")
    db.commit()
    db.close()

    with SqliteKnowledgeStore(path) as store:
        columns = [row[1] for row in store._db.execute("PRAGMA table_info(chunks)")]
        version = store._db.execute("PRAGMA user_version").fetchone()[0]

    assert "page" in columns
    assert version == SCHEMA_VERSION


def test_pdf_pages_become_located_chunks(research: ResearchApp) -> None:
    packet = research.research.find_related(ResearchQuery(query="betting against beta"))
    pdf = [p for p in packet.passages if p.source.source_id == "papers/low-volatility.pdf"]

    assert pdf
    assert all(p.source.location and p.source.location.startswith("page ") for p in pdf)
    assert pdf[0].source.title == "Low-volatility anomaly in US equities"


def test_pdf_relations_are_extracted(research: ResearchApp) -> None:
    packet = research.research.find_related(
        ResearchQuery(query="Which signals work in futures markets?")
    )
    related = {entity.id for entity in packet.related}

    assert {"signal:carry", "signal:time-series-momentum"} <= related


def test_scanned_and_broken_pdfs_are_reported(corpus_dir: Path) -> None:
    with open_research(":memory:") as research:
        result = research.ingest.ingest(corpus_dir)

    assert "papers/scanned.pdf" in result.added
    assert "broken.pdf" in result.skipped
    assert any("OCR" in warning for warning in result.warnings)
    assert any("not a readable PDF" in warning for warning in result.warnings)


def test_pdf_loader_reads_metadata_and_pages(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "p.pdf", LOW_VOLATILITY_PAGES, title="T", author="A, B")

    source = PdfLoader().load(path, tmp_path)

    assert source.uri == "p.pdf"
    assert source.metadata == {"title": "T", "authors": "A, B"}
    assert source.pages is not None and len(source.pages) == 2
    assert source.pages[1].startswith("Results")


def test_unavailable_loader_explains_how_to_enable_pdfs(tmp_path: Path) -> None:
    loader = UnavailableLoader(frozenset({".pdf"}), "PDF support is not installed; install it.")

    with pytest.raises(SourceReadError, match=r"Skipped x\.pdf: PDF support"):
        loader.load(tmp_path / "x.pdf", tmp_path)


def test_page_chunks_close_at_sentence_ends_and_respect_the_limit() -> None:
    sentence = "word " * 30 + "end."
    page = "\n".join([sentence] * 8 + ["", "x" * (MAX_CHUNK_CHARS - 2), "tail"])

    passages = split_page(page)

    assert all(len(p.text) <= MAX_CHUNK_CHARS for p in passages)
    assert len(passages[0].text) >= PAGE_CHUNK_CHARS
    assert passages[0].text.endswith("end.")
    assert passages[0].line_start == 1
    assert passages[-1].text == "tail"
