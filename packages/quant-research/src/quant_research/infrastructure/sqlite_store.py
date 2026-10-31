"""SQLite knowledge store and vector index: standard library only.

Keyword search uses FTS5, graph expansion a recursive CTE, and vector search brute-force cosine
similarity over float32 blobs. That is exact and fine for MVP-sized corpora (thousands of
chunks); pgvector replaces it at scale behind the same `VectorIndex` port.

The schema is the relational design shared with the planned PostgreSQL adapter (decision 0004).
Use ``":memory:"`` for tests.

The executor runs tools on worker threads (and a timed-out call may still be running), so one
connection is shared across threads and every public method holds a re-entrant lock.
"""

from __future__ import annotations

import functools
import json
import math
import re
import sqlite3
import threading
from array import array
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from typing import Concatenate, ParamSpec, Self, TypeVar

from quant_research.domain.models import (
    Chunk,
    CorpusSnapshot,
    Document,
    Entity,
    Mention,
    Relation,
)
from quant_research.domain.ports import GraphEdge, KeywordHit, VectorHit

SCHEMA_VERSION = 3

# Additive column changes per schema version: applied in order, each only if missing.
_MIGRATIONS: dict[int, list[tuple[str, str, str]]] = {
    1: [("chunks", "page", "INTEGER")],
    2: [("documents", "pipeline", "TEXT NOT NULL DEFAULT ''")],
}


class StoreError(RuntimeError):
    """Raised when a store cannot be used as requested; the message says what to do."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, uri TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL, authors TEXT NOT NULL, published TEXT, license_note TEXT,
    loader_version TEXT NOT NULL, pipeline TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL, text TEXT NOT NULL, line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL, section TEXT, page INTEGER
);
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL, aliases TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mentions (
    entity_id TEXT NOT NULL, chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    start INTEGER NOT NULL, end INTEGER NOT NULL, extractor TEXT NOT NULL,
    confidence REAL NOT NULL, PRIMARY KEY (entity_id, chunk_id, start)
);
CREATE TABLE IF NOT EXISTS relations (
    id TEXT PRIMARY KEY, subject_id TEXT NOT NULL, predicate TEXT NOT NULL,
    object_id TEXT NOT NULL, chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    extractor TEXT NOT NULL, extractor_version TEXT NOT NULL, confidence REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS relations_subject ON relations(subject_id);
CREATE INDEX IF NOT EXISTS relations_object ON relations(object_id);
CREATE INDEX IF NOT EXISTS mentions_chunk ON mentions(chunk_id);
CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE, model TEXT NOT NULL,
    dimensions INTEGER NOT NULL, vector BLOB NOT NULL, PRIMARY KEY (chunk_id, model)
);
CREATE TABLE IF NOT EXISTS corpus_snapshots (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL, document_count INTEGER NOT NULL,
    vocabulary_version TEXT NOT NULL, extractor_versions TEXT NOT NULL
);
"""

# Expansion runs as two queries so the recursive walk is evaluated once (an inlined CTE would
# be re-run for every relation it is joined with). First: distances of reachable entities,
# walking entities rather than relations, so a fact stated in many chunks is one step.
_DISTANCES = """
WITH RECURSIVE reach(entity_id, hops) AS (
    SELECT value, 0 FROM json_each(:seeds)
    UNION
    SELECT r.object_id, w.hops + 1 FROM reach AS w
    JOIN relations AS r ON r.subject_id = w.entity_id
    WHERE w.hops < :hops AND r.confidence >= :min_confidence
    UNION
    SELECT r.subject_id, w.hops + 1 FROM reach AS w
    JOIN relations AS r ON r.object_id = w.entity_id
    WHERE w.hops < :hops AND r.confidence >= :min_confidence
)
SELECT entity_id, MIN(hops) AS hops FROM reach GROUP BY entity_id
"""
# Second: edges between reached entities that lie within the hop limit, keeping at most
# :per_triple citing edges per (subject, predicate, object) together with its support count.
_EDGES = """
WITH distance(entity_id, hops) AS (
    SELECT key, value FROM json_each(:distances)
),
edges AS (
    SELECT r.*, MIN(s.hops, o.hops) + 1 AS hops,
           COUNT(*) OVER triple AS support,
           ROW_NUMBER() OVER (triple ORDER BY r.confidence DESC, r.id) AS strength
    FROM distance AS s
    JOIN relations AS r ON r.subject_id = s.entity_id
    JOIN distance AS o ON o.entity_id = r.object_id
    WHERE r.confidence >= :min_confidence AND MIN(s.hops, o.hops) < :hops
    WINDOW triple AS (PARTITION BY r.subject_id, r.predicate, r.object_id)
)
SELECT * FROM edges WHERE strength <= :per_triple
ORDER BY hops, confidence DESC, id
"""
EDGES_PER_TRIPLE = 3

