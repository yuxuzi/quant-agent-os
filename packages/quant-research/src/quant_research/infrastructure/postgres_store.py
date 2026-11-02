"""PostgreSQL knowledge store and vector index (B-14, decision 0004), the ``postgres`` extra.

It implements the same `KnowledgeStore` and `VectorIndex` ports as `SqliteKnowledgeStore`, over
the same relational design, and returns the same answers for the same corpus:
- **Keyword search.** Terms are indexed by the FTS5-compatible tokenizer in
  `quant_research.infrastructure.terms` and scored with FTS5's BM25 in SQL, not with
  PostgreSQL's ``ts_rank``. That is what keeps the two stores' rankings equal.
- **Graph expansion.** The same two-query walk, written for PostgreSQL's recursive CTEs.
- **Ordering.** Identifiers use the ``C`` collation, so ties break by bytes, as in SQLite.
- **Vectors.** pgvector, when the database has the extension, with exact cosine search. Without
  the extension the store still works and reports that the vector channel is unavailable.

Tables live in their own schema (``quant_research`` by default), so the store can share a
database. Passwords never appear in messages: pass them the libpq way (``PGPASSWORD`` or a
``.pgpass`` file) rather than in the URL, and `redact_url` hides one that is.

Like the SQLite store, one connection is shared across threads under a re-entrant lock.
"""

from __future__ import annotations

import functools
import json
import math
import threading
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from types import TracebackType
from typing import TYPE_CHECKING, Any, Concatenate, ParamSpec, Self, TypeGuard, TypeVar
from urllib.parse import urlsplit, urlunsplit

from quant_research.domain.models import (
    Chunk,
    CorpusSnapshot,
    Document,
    Entity,
    Mention,
    Relation,
)
from quant_research.domain.ports import GraphEdge, KeywordHit, VectorHit
from quant_research.infrastructure.sqlite_store import EDGES_PER_TRIPLE, StoreError
from quant_research.infrastructure.terms import (
    BM25_B,
    BM25_K1,
    BM25_MIN_IDF,
    index_terms,
    matched,
    query_tokens,
)

if TYPE_CHECKING:
    import psycopg

SCHEMA_VERSION = 1
DEFAULT_SCHEMA = "quant_research"
URL_SCHEMES = ("postgresql://", "postgres://")
INSTALL_HINT = "install the PostgreSQL extra: uv add 'quant-agent-os-research[postgres]'"
NO_VECTOR = (
    "This PostgreSQL database has no pgvector extension, so the vector channel is unavailable. "
    "Ask an administrator to run CREATE EXTENSION vector, or ingest without an embedder."
)

_TABLES = """
CREATE TABLE IF NOT EXISTS meta (key TEXT COLLATE "C" PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT COLLATE "C" PRIMARY KEY, sha256 TEXT COLLATE "C" NOT NULL UNIQUE,
    uri TEXT COLLATE "C" NOT NULL UNIQUE, title TEXT NOT NULL, authors JSONB NOT NULL,
    published TEXT, license_note TEXT, loader_version TEXT NOT NULL,
    pipeline TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT COLLATE "C" PRIMARY KEY,
    document_id TEXT COLLATE "C" NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL, text TEXT NOT NULL, line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL, section TEXT, page INTEGER, length INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS chunk_terms (
    term TEXT COLLATE "C" NOT NULL,
    chunk_id TEXT COLLATE "C" NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    tf INTEGER NOT NULL, PRIMARY KEY (term, chunk_id)
);
CREATE INDEX IF NOT EXISTS chunk_terms_chunk ON chunk_terms(chunk_id);
CREATE TABLE IF NOT EXISTS entities (
    id TEXT COLLATE "C" PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL,
    aliases JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS mentions (
    entity_id TEXT COLLATE "C" NOT NULL,
    chunk_id TEXT COLLATE "C" NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    start INTEGER NOT NULL, "end" INTEGER NOT NULL, extractor TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL, PRIMARY KEY (entity_id, chunk_id, start)
);
CREATE INDEX IF NOT EXISTS mentions_chunk ON mentions(chunk_id);
CREATE TABLE IF NOT EXISTS relations (
    id TEXT COLLATE "C" PRIMARY KEY, subject_id TEXT COLLATE "C" NOT NULL,
    predicate TEXT COLLATE "C" NOT NULL, object_id TEXT COLLATE "C" NOT NULL,
    chunk_id TEXT COLLATE "C" NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    extractor TEXT NOT NULL, extractor_version TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS relations_subject ON relations(subject_id);
CREATE INDEX IF NOT EXISTS relations_object ON relations(object_id);
CREATE TABLE IF NOT EXISTS corpus_snapshots (
    seq BIGSERIAL PRIMARY KEY, id TEXT COLLATE "C" NOT NULL, document_count INTEGER NOT NULL,
    vocabulary_version TEXT NOT NULL, extractor_versions JSONB NOT NULL
);
"""
_EMBEDDINGS = """
CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id TEXT COLLATE "C" NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    model TEXT COLLATE "C" NOT NULL, dimensions INTEGER NOT NULL, embedding vector NOT NULL,
    PRIMARY KEY (chunk_id, model)
);
"""

