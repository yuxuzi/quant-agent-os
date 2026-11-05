"""Hosted embedding models over plain HTTPS: Voyage AI and OpenAI (standard library only).

Both adapters implement the `Embedder` port. They batch inputs under each provider's per-request
limits, retry 429 and 5xx responses with exponential backoff and jitter (honouring
``Retry-After``), keep results in input order, and return unit-length vectors, because neither
provider documents normalisation. API keys are `SecretStr` and never appear in errors or logs.

Calling these adapters sends text to the provider: chunk text during ingestion, and query text
during search. Tools that embed queries therefore declare the ``network`` permission.

API references (checked 2026-10-30): https://docs.voyageai.com/reference/embeddings-api and
https://developers.openai.com/api/reference/resources/embeddings/methods/create
"""

from __future__ import annotations

import json
import math
import random
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from pydantic import SecretStr

from quant_research.domain.ports import EmbeddingError

InputKind = Literal["document", "query"]

RETRYABLE = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 5
CHARS_PER_TOKEN = 3  # conservative estimate for batching; providers count exactly


class EmbeddingServiceError(EmbeddingError):
    """Raised when an embedding provider rejects a request, stays unavailable, or runs out of
    time."""


@dataclass(frozen=True)
class HttpResponse:
    """The parts of an HTTP response the adapters need."""

    status: int
    body: bytes
    retry_after: float | None = None


Transport = Callable[[urllib.request.Request, float], HttpResponse]
Sleep = Callable[[float], None]
Clock = Callable[[], float]


def urllib_transport(request: urllib.request.Request, timeout: float) -> HttpResponse:
    """Send ``request`` with urllib; HTTP error statuses are returned, not raised."""
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HttpResponse(response.status, response.read())
    except urllib.error.HTTPError as error:
        with error:  # the error owns the open response; close it so retries do not leak
            retry_after = error.headers.get("Retry-After") if error.headers else None
            return HttpResponse(error.code, error.read(), _seconds(retry_after))


