"""A deterministic, offline stand-in for a real embedding model (the "mock vector" embedder).

Features are content words, adjacent word pairs, and the vocabulary entities a text mentions,
folded into a fixed number of dimensions with signed feature hashing and L2-normalised. Entity
features make aliases land together ("HML" and "book-to-market" share ``factor:value``), which
gives the vector channel something keyword search does not. It is not semantic understanding;
swap in a real `Embedder` (a different ``model_id``) for production.
"""

from __future__ import annotations

import hashlib
import itertools
import math
from collections.abc import Sequence

from quant_research.domain.text import content_terms
from quant_research.domain.vocabulary import EntityMatcher

ENTITY_WEIGHT = 3.0
BIGRAM_WEIGHT = 0.5


class HashingEmbedder:
    """Signed feature hashing over words, word pairs, and vocabulary entities."""

    def __init__(self, matcher: EntityMatcher | None = None, *, dimensions: int = 256) -> None:
        if dimensions < 8:
            raise ValueError("HashingEmbedder needs at least 8 dimensions.")
        self.dimensions = dimensions
        self._matcher = matcher
        self.model_id = f"hashing-v1-{dimensions}" + ("-entities" if matcher else "")

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one unit-length vector per text; texts without features map to zeros."""
        return [self._embed(text) for text in texts]

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        terms = content_terms(text)
        features = [(term, 1.0) for term in terms]
        features += [(f"{a} {b}", BIGRAM_WEIGHT) for a, b in itertools.pairwise(terms)]
        if self._matcher is not None:
            features += [
                (f"entity:{match.entity_id}", ENTITY_WEIGHT) for match in self._matcher.find(text)
            ]
        for feature, weight in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign * weight
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector
