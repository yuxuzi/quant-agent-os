"""Store safety under serving and concurrency (review fixes on PR #4)."""

import sqlite3
import stat
from pathlib import Path

import pytest
from conftest import CORPUS
from typer.testing import CliRunner

from quant_research import ResearchQuery, open_research
from quant_research.cli import app as cli
from quant_research.infrastructure.sqlite_store import (
    SCHEMA_VERSION,
    SqliteKnowledgeStore,
    StoreError,
)


@pytest.fixture
def built(tmp_path: Path) -> Path:
    path = tmp_path / "kb.db"
    with open_research(path) as research:
        research.ingest.ingest(CORPUS)
    return path


def test_read_only_stores_are_never_modified(built: Path) -> None:
    before = built.read_bytes()
    built.chmod(stat.S_IRUSR)  # a read-only mount, as far as SQLite can tell
    try:
        with open_research(built, read_only=True) as research:
            packet = research.research.find_related(ResearchQuery(query="PEAD"))
            with pytest.raises(StoreError, match="read-only"):
                research.ingest.ingest(CORPUS)
    finally:
        built.chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert packet.passages
    assert built.read_bytes() == before


def test_read_only_refuses_missing_and_outdated_stores(tmp_path: Path, built: Path) -> None:
    with pytest.raises(StoreError, match="run quant-research ingest"):
        SqliteKnowledgeStore(tmp_path / "absent.db", read_only=True)
    with pytest.raises(StoreError, match="needs a file"):
        SqliteKnowledgeStore(":memory:", read_only=True)
    db = sqlite3.connect(built)
    db.execute("PRAGMA user_version = 1")
    db.commit()
    db.close()

    with pytest.raises(StoreError, match=f"version 1, and this version reads {SCHEMA_VERSION}"):
        SqliteKnowledgeStore(built, read_only=True)


def test_search_and_serve_do_not_create_a_store(tmp_path: Path) -> None:
    runner = CliRunner()
    missing = tmp_path / "absent.db"

    searched = runner.invoke(cli, ["search", "carry", "--store", str(missing)])
    served = runner.invoke(cli, ["serve-mcp", "--store", str(missing)])

    assert searched.exit_code == served.exit_code == 1
    assert not missing.exists()


def test_an_interrupted_v1_migration_can_be_rerun(tmp_path: Path) -> None:
    path = tmp_path / "half.db"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE chunks (id TEXT PRIMARY KEY, document_id TEXT NOT NULL, ordinal INTEGER "
        "NOT NULL, text TEXT NOT NULL, line_start INTEGER NOT NULL, line_end INTEGER NOT NULL, "
        "section TEXT, page INTEGER)"  # the column was added, the version bump was lost
    )
    db.execute("PRAGMA user_version = 1")
    db.commit()
    db.close()

    with SqliteKnowledgeStore(path) as store:
        version = store._db.execute("PRAGMA user_version").fetchone()[0]

    assert version == SCHEMA_VERSION


def test_reads_inside_one_answer_see_a_single_corpus_state(built: Path) -> None:
    with SqliteKnowledgeStore(built, read_only=True) as reader:
        writer = sqlite3.connect(built, timeout=0.05)
        with reader.reading():
            reader.keyword_search("carry", 3)
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                writer.execute("DELETE FROM corpus_snapshots")
                writer.commit()
        writer.execute("DELETE FROM corpus_snapshots")  # succeeds once the read ends
        writer.commit()
        writer.close()


def test_snapshots_are_built_and_published_atomically(built: Path) -> None:
    seen: list[int] = []
    with SqliteKnowledgeStore(built) as store:
        other = sqlite3.connect(built, timeout=0.05)

        def build(hashes: list[str]) -> object:
            seen.append(len(hashes))
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("DELETE FROM documents")  # a concurrent ingest must wait
            current = store.current_snapshot()
            assert current is not None
            return current

        store.publish_snapshot(build)  # type: ignore[arg-type]
        other.close()

    assert seen == [6]
