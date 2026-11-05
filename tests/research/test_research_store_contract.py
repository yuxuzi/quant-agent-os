"""The shared store contract (B-14): SQLite and PostgreSQL behave alike and answer alike.

Every test runs against each store. The PostgreSQL runs need a disposable database with
pgvector, named by ``QUANT_RESEARCH_TEST_POSTGRES_URL``. CI provides one and sets
``QUANT_RESEARCH_REQUIRE_POSTGRES``, so the runs cannot be skipped there. Each test gets its own
schema and drops it afterwards. Answers are compared with an in-memory SQLite reference built
from the same corpus, down to BM25 scores.
"""

import os
import shutil
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from quant_research import ResearchApp, ResearchQuery, open_research
from quant_research.application.strategy import StrategyRequest, StrategyService
from quant_research.infrastructure.postgres_store import (
    PostgresKnowledgeStore,
    is_postgres_url,
    redact_url,
)
from quant_research.infrastructure.sqlite_store import StoreError

POSTGRES_URL = os.environ.get("QUANT_RESEARCH_TEST_POSTGRES_URL")
QUERIES = [
    "What is related to short-term reversal?",
    "momentum crashes after market rebounds",
    "bid ask bounce and liquidity provision",
    "post-earnings announcement drift",
    "protein folding prediction accuracy",
]
STRATEGIES = ["trend following", "short-term reversal", "low volatility", "carry"]


class Backend:
    """Where one test's store lives, and how to open it."""

    def __init__(self, kind: str, location: str, schema: str) -> None:
        self.kind, self.location, self.schema = kind, location, schema

    def open(self, **options: Any) -> ResearchApp:
        return open_research(self.location, schema=self.schema, **options)


@pytest.fixture(params=["sqlite", "postgres"])
def backend(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Backend]:
    if request.param == "sqlite":
        yield Backend("sqlite", str(tmp_path / "kb.db"), "unused")
        return
    if not POSTGRES_URL:
        if os.environ.get("QUANT_RESEARCH_REQUIRE_POSTGRES"):  # CI must not skip silently
            pytest.fail("QUANT_RESEARCH_REQUIRE_POSTGRES is set but no PostgreSQL URL is given")
        pytest.skip("set QUANT_RESEARCH_TEST_POSTGRES_URL to run the PostgreSQL store tests")
    import psycopg
    from psycopg import sql

    schema = f"qr_test_{uuid.uuid4().hex[:12]}"
    yield Backend("postgres", POSTGRES_URL, schema)
    with psycopg.connect(POSTGRES_URL, autocommit=True) as db:
        db.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture(scope="module")
def reference(corpus_dir: Path) -> Iterator[ResearchApp]:
    """The answers every store must give: in-memory SQLite, from the same corpus."""
    app = open_research(":memory:")
    app.ingest.ingest(corpus_dir)
    yield app
    app.close()


@pytest.fixture(scope="module")
def vector_reference(corpus_dir: Path) -> Iterator[ResearchApp]:
    """The reference with the offline vector channel (the embedder is part of the snapshot)."""
    app = open_research(":memory:", vector=True)
    app.ingest.ingest(corpus_dir)
    yield app
    app.close()


@pytest.fixture(scope="module")
def corpus_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from pdf_fixtures import build_corpus

    return build_corpus(tmp_path_factory.mktemp("contract-corpus"))


def test_ingestion_is_idempotent_and_gives_the_reference_snapshot(
    backend: Backend, corpus_dir: Path, reference: ResearchApp
) -> None:
    with backend.open() as app:
        first = app.ingest.ingest(corpus_dir)
        again = app.ingest.ingest(corpus_dir)

        assert first.snapshot == again.snapshot == reference.store.current_snapshot()
        assert again.added == [] and len(again.unchanged) == len(first.added)
        assert app.store.document_hashes() == reference.store.document_hashes()
        for relation in app.store.all_relations():
            chunk = app.store.get_chunk(relation.chunk_id)
            assert chunk is not None and chunk.line_start <= chunk.line_end
        assert app.store.all_relations() == reference.store.all_relations()


