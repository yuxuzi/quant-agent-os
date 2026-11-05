"""What the two knowledge stores share: their lifecycle, and the SQL that does not differ.

The rest of each store's SQL differs for a reason: how a list is passed (``json_each`` against
``ANY``), how keyword search ranks (FTS5 against BM25 in SQL), and how vectors are stored. The
shared store contract tests hold the two to the same answers.
"""

from __future__ import annotations

import functools
import threading
from collections.abc import Callable
from types import TracebackType
from typing import Any, Concatenate, ParamSpec, Self, TypeVar

# Written in psycopg's style; `qmark` adapts one for SQLite.
HAS_DOCUMENT = "SELECT 1 FROM documents WHERE sha256 = %s"
HAS_DOCUMENT_FROM_PIPELINE = "SELECT 1 FROM documents WHERE sha256 = %s AND pipeline = %s"
INSERT_DOCUMENT = (
    "INSERT INTO documents (id, sha256, uri, title, authors, published, license_note, "
    "loader_version, pipeline) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
INSERT_SNAPSHOT = (
    "INSERT INTO corpus_snapshots (id, document_count, vocabulary_version, extractor_versions) "
    "VALUES (%s, %s, %s, %s)"
)
ALL_ENTITIES = "SELECT * FROM entities ORDER BY id"
ALL_RELATIONS = "SELECT * FROM relations ORDER BY id"
DOCUMENT_HASHES = "SELECT sha256 FROM documents ORDER BY sha256"
GET_DOCUMENT = "SELECT * FROM documents WHERE id = %s"
GET_CHUNK = "SELECT * FROM chunks WHERE id = %s"
HAS_EMBEDDINGS = "SELECT 1 FROM embeddings WHERE model = %s LIMIT 1"
CHUNKS_WITHOUT_EMBEDDINGS = (
    "SELECT c.id, c.text FROM chunks AS c LEFT JOIN embeddings AS e "
    "ON e.chunk_id = c.id AND e.model = %s WHERE e.chunk_id IS NULL ORDER BY c.id"
)


def qmark(statement: str) -> str:
    """Return a shared statement with SQLite's placeholders."""
    return statement.replace("%s", "?")


class StoreError(RuntimeError):
    """Raised when a store cannot be used as requested; the message says what to do."""


class StoreLifecycle:
    """Opening state, closing, and the read-only guard, the same for every store."""

    _db: Any
    _lock: threading.RLock
    read_only: bool

    def _writable(self) -> None:
        if self.read_only:
            raise StoreError("This store is open read-only; open it for ingestion to change it.")

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


S = TypeVar("S", bound=StoreLifecycle)
P = ParamSpec("P")
R = TypeVar("R")


def locked(method: Callable[Concatenate[S, P], R]) -> Callable[Concatenate[S, P], R]:
    """Run a store method under the store's lock."""

    @functools.wraps(method)
    def wrapper(self: S, /, *args: P.args, **kwargs: P.kwargs) -> R:
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper
