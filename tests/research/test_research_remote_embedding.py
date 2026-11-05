"""Hosted embedders (plan step R11) against a fake HTTP transport: no network, no key needed."""

import json
import math
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from pydantic import SecretStr

from quant_agent_os.tools import Permission
from quant_research import ResearchQuery, open_research, research_tools
from quant_research.config import EmbeddingSettings, make_embedder
from quant_research.domain.vocabulary import EntityMatcher, load_vocabulary
from quant_research.infrastructure.embedding import HashingEmbedder
from quant_research.infrastructure.remote_embedding import (
    MAX_ATTEMPTS,
    EmbeddingServiceError,
    HttpResponse,
    OpenAIEmbedder,
    VoyageEmbedder,
    urllib_transport,
)

KEY = SecretStr("sk-test-secret-value")
VOCABULARY = load_vocabulary()
SIMULATED = HashingEmbedder(EntityMatcher(VOCABULARY), dimensions=1024)


class FakeApi:
    """Answers embedding requests like the real APIs: scaled vectors, reversed order, indexed."""

    def __init__(self, failures: list[HttpResponse] | None = None) -> None:
        self.failures = list(failures or [])
        self.requests: list[dict[str, object]] = []
        self.headers: list[dict[str, str]] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> HttpResponse:
        body = json.loads(request.data)  # type: ignore[arg-type]
        self.requests.append(body)
        self.headers.append(dict(request.header_items()))
        if self.failures:
            return self.failures.pop(0)
        vectors = SIMULATED.embed(body["input"])
        data = [
            {"object": "embedding", "index": index, "embedding": [3 * v for v in vector]}
            for index, vector in enumerate(vectors)
        ]
        return HttpResponse(200, json.dumps({"object": "list", "data": data[::-1]}).encode())


def _voyage(api: FakeApi, **options: object) -> VoyageEmbedder:
    return VoyageEmbedder(KEY, transport=api, sleep=lambda _: None, **options)  # type: ignore[arg-type]


def test_voyage_requests_carry_model_input_type_and_bearer_key() -> None:
    api = FakeApi()

    _voyage(api).embed(["carry"], kind="query")

    assert api.requests == [
        {"input": ["carry"], "model": "voyage-finance-2", "input_type": "query"}
    ]
    assert api.headers[0]["Authorization"] == "Bearer sk-test-secret-value"
    assert api.headers[0]["Content-type"] == "application/json"


def test_results_keep_input_order_and_are_unit_length() -> None:
    texts = ["momentum crashes", "carry in currencies", "PEAD"]

    vectors = _voyage(FakeApi()).embed(texts)

    for got, expected in zip(vectors, SIMULATED.embed(texts), strict=True):
        assert got == pytest.approx(expected)
    assert all(math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0) for vector in vectors)


def test_batches_respect_the_count_limit() -> None:
    api = FakeApi()

    _voyage(api).embed([f"text {n}" for n in range(300)])

    assert [len(request["input"]) for request in api.requests] == [128, 128, 44]  # type: ignore[arg-type]


def test_batches_respect_the_token_budget() -> None:
    api = FakeApi()
    embedder = _voyage(api)
    long_text = "word " * 30_000  # about 50,000 estimated tokens

    embedder.embed([long_text, long_text, "short"])

    assert len(api.requests) == 2


def test_rate_limits_and_server_errors_are_retried_with_backoff() -> None:
    sleeps: list[float] = []
    api = FakeApi([HttpResponse(429, b"{}", retry_after=2.0), HttpResponse(503, b"")])

    vectors = VoyageEmbedder(KEY, transport=api, sleep=sleeps.append).embed(["carry"])

    assert len(vectors) == 1
    assert len(api.requests) == 3
    assert sleeps[0] == 2.0 and 0 < sleeps[1] <= 2.0


def test_network_errors_are_retried_then_reported() -> None:
    calls = []

    def unreachable(request: urllib.request.Request, timeout: float) -> HttpResponse:
        calls.append(timeout)
        raise urllib.error.URLError("no route to host")

    embedder = VoyageEmbedder(KEY, transport=unreachable, sleep=lambda _: None, timeout=7)

    with pytest.raises(EmbeddingServiceError, match="unreachable after 5 attempts"):
        embedder.embed(["carry"])
    assert calls == [7] * MAX_ATTEMPTS


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (HttpResponse(401, b'{"detail": "bad key"}'), "check VOYAGE_API_KEY"),
        (HttpResponse(400, b'{"detail": "Batch too large"}'), "Batch too large"),
        (HttpResponse(429, b"{}"), "rate limit still exceeded"),
        (HttpResponse(500, b"oops"), "unavailable \\(500\\)"),
    ],
)
def test_failures_are_actionable_and_never_leak_the_key(
    response: HttpResponse, message: str
) -> None:
    api = FakeApi([response] * MAX_ATTEMPTS)

    with pytest.raises(EmbeddingServiceError, match=message) as raised:
        _voyage(api).embed(["carry"])
    assert "sk-test" not in str(raised.value)