def test_every_retrieval_answer_equals_the_reference(
    backend: Backend, corpus_dir: Path, vector_reference: ResearchApp
) -> None:
    reference = vector_reference
    with backend.open(vector=True) as app:
        app.ingest.ingest(corpus_dir)
        store, expected = app.store, reference.store

        for query in QUERIES:
            hits, wanted = store.keyword_search(query, 12), expected.keyword_search(query, 12)
            assert [(h.chunk_id, h.matched) for h in hits] == [
                (h.chunk_id, h.matched) for h in wanted
            ]
            assert [h.score for h in hits] == pytest.approx([h.score for h in wanted], rel=1e-9)
            assert store.term_frequencies([query]) == expected.term_frequencies([query])
            for hops in (1, 2):
                request = ResearchQuery(query=query, hops=hops)
                assert app.research.find_related(request) == reference.research.find_related(
                    request
                )
        entities = [entity.id for entity in app.vocabulary.entities]
        for hops, confidence in ((1, 0.5), (2, 0.0)):
            assert store.expand(entities, hops=hops, min_confidence=confidence) == expected.expand(
                entities, hops=hops, min_confidence=confidence
            )
        chunks = [hit.chunk_id for hit in expected.keyword_search("momentum reversal", 25)]
        assert store.mentioned_entities(chunks) == expected.mentioned_entities(chunks)
        assert store.chunks_mentioning(entities, 25) == expected.chunks_mentioning(entities, 25)
        assert store.get_entities(entities) == expected.get_entities(entities)


def test_strategy_specs_equal_the_reference(
    backend: Backend, corpus_dir: Path, reference: ResearchApp
) -> None:
    with backend.open() as app:
        app.ingest.ingest(corpus_dir)
        mine = StrategyService(app.store, app.research.matcher)
        theirs = StrategyService(reference.store, reference.research.matcher)

        for focus in STRATEGIES:
            request = StrategyRequest(focus=focus)
            assert mine.generate(request) == theirs.generate(request)


def test_vectors_round_trip_and_rank_like_the_reference(
    backend: Backend, corpus_dir: Path, vector_reference: ResearchApp
) -> None:
    reference = vector_reference
    with backend.open(vector=True) as app:
        result = app.ingest.ingest(corpus_dir)
        assert app.embedder is not None and result.embedded == result.chunks
        model = app.embedder.model_id
        assert app.store.has_embeddings(model) and not app.store.has_embeddings("other")
        assert app.store.chunks_without_embeddings(model) == []
        (query,) = app.embedder.embed(["momentum crash"], kind="query", deadline=None)

        hits = app.store.vector_search(model, query, 10)
        wanted = reference.store.vector_search(model, query, 10)

        assert {h.chunk_id for h in hits} == {h.chunk_id for h in wanted}
        assert sorted(h.score for h in hits) == pytest.approx(
            sorted(h.score for h in wanted), abs=1e-5
        )
        assert app.store.vector_search(model, [0.0] * len(query), 10) == []


def test_a_changed_file_replaces_its_records_and_the_snapshot(
    backend: Backend, tmp_path: Path, corpus_dir: Path
) -> None:
    corpus = tmp_path / "corpus"
    shutil.copytree(corpus_dir, corpus)
    with backend.open() as app:
        before = app.ingest.ingest(corpus)
        note = corpus / "momentum-crashes.md"
        note.write_text(note.read_text("utf-8") + "\nCarry is tested on FX.\n", "utf-8")
        after = app.ingest.ingest(corpus)

        assert after.added == ["momentum-crashes.md"]
        assert after.snapshot.id != before.snapshot.id
        assert app.store.current_snapshot() == after.snapshot
        assert len(app.store.document_hashes()) == len(before.added)


def test_has_document_respects_the_pipeline(backend: Backend, corpus_dir: Path) -> None:
    with backend.open() as app:
        app.ingest.ingest(corpus_dir)
        stored = app.store.document_hashes()[0]

        assert app.store.has_document(stored)
        assert not app.store.has_document(stored, pipeline="another-pipeline")
        assert not app.store.has_document("0" * 64)