def _seconds(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


class _HttpEmbedder:
    """Shared batching, retries, ordering, and normalisation."""

    provider: str
    url: str
    key_variable: str
    max_batch: int
    max_batch_tokens: int
    requires_network = True

    def __init__(
        self,
        api_key: SecretStr,
        *,
        timeout: float,
        transport: Transport | None,
        sleep: Sleep | None,
        clock: Clock | None = None,
    ) -> None:
        if not api_key.get_secret_value().strip():
            raise EmbeddingServiceError(
                f"{self.provider} needs an API key; set {self.key_variable}."
            )
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport or urllib_transport
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic

    def embed(
        self,
        texts: Sequence[str],
        *,
        kind: InputKind = "document",
        deadline: float | None = None,
    ) -> list[list[float]]:
        """Return one unit-length vector per text, in order.

        With ``deadline`` (a `time.monotonic` instant), each request's timeout shrinks to the
        time left and retries stop once a backoff would pass it.
        """
        vectors: list[list[float]] = []
        for batch in self._batches(texts):
            vectors += self._embed_batch(batch, kind, deadline)
        return vectors

    def _remaining(self, deadline: float | None) -> float:
        if deadline is None:
            return self._timeout
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise EmbeddingServiceError(f"{self.provider} did not answer within the time budget.")
        return min(self._timeout, remaining)

    def _pause(self, seconds: float, deadline: float | None) -> None:
        if deadline is not None and self._clock() + seconds >= deadline:
            raise EmbeddingServiceError(
                f"{self.provider} kept failing; no time left in the budget to retry."
            )
        self._sleep(seconds)

    def _batches(self, texts: Sequence[str]) -> list[list[str]]:
        batches: list[list[str]] = []
        current: list[str] = []
        tokens = 0
        for text in texts:
            if not text.strip():
                raise EmbeddingServiceError(f"{self.provider} cannot embed empty text.")
            estimate = len(text) // CHARS_PER_TOKEN + 1
            if current and (
                len(current) >= self.max_batch or tokens + estimate > self.max_batch_tokens
            ):
                batches.append(current)
                current, tokens = [], 0
            current.append(text)
            tokens += estimate
        if current:
            batches.append(current)
        return batches

    def _embed_batch(
        self, batch: list[str], kind: InputKind, deadline: float | None
    ) -> list[list[float]]:
        payload = json.dumps(self._body(batch, kind)).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        for attempt in range(1, MAX_ATTEMPTS + 1):
            request = urllib.request.Request(self.url, data=payload, headers=headers, method="POST")
            try:
                response = self._transport(request, self._remaining(deadline))
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                if attempt == MAX_ATTEMPTS:
                    raise EmbeddingServiceError(
                        f"{self.provider} is unreachable after {MAX_ATTEMPTS} attempts: {error}."
                    ) from error
                self._pause(_backoff(attempt, None), deadline)
                continue
            if response.status == 200:
                return self._parse(response.body, len(batch))
            if response.status in RETRYABLE and attempt < MAX_ATTEMPTS:
                self._pause(_backoff(attempt, response.retry_after), deadline)
                continue
            raise EmbeddingServiceError(self._explain(response))
        raise AssertionError("unreachable")  # pragma: no cover

    def _parse(self, body: bytes, expected: int) -> list[list[float]]:
        try:
            data: list[dict[str, Any]] = json.loads(body)["data"]
            ordered = sorted(data, key=lambda item: int(item["index"]))
            vectors = [[float(value) for value in item["embedding"]] for item in ordered]
        except (ValueError, KeyError, TypeError) as error:
            raise EmbeddingServiceError(
                f"{self.provider} returned an unexpected response."
            ) from error
        if len(vectors) != expected:
            raise EmbeddingServiceError(
                f"{self.provider} returned {len(vectors)} embeddings for {expected} texts."
            )
        return [_normalise(vector) for vector in vectors]

    def _explain(self, response: HttpResponse) -> str:
        detail = _detail(response.body)
        if response.status == 401:
            return f"{self.provider} rejected the API key (401); check {self.key_variable}."
        if response.status == 403:
            return f"{self.provider} refused the request (403){detail}."
        if response.status == 429:
            return (
                f"{self.provider} rate limit still exceeded after {MAX_ATTEMPTS} attempts (429); "
                f"ingest fewer documents at a time or raise your limit{detail}."
            )
        if response.status in RETRYABLE:
            return f"{self.provider} is unavailable ({response.status}); try again later{detail}."
        return f"{self.provider} rejected the request ({response.status}){detail}."

    def _body(self, batch: list[str], kind: InputKind) -> dict[str, Any]:
        raise NotImplementedError


class VoyageEmbedder(_HttpEmbedder):
    """Voyage AI embeddings; ``voyage-finance-2`` (finance-tuned) by default.

    Queries and documents are embedded with Voyage's ``input_type`` so each gets its retrieval
    prompt.
    """

    provider = "Voyage AI"
    url = "https://api.voyageai.com/v1/embeddings"
    key_variable = "VOYAGE_API_KEY"
    max_batch = 128  # the API allows 1,000; Voyage's rate-limit guide batches 128

    # Default dimension and allowed ``output_dimension`` values per model (from the docs).
    MODELS: ClassVar[dict[str, tuple[int, frozenset[int]]]] = {
        "voyage-finance-2": (1024, frozenset({1024})),
        "voyage-law-2": (1024, frozenset({1024})),
        **{
            name: (1024, frozenset({256, 512, 1024, 2048}))
            for name in (
                "voyage-4-large",
                "voyage-4",
                "voyage-4-lite",
                "voyage-code-4",
                "voyage-3-large",
                "voyage-3.5",
                "voyage-3.5-lite",
                "voyage-code-3",
            )
        },
    }
    # Total tokens per request; we stay below each with the batching estimate.
    TOKEN_LIMITS: ClassVar[dict[str, int]] = {
        "voyage-4-lite": 1_000_000,
        "voyage-3.5-lite": 1_000_000,
        "voyage-4": 320_000,
        "voyage-3.5": 320_000,
    }
    DEFAULT_TOKEN_LIMIT = 120_000

    def __init__(
        self,
        api_key: SecretStr,
        *,
        model: str = "voyage-finance-2",
        dimensions: int | None = None,
        timeout: float = 30.0,
        transport: Transport | None = None,
        sleep: Sleep | None = None,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(api_key, timeout=timeout, transport=transport, sleep=sleep, clock=clock)
        if model not in self.MODELS:
            known = ", ".join(sorted(self.MODELS))
            raise EmbeddingServiceError(f"Unknown Voyage model '{model}'. Supported: {known}.")
        default, allowed = self.MODELS[model]
        if dimensions is not None and dimensions not in allowed:
            options = ", ".join(str(value) for value in sorted(allowed))
            raise EmbeddingServiceError(f"{model} supports dimensions {options}, not {dimensions}.")
        self.model = model
        self.dimensions = dimensions or default
        self._output_dimension = dimensions if dimensions not in (None, default) else None
        self.max_batch_tokens = int(self.TOKEN_LIMITS.get(model, self.DEFAULT_TOKEN_LIMIT) * 0.8)
        self.model_id = f"voyage:{model}:{self.dimensions}"

    def _body(self, batch: list[str], kind: InputKind) -> dict[str, Any]:
        body: dict[str, Any] = {"input": batch, "model": self.model, "input_type": kind}
        if self._output_dimension is not None:
            body["output_dimension"] = self._output_dimension
        return body


class OpenAIEmbedder(_HttpEmbedder):
    """OpenAI embeddings; ``text-embedding-3-small`` by default.

    OpenAI has no query/document distinction, so ``kind`` is ignored.
    """

    provider = "OpenAI"
    url = "https://api.openai.com/v1/embeddings"
    key_variable = "OPENAI_API_KEY"
    max_batch = 2048
    max_batch_tokens = 240_000  # the API allows 300,000 tokens per request

    MODELS: ClassVar[dict[str, int]] = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
    }

    def __init__(
        self,
        api_key: SecretStr,
        *,
        model: str = "text-embedding-3-small",
        dimensions: int | None = None,
        timeout: float = 30.0,
        transport: Transport | None = None,
        sleep: Sleep | None = None,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(api_key, timeout=timeout, transport=transport, sleep=sleep, clock=clock)
        if model not in self.MODELS:
            known = ", ".join(sorted(self.MODELS))
            raise EmbeddingServiceError(f"Unknown OpenAI model '{model}'. Supported: {known}.")
        default = self.MODELS[model]
        if dimensions is not None and not 1 <= dimensions <= default:
            raise EmbeddingServiceError(f"{model} supports 1 to {default} dimensions.")
        self.model = model
        self.dimensions = dimensions or default
        self._requested_dimensions = dimensions if dimensions not in (None, default) else None
        self.model_id = f"openai:{model}:{self.dimensions}"

    def _body(self, batch: list[str], kind: InputKind) -> dict[str, Any]:
        body: dict[str, Any] = {"input": batch, "model": self.model, "encoding_format": "float"}
        if self._requested_dimensions is not None:
            body["dimensions"] = self._requested_dimensions
        return body


def _backoff(attempt: int, retry_after: float | None) -> float:
    if retry_after is not None:
        return min(retry_after, 60.0)
    return min(60.0, 2.0 ** (attempt - 1)) * (0.5 + random.random() / 2)


def _normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


def _detail(body: bytes) -> str:
    try:
        payload = json.loads(body)
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    message = payload.get("detail") or (error.get("message") if isinstance(error, dict) else error)
    return f": {str(message)[:300]}" if message else ""