def test_malformed_responses_are_rejected() -> None:
    api = FakeApi([HttpResponse(200, b'{"data": [{"index": 0}]}')])

    with pytest.raises(EmbeddingServiceError, match="unexpected response"):
        _voyage(api).embed(["carry"])


def test_empty_texts_and_keys_are_rejected_before_any_request() -> None:
    api = FakeApi()

    with pytest.raises(EmbeddingServiceError, match="empty text"):
        _voyage(api).embed(["carry", "  "])
    with pytest.raises(EmbeddingServiceError, match="VOYAGE_API_KEY"):
        VoyageEmbedder(SecretStr(" "))
    assert api.requests == []


def test_voyage_models_and_dimensions_are_validated() -> None:
    api = FakeApi()

    flexible = _voyage(api, model="voyage-4", dimensions=512)
    flexible.embed(["carry"])

    assert flexible.model_id == "voyage:voyage-4:512"
    assert api.requests[0]["output_dimension"] == 512
    assert _voyage(api).model_id == "voyage:voyage-finance-2:1024"
    with pytest.raises(EmbeddingServiceError, match="supports dimensions 1024"):
        _voyage(api, dimensions=512)
    with pytest.raises(EmbeddingServiceError, match="Unknown Voyage model"):
        _voyage(api, model="voyage-9")


def test_openai_requests_and_dimensions() -> None:
    api = FakeApi()
    embedder = OpenAIEmbedder(KEY, dimensions=256, transport=api, sleep=lambda _: None)

    embedder.embed(["carry"], kind="query")

    assert api.requests == [
        {
            "input": ["carry"],
            "model": "text-embedding-3-small",
            "encoding_format": "float",
            "dimensions": 256,
        }
    ]
    assert embedder.model_id == "openai:text-embedding-3-small:256"
    assert OpenAIEmbedder(KEY, model="text-embedding-3-large").dimensions == 3072
    with pytest.raises(EmbeddingServiceError, match="1 to 1536"):
        OpenAIEmbedder(KEY, dimensions=4096)
    with pytest.raises(EmbeddingServiceError, match="Unknown OpenAI model"):
        OpenAIEmbedder(KEY, model="ada")


def test_openai_errors_name_the_openai_key() -> None:
    api = FakeApi([HttpResponse(401, b'{"error": {"message": "Incorrect API key"}}')])
    embedder = OpenAIEmbedder(KEY, transport=api, sleep=lambda _: None)

    with pytest.raises(EmbeddingServiceError, match="check OPENAI_API_KEY"):
        embedder.embed(["carry"])


def test_settings_read_conventional_and_prefixed_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOYAGE_API_KEY", "v-key")
    monkeypatch.setenv("QUANT_RESEARCH_OPENAI_API_KEY", "o-key")

    settings = EmbeddingSettings(_env_file=None)  # type: ignore[call-arg]

    assert settings.voyage_api_key is not None
    assert settings.voyage_api_key.get_secret_value() == "v-key"
    assert settings.openai_api_key is not None
    assert "o-key" not in repr(settings)


def test_factory_builds_each_embedder_and_fails_fast_without_keys() -> None:
    keys = EmbeddingSettings(voyage_api_key=KEY, openai_api_key=KEY, _env_file=None)  # type: ignore[call-arg]
    none = EmbeddingSettings(_env_file=None)  # type: ignore[call-arg]

    assert isinstance(make_embedder("hashing", VOCABULARY), HashingEmbedder)
    assert isinstance(make_embedder("voyage", VOCABULARY, settings=keys), VoyageEmbedder)
    openai = make_embedder("openai", VOCABULARY, model="text-embedding-3-large", settings=keys)
    assert openai.model_id == "openai:text-embedding-3-large:3072"
    with pytest.raises(EmbeddingServiceError, match="needs VOYAGE_API_KEY"):
        make_embedder("voyage", VOCABULARY, settings=none)
    with pytest.raises(EmbeddingServiceError, match="needs OPENAI_API_KEY"):
        make_embedder("openai", VOCABULARY, settings=none)
    with pytest.raises(EmbeddingServiceError, match="no model option"):
        make_embedder("hashing", VOCABULARY, model="x")


def test_hosted_embedders_make_search_declare_the_network_permission() -> None:
    with (
        open_research(":memory:", embedder=_voyage(FakeApi())) as hosted,
        open_research(":memory:", vector=True) as local,
    ):
        hosted_search = research_tools(hosted.research)[0]
        local_search = research_tools(local.research)[0]

    assert Permission.NETWORK in hosted_search.permissions
    assert Permission.NETWORK not in local_search.permissions


def test_end_to_end_with_a_simulated_voyage_api(corpus_dir: Path) -> None:
    api = FakeApi()
    with open_research(":memory:", embedder=_voyage(api)) as research:
        result = research.ingest.ingest(corpus_dir)
        packet = research.research.find_related(ResearchQuery(query="betting against beta"))

    assert result.embedded == result.chunks
    assert result.snapshot.extractor_versions["embedder"] == "voyage:voyage-finance-2:1024"
    assert {request["input_type"] for request in api.requests} == {"document", "query"}
    assert any("vector" in passage.channels for passage in packet.passages)


def test_searching_with_a_model_the_store_was_not_built_with_warns(corpus_dir: Path) -> None:
    with open_research(":memory:", vector=True) as research:
        research.ingest.ingest(corpus_dir)
    with open_research(":memory:", embedder=_voyage(FakeApi())) as other:
        other.ingest.store.upsert_entities(VOCABULARY.entities)
        packet = other.research.find_related(ResearchQuery(query="carry"))

    assert any("No embeddings for voyage:voyage-finance-2:1024" in w for w in packet.warnings)


def test_the_urllib_transport_returns_statuses_and_retry_after() -> None:
    """Exercise the real HTTP transport against a loopback server (no external network)."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            limited = self.path == "/limited"
            self.send_response(429 if limited else 200)
            if limited:
                self.send_header("Retry-After", "3")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        ok = urllib_transport(urllib.request.Request(f"{base}/ok", data=b"{}"), 5)
        limited = urllib_transport(urllib.request.Request(f"{base}/limited", data=b"{}"), 5)
    finally:
        server.shutdown()
        server.server_close()

    assert (ok.status, ok.body) == (200, b'{"ok": true}')
    assert (limited.status, limited.retry_after) == (429, 3.0)


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_query_embedding_respects_the_deadline() -> None:
    clock = FakeClock()
    timeouts: list[float] = []

    def failing(request: urllib.request.Request, timeout: float) -> HttpResponse:
        timeouts.append(timeout)
        clock.now += 1.0
        return HttpResponse(503, b"")

    embedder = VoyageEmbedder(KEY, transport=failing, sleep=clock.sleep, clock=clock, timeout=30)

    with pytest.raises(EmbeddingServiceError, match=r"time left in the budget|time budget"):
        embedder.embed(["carry"], kind="query", deadline=clock.now + 4.0)
    assert timeouts[0] == 4.0  # the request timeout shrinks to the time left
    assert clock.now <= 104.0 + 1.0


def test_search_degrades_to_other_channels_when_embedding_fails(corpus_dir: Path) -> None:
    api = FakeApi()
    with open_research(":memory:", embedder=_voyage(api)) as research:
        research.ingest.ingest(corpus_dir)
        api.failures = [HttpResponse(401, b"{}")]
        packet = research.research.find_related(ResearchQuery(query="carry"))

    assert packet.passages
    assert any(w.startswith("Vector channel skipped") for w in packet.warnings)


def test_repeated_queries_do_not_call_the_provider_again(corpus_dir: Path) -> None:
    api = FakeApi()
    with open_research(":memory:", embedder=_voyage(api)) as research:
        research.ingest.ingest(corpus_dir)
        before = len(api.requests)
        for _ in range(3):
            research.research.find_related(ResearchQuery(query="carry"))

    assert len(api.requests) == before + 1


def test_the_search_tool_timeout_covers_the_query_budget() -> None:
    from quant_research.application.retrieval import QUERY_EMBEDDING_SECONDS
    from quant_research.tools import SEARCH_TIMEOUT

    with open_research(":memory:", embedder=_voyage(FakeApi())) as research:
        search = research_tools(research.research)[0]

    assert search.timeout_seconds == SEARCH_TIMEOUT > QUERY_EMBEDDING_SECONDS