def test_read_only_stores_refuse_writes_and_missing_stores(
    backend: Backend, corpus_dir: Path
) -> None:
    with pytest.raises(StoreError, match="run quant-research ingest"):
        backend.open(read_only=True)
    with backend.open() as app:
        app.ingest.ingest(corpus_dir)
        snapshot = app.store.current_snapshot()

    with backend.open(read_only=True) as reader:
        packet = reader.research.find_related(ResearchQuery(query="short-term reversal"))
        with pytest.raises(StoreError, match="read-only"):
            reader.ingest.ingest(corpus_dir)
        assert reader.store.current_snapshot() == snapshot
    assert packet.passages


def test_a_reading_block_sees_one_version_while_ingestion_commits(
    backend: Backend, tmp_path: Path, corpus_dir: Path
) -> None:
    if backend.kind != "postgres":
        pytest.skip("SQLite locks writers out of a read instead (test_research_store_safety)")
    corpus = tmp_path / "corpus"
    shutil.copytree(corpus_dir, corpus)
    with backend.open() as writer:
        first = writer.ingest.ingest(corpus).snapshot
        with backend.open(read_only=True) as reader:
            with reader.store.reading():
                assert reader.store.current_snapshot() == first  # the snapshot starts here
                (corpus / "extra.md").write_text("# Extra\n\nCarry is tested on FX.\n", "utf-8")
                second = writer.ingest.ingest(corpus).snapshot
                assert reader.store.current_snapshot() == first
                assert len(reader.store.document_hashes()) == first.document_count
            assert reader.store.current_snapshot() == second != first


def test_postgres_urls_are_recognised_and_passwords_never_shown() -> None:
    assert is_postgres_url("postgresql://u@h/db") and is_postgres_url("postgres://h/db")
    assert not is_postgres_url("kb.db") and not is_postgres_url(Path("postgresql:/x"))
    assert redact_url("postgresql://qa:s3cret@db.example:5432/kb") == (
        "postgresql://qa:***@db.example:5432/kb"
    )
    assert redact_url("postgresql://qa@db/kb") == "postgresql://qa@db/kb"


def test_connection_errors_name_the_server_without_the_password() -> None:
    with pytest.raises(StoreError) as caught:
        PostgresKnowledgeStore("postgresql://qa:s3cret@127.0.0.1:1/kb?connect_timeout=2")

    assert "s3cret" not in str(caught.value) and "qa:***@127.0.0.1:1" in str(caught.value)


def test_a_missing_extra_gives_the_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "psycopg", None)

    with pytest.raises(StoreError, match="quant-agent-os\\[postgres\\]"):
        PostgresKnowledgeStore("postgresql://u@h/db")


@pytest.mark.skipif(not POSTGRES_URL, reason="needs QUANT_RESEARCH_TEST_POSTGRES_URL")
def test_a_newer_postgres_schema_version_is_refused(backend: Backend) -> None:
    if backend.kind != "postgres":
        pytest.skip("PostgreSQL only")
    backend.open().close()
    import psycopg
    from psycopg import sql

    assert POSTGRES_URL is not None
    with psycopg.connect(POSTGRES_URL, autocommit=True) as db:
        db.execute(
            sql.SQL("UPDATE {}.meta SET value = '99' WHERE key = 'schema_version'").format(
                sql.Identifier(backend.schema)
            )
        )

    with pytest.raises(StoreError, match="schema version 99"):
        backend.open()
    with pytest.raises(StoreError, match="schema version 99"):
        backend.open(read_only=True)


def test_the_cli_takes_a_postgres_url_and_never_prints_its_password() -> None:
    from typer.testing import CliRunner

    from quant_research.cli import app as cli

    url = "postgresql://qa:s3cret@127.0.0.1:1/kb?connect_timeout=2"
    result = CliRunner().invoke(cli, ["search", "carry", "--store", url])

    assert result.exit_code != 0
    assert "s3cret" not in result.output and "qa:***@127.0.0.1:1" in result.output
