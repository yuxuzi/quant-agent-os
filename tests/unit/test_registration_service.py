"""A firm's internal service as a tool (decision 0024): `FirmApp.call_service`, over loopback."""

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Literal

import pytest

from quant_agent_os.data import Dataset, UnknownDatasetError, parse_dataset
from quant_agent_os.registration import FirmApp, Frame, Series
from quant_agent_os.runtime.call import ToolCall
from quant_agent_os.runtime.executor import ToolCallRecord, ToolExecutor
from quant_agent_os.testing import check_tool
from quant_agent_os.tools import Permission, PermissionPolicy, ToolContractError, ToolRegistry

DESKS = "date,growth\n2024-01-31,0.02\n2024-02-29,nan\n2024-03-31,-0.04\n"
GROWTH = {"dataset": "desks", "column": "growth"}


class Store:
    def get(self, dataset_id: str) -> Dataset:
        if dataset_id != "desks":
            raise UnknownDatasetError(f"No dataset '{dataset_id}'.")
        return parse_dataset("desks", DESKS.encode())

    def ids(self) -> list[str]:
        return ["desks"]


@dataclass
class Exchange:
    """What the service received, and what it answers."""

    status: int = 200
    answer: bytes = b"{}"
    headers: dict[str, str] = field(default_factory=dict)
    requests: list[dict[str, Any]] = field(default_factory=list)


@pytest.fixture
def service() -> Iterator[tuple[str, Exchange]]:
    exchange = Exchange()

    class Handler(BaseHTTPRequestHandler):
        def _answer(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            exchange.requests.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "headers": dict(self.headers),
                    "body": json.loads(self.rfile.read(length)) if length else None,
                }
            )
            self.send_response(exchange.status)
            for name, value in exchange.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(exchange.answer)

        do_GET = do_POST = _answer  # noqa: N815 (the names http.server calls)

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", exchange
    server.shutdown()
    server.server_close()


def _run(app: FirmApp, arguments: dict[str, Any], *, granted: bool = True) -> ToolCallRecord:
    (spec,) = app.tools(Store())
    policy = PermissionPolicy(
        granted=frozenset(Permission) if granted else frozenset({Permission.READ_LOCAL_DATA}),
        preapproved=frozenset({Permission.NETWORK}),
    )
    return ToolExecutor(ToolRegistry([spec]), policy).execute(
        ToolCall(tool=spec.name, arguments=arguments)
    )


@dataclass
class Limits:
    """A desk's risk limits."""

    var_limit: float
    currency: str


def desk_limits(desk: str, as_of: str | None = None) -> Limits:
    """A desk's risk limits, from the limits service.

    Args:
        desk: The desk's code, such as "growth".
        as_of: A date, such as 2024-03-31; default: today.
    """
    raise NotImplementedError  # never runs: the service answers


def desk_var(returns: list[float], confidence: float = 0.95) -> float:
    """A desk's value at risk, from the risk engine."""
    raise NotImplementedError


def test_a_get_service_becomes_a_tool_that_needs_network(
    service: tuple[str, Exchange],
) -> None:
    url, exchange = service
    exchange.answer = b'{"var_limit": 1000000.0, "currency": "USD"}'
    app = FirmApp("risk")
    app.call_service(desk_limits, url=f"{url}/desks/{{desk}}/limits", method="GET")

    (spec,) = app.tools()
    record = _run(app, {"desk": "growth/a", "as_of": "2024-03-31"})

    assert Permission.NETWORK in spec.permissions
    assert spec.description == "A desk's risk limits, from the limits service."
    assert spec.input_model.model_json_schema()["required"] == ["desk"]
    assert record.status == "ok" and record.output is not None, record.error
    assert exchange.requests[0]["method"] == "GET"
    assert exchange.requests[0]["path"] == "/desks/growth%2Fa/limits?as_of=2024-03-31"
    assert record.output["var_limit"] == 1_000_000.0 and record.output["currency"] == "USD"
    (evidence,) = record.output["evidence"]
    assert evidence["kind"] == "retrieved"
    assert evidence["statement"] == (
        f"desk_limits retrieved its result from the service at {url}/desks/{{desk}}/limits."
    )
    assert record.output["code_version"] is None


def test_data_references_are_read_and_sent_as_values(service: tuple[str, Exchange]) -> None:
    url, exchange = service
    exchange.answer = b"0.04"
    app = FirmApp("risk")
    app.call_service(desk_var, url=f"{url}/var", returns=Series, convention="Historical")

    record = _run(app, {"returns": GROWTH})

    assert record.status == "ok" and record.output is not None, record.error
    assert exchange.requests[0]["body"] == {"returns": [0.02, None, -0.04], "confidence": 0.95}
    assert exchange.requests[0]["headers"]["Content-Type"] == "application/json"
    assert record.output["result"] == 0.04
    assert record.output["warnings"] == ["desks.growth has 1 missing values, sent as null."]
    assert record.output["data_versions"].keys() == {"dataset:desks"}
    (evidence,) = record.output["evidence"]
    assert evidence["statement"] == (
        f"desk_var retrieved 0.04 from the service at {url}/var, given desks.growth, "
        "by the convention: Historical."
    )
    assert [source["source_id"] for source in evidence["sources"]] == ["desks.growth", f"{url}/var"]