# The same two-step walk as the SQLite store. PostgreSQL allows one recursive reference, so
# both directions of each relation come from one non-recursive edge list.
_DISTANCES = """
WITH RECURSIVE edges(a, b) AS (
    SELECT subject_id, object_id FROM relations WHERE confidence >= %(min_confidence)s
    UNION ALL
    SELECT object_id, subject_id FROM relations WHERE confidence >= %(min_confidence)s
),
reach(entity_id, hops) AS (
    SELECT seed COLLATE "C", 0 FROM unnest(%(seeds)s::text[]) AS seed
    UNION
    SELECT e.b, w.hops + 1 FROM reach AS w JOIN edges AS e ON e.a = w.entity_id
    WHERE w.hops < %(hops)s
)
SELECT entity_id, MIN(hops) AS hops FROM reach GROUP BY entity_id
"""
_EDGES = """
WITH distance(entity_id, hops) AS (
    SELECT id COLLATE "C", hops FROM unnest(%(ids)s::text[], %(distances)s::int[]) AS d(id, hops)
),
edges AS (
    SELECT r.*, LEAST(s.hops, o.hops) + 1 AS hops,
           COUNT(*) OVER triple AS support,
           ROW_NUMBER() OVER (triple ORDER BY r.confidence DESC, r.id) AS strength
    FROM distance AS s
    JOIN relations AS r ON r.subject_id = s.entity_id
    JOIN distance AS o ON o.entity_id = r.object_id
    WHERE r.confidence >= %(min_confidence)s AND LEAST(s.hops, o.hops) < %(hops)s
    WINDOW triple AS (PARTITION BY r.subject_id, r.predicate, r.object_id)
)
SELECT * FROM edges WHERE strength <= %(per_triple)s
ORDER BY hops, confidence DESC, id
"""
# FTS5's BM25 (k1 = 1.2, b = 0.75, negative IDF clamped to 1e-6), over the indexed terms.
_BM25 = f"""
WITH stats AS (
    SELECT COUNT(*)::float8 AS n, COALESCE(AVG(length), 0)::float8 AS avgdl FROM chunks
),
idf AS (
    SELECT t.term,
           GREATEST(LN((stats.n - COUNT(*) + 0.5) / (COUNT(*) + 0.5)), {BM25_MIN_IDF}) AS w
    FROM chunk_terms AS t, stats WHERE t.term = ANY(%(terms)s) GROUP BY t.term, stats.n
)
SELECT t.chunk_id,
       SUM(idf.w * t.tf * {BM25_K1 + 1}
           / (t.tf + {BM25_K1} * (1 - {BM25_B} + {BM25_B} * c.length / stats.avgdl))) AS score,
       c.text
FROM chunk_terms AS t JOIN idf ON idf.term = t.term JOIN chunks AS c ON c.id = t.chunk_id,
     stats
GROUP BY t.chunk_id, c.text
ORDER BY score DESC, t.chunk_id
LIMIT %(limit)s
"""

