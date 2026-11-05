"""Tokens for keyword search, shared by the SQLite and PostgreSQL stores.

Query tokens are ASCII letter and digit runs, lower-cased. Indexed terms follow SQLite FTS5's
default ``unicode61`` tokenizer: runs of Unicode letters and digits, case-folded, with
diacritics removed. The PostgreSQL store indexes these terms itself and scores them with FTS5's
BM25, so both stores rank the same passages the same way.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable

# FTS5's BM25 parameters (sqlite/ext/fts5/fts5_aux.c).
BM25_K1 = 1.2
BM25_B = 0.75
BM25_MIN_IDF = 1e-6

_QUERY_TOKEN = re.compile(r"[A-Za-z0-9]+")
_INDEX_TOKEN = re.compile(r"[^\W_]+")


def query_tokens(text: str) -> list[str]:
    """Return the distinct lower-cased query tokens of ``text``, sorted."""
    return sorted({token.lower() for token in _QUERY_TOKEN.findall(text)})


def matched(tokens: Iterable[str], text: str) -> list[str]:
    """Return the ``tokens`` that occur in ``text`` as whole words, case-insensitively."""
    words = {word.lower() for word in _QUERY_TOKEN.findall(text)}
    return [token for token in tokens if token in words]


def index_terms(text: str) -> Counter[str]:
    """Return each indexed term of ``text`` with its count, as FTS5's tokenizer splits it."""
    folded = "".join(
        char
        for char in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(char)
    )
    return Counter(_INDEX_TOKEN.findall(folded))