_TOKEN = re.compile(r"[A-Za-z0-9]+")

P = ParamSpec("P")
R = TypeVar("R")


def _locked(
    method: Callable[Concatenate[SqliteKnowledgeStore, P], R],
) -> Callable[Concatenate[SqliteKnowledgeStore, P], R]:
    @functools.wraps(method)
    def wrapper(self: SqliteKnowledgeStore, /, *args: P.args, **kwargs: P.kwargs) -> R:
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class SqliteKnowledgeStore:
    """A `KnowledgeStore` in one SQLite file (or in memory).

    ``read_only=True`` opens an existing file with SQLite's ``mode=ro``: nothing is created,
    migrated, or written (serving and searching use it, so a read-only mount works), and a
    store at another schema version is refused with the command that upgrades it.
    """

    def __init__(self, path: str | Path = ":memory:", *, read_only: bool = False) -> None:
        self.read_only = read_only
        self._lock = threading.RLock()
        if read_only:
            self._db = _open_read_only(path)
            self.has_fts = _has_table(self._db, "chunks_fts")
            return
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        _migrate(self._db)
        self._db.executescript(_SCHEMA)
        self.has_fts = _create_fts(self._db)
        self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _writable(self) -> None:
        if self.read_only:
            raise StoreError("This store is open read-only; open it for ingestion to change it.")

    @contextmanager
    def reading(self) -> Iterator[None]:
        """Hold one read transaction so every read in the block sees the same store state."""
        with self._lock:
            if self._db.in_transaction:
                yield
                return
            self._db.execute("BEGIN")
            try:
                yield
            finally:
                self._db.execute("COMMIT")

    @_locked
    def close(self) -> None:
        """Close the database connection."""
        self._db.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    @_locked
    def has_document(self, sha256: str, pipeline: str | None = None) -> bool:
        """Return whether this content is stored (and, with ``pipeline``, by that pipeline)."""
        if pipeline is None:
            row = self._db.execute("SELECT 1 FROM documents WHERE sha256 = ?", (sha256,))
        else:
            row = self._db.execute(
                "SELECT 1 FROM documents WHERE sha256 = ? AND pipeline = ?", (sha256, pipeline)
            )
        return row.fetchone() is not None

    @_locked
    def replace_document(
        self,
        document: Document,
        chunks: Sequence[Chunk],
        mentions: Sequence[Mention],
        relations: Sequence[Relation],
    ) -> None:
        """Store a document and its records atomically, replacing any document at its URI."""
        self._writable()
        with self._db:
            stale = [
                row["id"]
                for row in self._db.execute(
                    "SELECT id FROM documents WHERE uri = ? OR sha256 = ?",
                    (document.uri, document.sha256),
                )
            ]
            for document_id in stale:
                if self.has_fts:
                    self._db.execute(
                        "DELETE FROM chunks_fts WHERE chunk_id IN "
                        "(SELECT id FROM chunks WHERE document_id = ?)",
                        (document_id,),
                    )
                self._db.execute("DELETE FROM documents WHERE id = ?", (document_id,))
            self._db.execute(
                "INSERT INTO documents (id, sha256, uri, title, authors, published, "
                "license_note, loader_version, pipeline) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document.id,
                    document.sha256,
                    document.uri,
                    document.title,
                    json.dumps(document.authors),
                    document.published,
                    document.license_note,
                    document.loader_version,
                    document.pipeline,
                ),
            )
            self._db.executemany(
                "INSERT INTO chunks (id, document_id, ordinal, text, line_start, line_end, "
                "section, page) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        c.id,
                        c.document_id,
                        c.ordinal,
                        c.text,
                        c.line_start,
                        c.line_end,
                        c.section,
                        c.page,
                    )
                    for c in chunks
                ],
            )
            if self.has_fts:
                self._db.executemany(
                    "INSERT INTO chunks_fts (chunk_id, text) VALUES (?, ?)",
                    [(chunk.id, chunk.text) for chunk in chunks],
                )
            self._db.executemany(
                "INSERT OR IGNORE INTO mentions VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (m.entity_id, m.chunk_id, m.start, m.end, m.extractor, m.confidence)
                    for m in mentions
                ],
            )
            self._db.executemany(
                "INSERT OR IGNORE INTO relations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        r.id,
                        r.subject_id,
                        r.predicate,
                        r.object_id,
                        r.chunk_id,
                        r.extractor,
                        r.extractor_version,
                        r.confidence,
                    )
                    for r in relations
                ],
            )

    @_locked
    def upsert_entities(self, entities: Iterable[Entity]) -> None:
        """Insert or update canonical entities."""
        self._writable()
        with self._db:
            self._db.executemany(
                "INSERT INTO entities VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "type = excluded.type, name = excluded.name, aliases = excluded.aliases",
                [(e.id, e.type, e.name, json.dumps(e.aliases)) for e in entities],
            )

    @_locked
    def publish_snapshot(self, build: Callable[[list[str]], CorpusSnapshot]) -> CorpusSnapshot:
        """Build the snapshot from the current documents and publish it in one write lock.

        ``BEGIN IMMEDIATE`` takes the write lock before reading the hashes, so a concurrent
        ingestion either commits before (and is included) or waits until after.
        """
        self._writable()
        self._db.execute("BEGIN IMMEDIATE")
        try:
            snapshot = build(self.document_hashes())
            current = self.current_snapshot()
            if current is None or current.id != snapshot.id:
                self._db.execute(
                    "INSERT INTO corpus_snapshots (id, document_count, vocabulary_version, "
                    "extractor_versions) VALUES (?, ?, ?, ?)",
                    (
                        snapshot.id,
                        snapshot.document_count,
                        snapshot.vocabulary_version,
                        json.dumps(snapshot.extractor_versions, sort_keys=True),
                    ),
                )
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")
        return snapshot

    @_locked
    def current_snapshot(self) -> CorpusSnapshot | None:
        """Return the most recently recorded snapshot, if any."""
        row = self._db.execute(
            "SELECT * FROM corpus_snapshots ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return CorpusSnapshot(
            id=row["id"],
            document_count=row["document_count"],
            vocabulary_version=row["vocabulary_version"],
            extractor_versions=json.loads(row["extractor_versions"]),
        )

    @_locked
    def document_hashes(self) -> list[str]:
        """Return the content hashes of every stored document, sorted."""
        return [row[0] for row in self._db.execute("SELECT sha256 FROM documents ORDER BY sha256")]

    @_locked
    def get_document(self, document_id: str) -> Document | None:
        """Return a document by id."""
        row = self._db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        if row is None:
            return None
        return Document.model_validate(dict(row) | {"authors": json.loads(row["authors"])})

    @_locked
    def get_chunk(self, chunk_id: str) -> Chunk | None:
        """Return a chunk by id."""
        row = self._db.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        return None if row is None else Chunk.model_validate(dict(row))

    @_locked
    def get_entities(self, entity_ids: Iterable[str]) -> dict[str, Entity]:
        """Return the known entities among ``entity_ids``."""
        rows = self._db.execute(
            "SELECT * FROM entities WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps(sorted(set(entity_ids))),),
        )
        return {
            row["id"]: Entity.model_validate(dict(row) | {"aliases": json.loads(row["aliases"])})
            for row in rows
        }

    @_locked
    def mentioned_entities(self, chunk_ids: Iterable[str]) -> dict[str, list[str]]:
        """Return entity ids mentioned in each chunk, in order of first mention."""
        found: dict[str, list[str]] = {}
        rows = self._db.execute(
            "SELECT chunk_id, entity_id, MIN(start) AS first FROM mentions "
            "WHERE chunk_id IN (SELECT value FROM json_each(?)) "
            "GROUP BY chunk_id, entity_id ORDER BY chunk_id, first",
            (json.dumps(sorted(set(chunk_ids))),),
        )
        for row in rows:
            found.setdefault(row["chunk_id"], []).append(row["entity_id"])
        return found

    @_locked
    def chunks_mentioning(self, entity_ids: Iterable[str], limit: int) -> list[str]:
        """Return chunk ids ranked by distinct entities mentioned, then by mention count."""
        rows = self._db.execute(
            "SELECT chunk_id FROM mentions WHERE entity_id IN (SELECT value FROM json_each(?)) "
            "GROUP BY chunk_id ORDER BY COUNT(DISTINCT entity_id) DESC, COUNT(*) DESC, chunk_id "
            "LIMIT ?",
            (json.dumps(sorted(set(entity_ids))), limit),
        )
        return [row["chunk_id"] for row in rows]

    @_locked
    def keyword_search(self, query: str, limit: int) -> list[KeywordHit]:
        """Return chunks ranked by BM25 (FTS5), or by term overlap when FTS5 is unavailable."""
        tokens = sorted({token.lower() for token in _TOKEN.findall(query)})
        if not tokens:
            return []
        if self.has_fts:
            match = " OR ".join(f'"{token}"' for token in tokens)
            rows = self._db.execute(
                "SELECT chunk_id, -bm25(chunks_fts) AS score FROM chunks_fts "
                "WHERE chunks_fts MATCH ? ORDER BY score DESC, chunk_id LIMIT ?",
                (match, limit),
            )
            return [KeywordHit(chunk_id=row["chunk_id"], score=row["score"]) for row in rows]
        scored = []
        for row in self._db.execute("SELECT id, text FROM chunks"):
            words = [word.lower() for word in _TOKEN.findall(row["text"])]
            score = sum(words.count(token) for token in tokens) / (1 + len(words)) ** 0.5
            if score > 0:
                scored.append(KeywordHit(chunk_id=row["id"], score=score))
        scored.sort(key=lambda hit: (-hit.score, hit.chunk_id))
        return scored[:limit]

    @_locked
    def all_relations(self) -> list[Relation]:
        """Return every stored relation, by id (for evaluation and export)."""
        rows = self._db.execute("SELECT * FROM relations ORDER BY id")
        return [
            Relation.model_validate({key: row[key] for key in Relation.model_fields})
            for row in rows
        ]

    @_locked
    def upsert_embeddings(self, model_id: str, vectors: Sequence[tuple[str, list[float]]]) -> None:
        """Store ``(chunk_id, vector)`` pairs for ``model_id``, replacing existing ones."""
        self._writable()
        with self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO embeddings VALUES (?, ?, ?, ?)",
                [(chunk_id, model_id, len(vector), _pack(vector)) for chunk_id, vector in vectors],
            )

    @_locked
    def has_embeddings(self, model_id: str) -> bool:
        """Return whether any chunk has an embedding for ``model_id``."""
        row = self._db.execute("SELECT 1 FROM embeddings WHERE model = ? LIMIT 1", (model_id,))
        return row.fetchone() is not None

    @_locked
    def chunks_without_embeddings(self, model_id: str) -> list[tuple[str, str]]:
        """Return ``(chunk_id, text)`` for chunks with no ``model_id`` embedding, by id."""
        rows = self._db.execute(
            "SELECT c.id, c.text FROM chunks AS c LEFT JOIN embeddings AS e "
            "ON e.chunk_id = c.id AND e.model = ? WHERE e.chunk_id IS NULL ORDER BY c.id",
            (model_id,),
        )
        return [(row["id"], row["text"]) for row in rows]

    @_locked
    def vector_search(self, model_id: str, vector: list[float], limit: int) -> list[VectorHit]:
        """Return chunks by cosine similarity to ``vector`` (exact, brute force), best first."""
        norm = math.sqrt(sum(value * value for value in vector))
        if not norm:
            return []
        hits: list[VectorHit] = []
        rows = self._db.execute(
            "SELECT chunk_id, vector FROM embeddings WHERE model = ? AND dimensions = ?",
            (model_id, len(vector)),
        )
        for row in rows:
            stored = _unpack(row["vector"])
            stored_norm = math.sqrt(sum(value * value for value in stored))
            if stored_norm:
                dot = sum(a * b for a, b in zip(vector, stored, strict=True))
                hits.append(VectorHit(chunk_id=row["chunk_id"], score=dot / (norm * stored_norm)))
        hits.sort(key=lambda hit: (-hit.score, hit.chunk_id))
        return hits[:limit]

    @_locked
    def expand(
        self, entity_ids: Iterable[str], *, hops: int, min_confidence: float
    ) -> list[GraphEdge]:
        """Return relations reachable from ``entity_ids`` within ``hops`` steps, either way."""
        seeds = sorted(set(entity_ids))
        if not seeds or hops < 1:
            return []
        limits = {"hops": hops, "min_confidence": min_confidence}
        distances = {
            row["entity_id"]: row["hops"]
            for row in self._db.execute(_DISTANCES, {"seeds": json.dumps(seeds), **limits})
        }
        rows = self._db.execute(
            _EDGES,
            {"distances": json.dumps(distances), "per_triple": EDGES_PER_TRIPLE, **limits},
        )
        return [
            GraphEdge(
                relation=Relation.model_validate({key: row[key] for key in Relation.model_fields}),
                hops=row["hops"],
                support=row["support"],
            )
            for row in rows
        ]


def _migrate(db: sqlite3.Connection) -> None:
    """Upgrade an older store to `SCHEMA_VERSION` in place, atomically and idempotently.

    All column changes and the version bump commit together, and each column is added only if
    it is missing, so an interrupted upgrade can simply be run again. Documents upgraded from
    version 2 have no recorded pipeline, so the next ingestion processes them again.
    """
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if not 0 < version < SCHEMA_VERSION:
        return
    db.execute("BEGIN IMMEDIATE")
    try:
        for step in range(version, SCHEMA_VERSION):
            for table, column, declaration in _MIGRATIONS[step]:
                if not _has_table(db, table):
                    continue
                columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
                if column not in columns:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    db.execute("COMMIT")


def _open_read_only(path: str | Path) -> sqlite3.Connection:
    if str(path) == ":memory:":
        raise StoreError("A read-only store needs a file; ingest into one first.")
    location = Path(path)
    if not location.is_file():
        raise StoreError(f"No knowledge store at {location}; run quant-research ingest.")
    db = sqlite3.connect(
        f"{location.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False
    )
    db.row_factory = sqlite3.Row
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        db.close()
        raise StoreError(
            f"The store at {location} has schema version {version}, and this version reads "
            f"{SCHEMA_VERSION}. Run quant-research ingest on it once to upgrade it."
        )
    return db


def _has_table(db: sqlite3.Connection, name: str) -> bool:
    row = db.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone()
    return row is not None


def _pack(vector: list[float]) -> bytes:
    return array("f", vector).tobytes()


def _unpack(blob: bytes) -> array[float]:
    values = array("f")
    values.frombytes(blob)
    return values


def _create_fts(db: sqlite3.Connection) -> bool:
    try:
        db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(chunk_id UNINDEXED, text)"
        )
    except sqlite3.OperationalError:
        return False
    return True