P = ParamSpec("P")
R = TypeVar("R")


def _locked(
    method: Callable[Concatenate[PostgresKnowledgeStore, P], R],
) -> Callable[Concatenate[PostgresKnowledgeStore, P], R]:
    @functools.wraps(method)
    def wrapper(self: PostgresKnowledgeStore, /, *args: P.args, **kwargs: P.kwargs) -> R:
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


def is_postgres_url(location: object) -> TypeGuard[str]:
    """Return whether ``location`` names a PostgreSQL database rather than a file."""
    return isinstance(location, str) and location.startswith(URL_SCHEMES)


def redact_url(url: str) -> str:
    """Return ``url`` with any password replaced, for messages and logs."""
    parts = urlsplit(url)
    if parts.password is None:
        return url
    host = parts.hostname or ""
    if parts.port:
        host += f":{parts.port}"
    netloc = f"{parts.username or ''}:***@{host}"
    return urlunsplit(parts._replace(netloc=netloc))


class PostgresKnowledgeStore:
    """A `KnowledgeStore` and `VectorIndex` in a PostgreSQL schema.

    ``read_only=True`` opens an existing store in read-only transactions: nothing is created,
    migrated, or written, and a store at another schema version is refused. ``schema`` names
    the PostgreSQL schema holding the tables.
    """

    def __init__(self, url: str, *, read_only: bool = False, schema: str = DEFAULT_SCHEMA) -> None:
        try:
            import psycopg
            from psycopg import sql
            from psycopg.rows import dict_row
        except ImportError as error:
            raise StoreError(f"PostgreSQL support is not installed; {INSTALL_HINT}.") from error
        self.read_only = read_only
        self.schema = schema
        self.location = redact_url(url)
        self._lock = threading.RLock()
        self._idle = psycopg.pq.TransactionStatus.IDLE
        try:
            self._db: psycopg.Connection[dict[str, Any]] = psycopg.connect(
                url, autocommit=True, row_factory=dict_row
            )
        except psycopg.Error as error:
            raise StoreError(f"Cannot connect to PostgreSQL at {self.location}: {error}") from None
        try:
            if read_only:
                self._db.execute("SET default_transaction_read_only = on")
            else:
                self._db.execute(
                    sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema))
                )
            self._db.execute(
                sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema))
            )
            if read_only:
                self._check_version()
            else:
                self._create()
            self.has_vector = self._table_exists("embeddings")
        except psycopg.Error as error:
            self._db.close()
            raise StoreError(
                f"Cannot open the knowledge store in schema {schema} at {self.location}: "
                f"{str(error).strip()}"
            ) from None
        except BaseException:
            self._db.close()
            raise

    def _create(self) -> None:
        with self._db.transaction():
            # Serialise concurrent first opens of the same schema.
            self._db.execute("SELECT pg_advisory_xact_lock(hashtext(current_schema()))")
            version = self._version()
            if version is not None and version > SCHEMA_VERSION:
                raise StoreError(
                    f"The store in schema {self.schema} at {self.location} has schema version "
                    f"{version}; this version reads {SCHEMA_VERSION}. Upgrade quant-research."
                )
            self._db.execute(_TABLES)
            if self._vector_available():
                self._db.execute(_EMBEDDINGS)
            self._db.execute(
                "INSERT INTO meta VALUES ('schema_version', %s) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),),
            )

    def _vector_available(self) -> bool:
        """Create pgvector if allowed; return whether the ``vector`` type exists."""
        if self._db.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'").fetchone():
            return True
        import psycopg

        try:
            with self._db.transaction():  # a savepoint: a refused CREATE leaves the rest intact
                # In public, so it outlives this schema and serves every schema in the database.
                self._db.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        except psycopg.Error:
            return False
        return True

    def _check_version(self) -> None:
        if not self._table_exists("meta"):
            raise StoreError(
                f"No knowledge store in schema {self.schema} at {self.location}; "
                "run quant-research ingest."
            )
        version = self._version()
        if version != SCHEMA_VERSION:
            raise StoreError(
                f"The store in schema {self.schema} at {self.location} has schema version "
                f"{version}, and this version reads {SCHEMA_VERSION}. Run quant-research ingest "
                "on it once to upgrade it."
            )

    def _version(self) -> int | None:
        if not self._table_exists("meta"):
            return None
        row = self._db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        return None if row is None else int(row["value"])

    def _table_exists(self, name: str) -> bool:
        row = self._db.execute(
            "SELECT to_regclass(format('%%I.%%I', %s::text, %s::text)) AS found",
            (self.schema, name),
        ).fetchone()
        return row is not None and row["found"] is not None

    def _writable(self) -> None:
        if self.read_only:
            raise StoreError("This store is open read-only; open it for ingestion to change it.")

    @contextmanager
    def reading(self) -> Iterator[None]:
        """Hold one read-only, repeatable-read transaction for the block's reads."""
        with self._lock:
            if self._db.info.transaction_status != self._idle:  # already in a transaction
                yield
                return
            with self._db.transaction():
                self._db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                yield

    @_locked
    def close(self) -> None:
        """Close the connection."""
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
            row = self._db.execute("SELECT 1 FROM documents WHERE sha256 = %s", (sha256,))
        else:
            row = self._db.execute(
                "SELECT 1 FROM documents WHERE sha256 = %s AND pipeline = %s", (sha256, pipeline)
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
        from psycopg.types.json import Jsonb

        self._writable()
        terms = {chunk.id: index_terms(chunk.text) for chunk in chunks}
        with self._db.transaction(), self._db.cursor() as cursor:
            cursor.execute(
                "DELETE FROM documents WHERE uri = %s OR sha256 = %s",
                (document.uri, document.sha256),
            )
            cursor.execute(
                "INSERT INTO documents (id, sha256, uri, title, authors, published, "
                "license_note, loader_version, pipeline) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    document.id,
                    document.sha256,
                    document.uri,
                    document.title,
                    Jsonb(document.authors),
                    document.published,
                    document.license_note,
                    document.loader_version,
                    document.pipeline,
                ),
            )
            cursor.executemany(
                "INSERT INTO chunks (id, document_id, ordinal, text, line_start, line_end, "
                "section, page, length) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
                        sum(terms[c.id].values()),
                    )
                    for c in chunks
                ],
            )
            cursor.executemany(
                "INSERT INTO chunk_terms (term, chunk_id, tf) VALUES (%s, %s, %s)",
                [
                    (term, chunk_id, count)
                    for chunk_id, counts in terms.items()
                    for term, count in counts.items()
                ],
            )
            cursor.executemany(
                'INSERT INTO mentions (entity_id, chunk_id, start, "end", extractor, confidence) '
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                [
                    (m.entity_id, m.chunk_id, m.start, m.end, m.extractor, m.confidence)
                    for m in mentions
                ],
            )
            cursor.executemany(
                "INSERT INTO relations (id, subject_id, predicate, object_id, chunk_id, "
                "extractor, extractor_version, confidence) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
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
        from psycopg.types.json import Jsonb

        self._writable()
        with self._db.transaction(), self._db.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO entities (id, type, name, aliases) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET type = excluded.type, name = excluded.name, "
                "aliases = excluded.aliases",
                [(e.id, e.type, e.name, Jsonb(e.aliases)) for e in entities],
            )

    @_locked
    def publish_snapshot(self, build: Callable[[list[str]], CorpusSnapshot]) -> CorpusSnapshot:
        """Build the snapshot from the current documents and publish it under one lock.

        The lock conflicts with document writes, so a concurrent ingestion either commits
        before (and is included) or waits until after.
        """
        from psycopg.types.json import Jsonb

        self._writable()
        with self._db.transaction():
            self._db.execute("LOCK TABLE documents IN SHARE ROW EXCLUSIVE MODE")
            snapshot = build(self.document_hashes())
            current = self.current_snapshot()
            if current is None or current.id != snapshot.id:
                self._db.execute(
                    "INSERT INTO corpus_snapshots (id, document_count, vocabulary_version, "
                    "extractor_versions) VALUES (%s, %s, %s, %s)",
                    (
                        snapshot.id,
                        snapshot.document_count,
                        snapshot.vocabulary_version,
                        Jsonb(snapshot.extractor_versions),
                    ),
                )
        return snapshot

    @_locked
    def current_snapshot(self) -> CorpusSnapshot | None:
        """Return the most recently recorded snapshot, if any."""
        row = self._db.execute(
            "SELECT id, document_count, vocabulary_version, extractor_versions "
            "FROM corpus_snapshots ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return None if row is None else CorpusSnapshot.model_validate(row)

    @_locked
    def document_hashes(self) -> list[str]:
        """Return the content hashes of every stored document, sorted."""
        rows = self._db.execute("SELECT sha256 FROM documents ORDER BY sha256").fetchall()
        return [row["sha256"] for row in rows]

    @_locked
    def get_document(self, document_id: str) -> Document | None:
        """Return a document by id."""
        row = self._db.execute("SELECT * FROM documents WHERE id = %s", (document_id,)).fetchone()
        return None if row is None else Document.model_validate(row)

    @_locked
    def get_chunk(self, chunk_id: str) -> Chunk | None:
        """Return a chunk by id."""
        row = self._db.execute("SELECT * FROM chunks WHERE id = %s", (chunk_id,)).fetchone()
        return None if row is None else Chunk.model_validate(row)

    @_locked
    def get_entities(self, entity_ids: Iterable[str]) -> dict[str, Entity]:
        """Return the known entities among ``entity_ids``."""
        rows = self._db.execute(
            "SELECT * FROM entities WHERE id = ANY(%s)", (sorted(set(entity_ids)),)
        )
        return {row["id"]: Entity.model_validate(row) for row in rows}

    @_locked
    def mentioned_entities(self, chunk_ids: Iterable[str]) -> dict[str, list[str]]:
        """Return entity ids mentioned in each chunk, in order of first mention."""
        found: dict[str, list[str]] = {}
        rows = self._db.execute(
            "SELECT chunk_id, entity_id, MIN(start) AS first FROM mentions "
            "WHERE chunk_id = ANY(%s) GROUP BY chunk_id, entity_id "
            "ORDER BY chunk_id, first, entity_id",
            (sorted(set(chunk_ids)),),
        )
        for row in rows:
            found.setdefault(row["chunk_id"], []).append(row["entity_id"])
        return found

    @_locked
    def chunks_mentioning(self, entity_ids: Iterable[str], limit: int) -> list[str]:
        """Return chunk ids ranked by distinct entities mentioned, then by mention count."""
        rows = self._db.execute(
            "SELECT chunk_id FROM mentions WHERE entity_id = ANY(%s) GROUP BY chunk_id "
            "ORDER BY COUNT(DISTINCT entity_id) DESC, COUNT(*) DESC, chunk_id LIMIT %s",
            (sorted(set(entity_ids)), limit),
        )
        return [row["chunk_id"] for row in rows]

    @_locked
    def keyword_search(self, query: str, limit: int) -> list[KeywordHit]:
        """Return chunks ranked by FTS5's BM25 over the indexed terms."""
        tokens = query_tokens(query)
        if not tokens:
            return []
        rows = self._db.execute(_BM25, {"terms": tokens, "limit": limit})
        return [
            KeywordHit(
                chunk_id=row["chunk_id"], score=row["score"], matched=matched(tokens, row["text"])
            )
            for row in rows
        ]

    @_locked
    def term_frequencies(self, terms: Iterable[str]) -> tuple[int, dict[str, int]]:
        """Return the chunk count and, per term, the number of chunks containing it."""
        tokens = sorted({token for term in terms for token in query_tokens(term)})
        total = self._db.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()
        rows = self._db.execute(
            "SELECT term, COUNT(*) AS n FROM chunk_terms WHERE term = ANY(%s) GROUP BY term",
            (tokens,),
        )
        counts = {row["term"]: row["n"] for row in rows}
        return (total["n"] if total else 0), {token: counts.get(token, 0) for token in tokens}

    @_locked
    def all_relations(self) -> list[Relation]:
        """Return every stored relation, by id (for evaluation and export)."""
        rows = self._db.execute("SELECT * FROM relations ORDER BY id")
        return [Relation.model_validate(row) for row in rows]

    @_locked
    def upsert_embeddings(self, model_id: str, vectors: Sequence[tuple[str, list[float]]]) -> None:
        """Store ``(chunk_id, vector)`` pairs for ``model_id``, replacing existing ones."""
        self._writable()
        if not self.has_vector:
            raise StoreError(NO_VECTOR)
        with self._db.transaction(), self._db.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO embeddings (chunk_id, model, dimensions, embedding) "
                "VALUES (%s, %s, %s, %s::vector) ON CONFLICT (chunk_id, model) DO UPDATE SET "
                "dimensions = excluded.dimensions, embedding = excluded.embedding",
                [
                    (chunk_id, model_id, len(vector), _vector(vector))
                    for chunk_id, vector in vectors
                ],
            )

    @_locked
    def has_embeddings(self, model_id: str) -> bool:
        """Return whether any chunk has an embedding for ``model_id``."""
        if not self.has_vector:
            return False
        row = self._db.execute("SELECT 1 FROM embeddings WHERE model = %s LIMIT 1", (model_id,))
        return row.fetchone() is not None

    @_locked
    def chunks_without_embeddings(self, model_id: str) -> list[tuple[str, str]]:
        """Return ``(chunk_id, text)`` for chunks with no ``model_id`` embedding, by id."""
        if not self.has_vector:
            raise StoreError(NO_VECTOR)
        rows = self._db.execute(
            "SELECT c.id, c.text FROM chunks AS c LEFT JOIN embeddings AS e "
            "ON e.chunk_id = c.id AND e.model = %s WHERE e.chunk_id IS NULL ORDER BY c.id",
            (model_id,),
        )
        return [(row["id"], row["text"]) for row in rows]

    @_locked
    def vector_search(self, model_id: str, vector: list[float], limit: int) -> list[VectorHit]:
        """Return chunks by cosine similarity to ``vector`` (exact), best first."""
        if not self.has_vector or not math.sqrt(sum(value * value for value in vector)):
            return []
        rows = self._db.execute(
            "SELECT chunk_id, 1 - (embedding <=> %(query)s::vector) AS score FROM embeddings "
            "WHERE model = %(model)s AND dimensions = %(dimensions)s "
            "AND vector_norm(embedding) > 0 "
            "ORDER BY embedding <=> %(query)s::vector, chunk_id LIMIT %(limit)s",
            {
                "query": _vector(vector),
                "model": model_id,
                "dimensions": len(vector),
                "limit": limit,
            },
        )
        return [VectorHit(chunk_id=row["chunk_id"], score=row["score"]) for row in rows]

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
            for row in self._db.execute(_DISTANCES, {"seeds": seeds, **limits})
        }
        rows = self._db.execute(
            _EDGES,
            {
                "ids": list(distances),
                "distances": list(distances.values()),
                "per_triple": EDGES_PER_TRIPLE,
                **limits,
            },
        )
        return [
            GraphEdge(
                relation=Relation.model_validate({key: row[key] for key in Relation.model_fields}),
                hops=row["hops"],
                support=row["support"],
            )
            for row in rows
        ]


def _vector(values: Sequence[float]) -> str:
    """Return pgvector's text form of ``values``."""
    return json.dumps([float(value) for value in values])
