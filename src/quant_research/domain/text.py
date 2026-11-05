"""Query-term normalisation for keyword retrieval."""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[A-Za-z0-9]+")

# Function words and question words that carry no retrieval signal in research questions.
STOPWORDS = frozenset(
    """
    a about after again against all also an and any are as at be been being between both but by
    can could did do does each few for from further had has have here how i if in into is it its
    just may might more most no not of on only or other our over own same should so some such
    than that the their them then there these they this those through to too under up very was
    we were what when where which while who whom why will with within would you your
    """.split()  # noqa: SIM905 - a word list reads better than 95 quoted strings
)


def content_terms(query: str) -> list[str]:
    """Return the lower-cased, de-duplicated query tokens worth matching, in query order.

    Stopwords and single characters are dropped.
    """
    terms: list[str] = []
    for token in _TOKEN.findall(query):
        term = token.lower()
        if len(term) > 1 and term not in STOPWORDS and term not in terms:
            terms.append(term)
    return terms
