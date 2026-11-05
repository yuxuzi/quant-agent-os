"""An embedding model that runs on this machine: ONNX through the ``local-embeddings`` extra.

No text leaves the machine. The model's files are downloaded once, on first use, into the
platform's home. Only models with a measured score floor are offered: without one, the vector
channel answers questions the corpus cannot answer (docs/plans/real-corpus-eval.md, C6).
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Literal

from quant_research.infrastructure.remote_embedding import EmbeddingServiceError

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
# Dimensions and the lowest cosine similarity that counts as evidence, for each measured model.
MODELS: dict[str, tuple[int, float]] = {DEFAULT_MODEL: (384, 0.8)}
INSTALL_HINT = (
    "The local embedder needs the local-embeddings extra: "
    'install "quant-agent-os[local-embeddings]" (Python 3.11 to 3.14).'
)


class LocalEmbedder:
    """A fastembed ONNX model; ``backend`` replaces the loaded model in tests."""

    requires_network = False

    def __init__(
        self, model: str = DEFAULT_MODEL, *, cache_dir: Path | None = None, backend: Any = None
    ) -> None:
        if model not in MODELS:
            raise EmbeddingServiceError(
                f"No measured score floor for local model {model!r}; use {', '.join(MODELS)}."
            )
        self.model_id = f"local:{model}"
        self.dimensions, self.min_score = MODELS[model]
        self._backend = backend if backend is not None else _load(model, cache_dir)

    def embed(
        self,
        texts: Sequence[str],
        *,
        kind: Literal["document", "query"] = "document",
        deadline: float | None = None,
    ) -> list[list[float]]:
        """Return one unit-length vector per text; a local model has no deadline to respect."""
        embed = self._backend.query_embed if kind == "query" else self._backend.embed
        vectors: Iterable[Iterable[float]] = embed(list(texts))
        return [[float(value) for value in vector] for vector in vectors]


def _load(model: str, cache_dir: Path | None) -> Any:
    try:
        fastembed = importlib.import_module("fastembed")
    except ImportError as error:
        raise EmbeddingServiceError(INSTALL_HINT) from error
    try:
        return fastembed.TextEmbedding(model, cache_dir=str(cache_dir) if cache_dir else None)
    except Exception as error:  # the library raises many types, from downloads and from ONNX
        raise EmbeddingServiceError(
            f"Could not load local model {model}: {type(error).__name__}: {error}. Its files are "
            "downloaded on first use, so the first run needs network access."
        ) from error