def test_the_token_comes_from_the_environment_and_is_never_shown(
    service: tuple[str, Exchange], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, exchange = service
    exchange.status, exchange.answer = 403, b'{"detail": "no"}'
    app = FirmApp("risk")
    app.call_service(desk_var, url=f"{url}/var", token_env="RISK_TOKEN", returns=Series)

    missing = _run(app, {"returns": GROWTH})
    monkeypatch.setenv("RISK_TOKEN", "s3cret-value")
    refused = _run(app, {"returns": GROWTH})

    assert missing.error_detail == f"ServiceError: Set RISK_TOKEN to the token for {url}/var."
    assert exchange.requests[0]["headers"]["Authorization"] == "Bearer s3cret-value"
    assert refused.error_detail == (
        f"ServiceError: The service at {url}/var answered 403 Forbidden."
    )
    assert refused.error is not None and url not in refused.error  # the caller sees no host
    assert "s3cret" not in json.dumps([missing.model_dump(), refused.model_dump()], default=str)


@pytest.mark.parametrize(
    ("status", "answer", "headers", "expected"),
    [
        (302, b"{}", {"Location": "http://example.org/"}, "answered 302 Found; redirects"),
        (200, b"not json", {}, "did not answer with JSON."),
        (200, b"[" + b"0," * 100 + b"0]", {}, "answered with more than 64 bytes."),
        (200, b'"high"', {}, "Input should be a valid number"),
    ],
)
def test_a_bad_answer_is_recorded_as_an_error(
    service: tuple[str, Exchange],
    status: int,
    answer: bytes,
    headers: dict[str, str],
    expected: str,
) -> None:
    url, exchange = service
    exchange.status, exchange.answer, exchange.headers = status, answer, headers
    app = FirmApp("risk")
    app.call_service(desk_var, url=f"{url}/var", returns=Series, max_output_bytes=64)

    record = _run(app, {"returns": GROWTH})

    assert record.status == "error"
    assert expected in (record.error_detail or record.error or "")
    assert len(exchange.requests) == 1  # the redirect was not followed


def test_an_unreachable_service_is_explained() -> None:
    app = FirmApp("risk")
    # Windows retries a refused connection for about two seconds, so keep the default timeout.
    app.call_service(desk_var, url="http://127.0.0.1:9/var", returns=Series)

    record = _run(app, {"returns": GROWTH})

    assert record.status == "error" and record.error_detail is not None
    assert record.error_detail.startswith(
        "ServiceError: Cannot reach the service at http://127.0.0.1:9/var:"
    )


def test_without_the_network_permission_the_call_is_refused(
    service: tuple[str, Exchange],
) -> None:
    url, exchange = service
    app = FirmApp("risk")
    app.call_service(desk_var, url=f"{url}/var", returns=Series)

    record = _run(app, {"returns": GROWTH}, granted=False)

    assert record.status == "denied"
    assert exchange.requests == []


def by_code(desk: Literal["growth", "value"], days: list[int]) -> float:
    """A desk's figure."""
    raise NotImplementedError


def frame_stub(prices: dict[str, list[float]]) -> float:
    """A figure from prices."""
    raise NotImplementedError


@pytest.mark.parametrize(
    ("stub", "options", "message"),
    [
        (desk_var, {"url": "ftp://risk/var"}, "must be an http or https address"),
        (desk_var, {"url": "https://user:pw@risk/var"}, "no credentials"),
        (desk_var, {"url": "https://{desk}.risk/var"}, "host must be fixed"),
        (desk_var, {"url": "http://risk.internal/var", "token_env": "T"}, "only over https"),
        (desk_var, {"url": "https://risk/{desk}"}, "the url names desk"),
        (desk_var, {"url": "https://r/v", "method": "GET", "returns": Series}, "'returns' cannot"),
        (frame_stub, {"url": "https://risk/p", "method": "GET", "prices": Frame}, "'prices'"),
        (desk_var, {"url": "https://risk/var", "method": "PUT"}, "method must be GET or POST"),
    ],
)
def test_a_wrong_endpoint_stops_registration(
    stub: Any, options: dict[str, Any], message: str
) -> None:
    with pytest.raises(ToolContractError, match=message):
        FirmApp("risk").call_service(stub, **options)


def test_literals_and_lists_go_in_a_query_and_check_tool_passes(
    service: tuple[str, Exchange],
) -> None:
    url, exchange = service
    exchange.answer = b"1.5"
    app = FirmApp("risk", prefix=True)
    app.call_service(by_code, url=f"{url}/figure?v=2", method="GET")
    app.call_service(desk_var, url="http://localhost/var", token_env="T", returns=Series)

    (spec, _) = app.tools(Store())
    record = check_tool(spec, {"desk": "value", "days": [1, 5]})

    assert spec.name == "risk_by_code" and not spec.deterministic and spec.idempotent
    assert record.output is not None and record.output["result"] == 1.5
    assert exchange.requests[0]["path"] == "/figure?v=2&desk=value&days=1&days=5"
