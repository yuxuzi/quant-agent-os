"""`call_service(..., forward_caller=True)`: the service learns who called (decision 0030).

A loopback service checks each call's `X-QAOS-Caller` with `verify_caller` and scopes its answer
to the caller's groups, as a firm's risk service would scope VaR to a PM's own desk.
"""

import json
import logging
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from quant_agent_os.registration import (
    CALLER_HEADER,
    CallerAssertionError,
    FirmApp,
    sign_caller,
    verify_caller,
)
from quant_agent_os.runs import SqliteRunStore
from quant_agent_os.runtime.call import ToolCall
from quant_agent_os.runtime.executor import ToolCallRecord, ToolExecutor
from quant_agent_os.sdk import Platform
from quant_agent_os.testing import check_app
from quant_agent_os.tools import (
    Caller,
    Permission,
    PermissionPolicy,
    ToolContractError,
    ToolRegistry,
)
from quant_agent_os.tools.access import acting_as

SECRET = "kestrel-risk-caller-secret-0123456789abcdef"
SECRET_ENV = "RISK_CALLER_SECRET"
DESK_VAR = {"equity": 1.2, "macro": 3.4, "commodities": 5.6}
PM = Caller("pm.equity", groups=frozenset({"equity"}))
RISK = Caller("risk.lead", groups=frozenset({"equity", "macro", "commodities"}))


@dataclass
class Seen:
    """What the service received and how it judged it."""

    assertions: list[str | None] = field(default_factory=list)  # each request's X-QAOS-Caller
    authorized: list[bool] = field(default_factory=list)  # whether it carried a bearer token
    refusals: list[str] = field(default_factory=list)


@pytest.fixture
def service() -> Iterator[tuple[str, Seen]]:
    """A firm's risk service that answers only for the caller's own desks."""
    seen = Seen()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            seen.assertions.append(self.headers.get(CALLER_HEADER))  # any case
            seen.authorized.append("Authorization" in self.headers)
            try:
                caller = verify_caller(self.headers.get(CALLER_HEADER), SECRET, tool="desk_var")
            except CallerAssertionError as error:
                seen.refusals.append(type(error).__name__)
                self.send_response(401)
                self.end_headers()
                return
            answer = {desk: DESK_VAR[desk] for desk in sorted(caller.groups) if desk in DESK_VAR}
            body = json.dumps({"principal": caller.principal, "var": answer}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", seen
    server.shutdown()
    server.server_close()


def desk_var() -> dict[str, Any]:
    """Value at risk for each desk the caller may see, from the risk service."""
    raise NotImplementedError  # never runs: the service answers


def _app(url: str, **options: Any) -> FirmApp:
    app = FirmApp("risk")
    options = {"forward_caller": True, "caller_secret_env": SECRET_ENV, **options}
    app.call_service(desk_var, url=f"{url}/desk_var", **options)
    return app


def _executor(app: FirmApp) -> ToolExecutor:
    policy = PermissionPolicy(
        granted=frozenset(Permission),
        preapproved_tools={Permission.NETWORK: frozenset({"desk_var"})},
    )
    return ToolExecutor(ToolRegistry(app.tools()), policy)


def _call(app: FirmApp, caller: Caller | None) -> ToolCallRecord:
    with acting_as(caller):
        return _executor(app).execute(ToolCall(tool="desk_var", arguments={}))


@pytest.fixture
def secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SECRET_ENV, SECRET)


@pytest.mark.usefixtures("secret")
def test_the_service_verifies_the_caller_and_scopes_its_answer(
    service: tuple[str, Seen],
) -> None:
    url, seen = service
    app = _app(url)

    pm = _call(app, PM)
    risk = _call(app, RISK)

    assert pm.status == "ok", pm.error_detail
    assert pm.output is not None and pm.output["result"] == {
        "principal": "pm.equity",
        "var": {"equity": 1.2},
    }
    assert risk.output is not None and risk.output["result"]["var"] == DESK_VAR
    assert seen.refusals == []
    header = seen.assertions[0]
    assert header is not None and header.startswith("v1.") and seen.assertions[1] != header
    assert seen.authorized == [False, False]


@pytest.mark.usefixtures("secret")
def test_a_call_with_no_caller_is_refused_before_anything_is_sent(
    service: tuple[str, Seen],
) -> None:
    url, seen = service

    record = _call(_app(url), None)

    assert record.status == "error"
    assert "no authenticated caller" in (record.error_detail or "")
    assert seen.assertions == []  # never an anonymous assertion


def test_a_missing_or_short_secret_refuses_the_call(
    service: tuple[str, Seen], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, seen = service
    monkeypatch.delenv(SECRET_ENV, raising=False)

    missing = _call(_app(url), PM)
    monkeypatch.setenv(SECRET_ENV, "too-short")
    short = _call(_app(url), PM)

    assert missing.status == short.status == "error"
    assert f"Set {SECRET_ENV} (or {SECRET_ENV}_FILE) to the secret" in (missing.error_detail or "")
    assert f"{SECRET_ENV} must hold at least 32 bytes" in (short.error_detail or "")
    assert "too-short" not in (short.error_detail or "") + (short.error or "")
    assert seen.assertions == []


def test_the_service_refuses_an_assertion_under_another_secret(
    service: tuple[str, Seen], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, seen = service
    monkeypatch.setenv(SECRET_ENV, "x" * 40)

    record = _call(_app(url), PM)

    assert record.status == "error"
    assert "401" in (record.error_detail or "")
    assert seen.refusals == ["BadSignatureError"]


@pytest.mark.usefixtures("secret")
def test_the_service_refuses_an_assertion_for_another_tool(service: tuple[str, Seen]) -> None:
    url, seen = service
    app = FirmApp("risk")

    def desk_stress() -> dict[str, Any]:
        """Stress results from the risk service."""
        raise NotImplementedError

    app.call_service(
        desk_stress,
        url=f"{url}/desk_var",  # the endpoint that answers only for desk_var
        forward_caller=True,
        caller_secret_env=SECRET_ENV,
    )
    policy = PermissionPolicy(
        granted=frozenset(Permission), preapproved=frozenset({Permission.NETWORK})
    )
    with acting_as(PM):
        record = ToolExecutor(ToolRegistry(app.tools()), policy).execute(
            ToolCall(tool="desk_stress", arguments={})
        )

    assert record.status == "error"
    assert seen.refusals == ["WrongToolError"]


@pytest.mark.usefixtures("secret")
def test_the_secret_never_reaches_output_logs_or_run_records(
    service: tuple[str, Seen],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    url, seen = service
    app = _app(url)
    caplog.set_level(logging.DEBUG)
    store = SqliteRunStore(tmp_path / "runs.db")
    policy = PermissionPolicy(
        granted=frozenset(Permission),
        preapproved_tools={Permission.NETWORK: frozenset({"desk_var"})},
    )
    with Platform(app.tools(), policy=policy, run_store=store) as platform:
        with acting_as(PM):
            ok = platform.call("desk_var")
        refused = platform.call("desk_var")  # no caller: refused
        runs = [platform.get_run(r.run_id or "") for r in (ok, refused)]

    assert ok.status == "ok" and refused.status == "error"
    (header,) = seen.assertions
    assert header is not None
    shown = " ".join(
        [
            ok.model_dump_json(),
            refused.model_dump_json(),
            *(run.model_dump_json() for run in runs if run is not None),
            caplog.text,
            *capsys.readouterr(),
            (tmp_path / "runs.db").read_bytes().decode("latin-1"),
        ]
    )
    assert SECRET not in shown
    assert header not in shown  # the assertion is a credential for 60 s: never recorded


def test_registration_refuses_a_forwarded_caller_it_cannot_send_safely() -> None:
    def stub() -> float:
        """A figure from a service."""
        raise NotImplementedError

    def register(url: str = "https://risk.internal/var", **options: Any) -> None:
        FirmApp("risk").call_service(stub, url=url, **options)

    with pytest.raises(ToolContractError, match="needs caller_secret_env"):
        register(forward_caller=True)
    with pytest.raises(ToolContractError, match="does nothing without forward_caller"):
        register(caller_secret_env=SECRET_ENV)
    with pytest.raises(ToolContractError, match="only over https"):
        register("http://risk.internal/var", forward_caller=True, caller_secret_env=SECRET_ENV)
    with pytest.raises(ToolContractError, match="must differ"):
        register(forward_caller=True, caller_secret_env=SECRET_ENV, token_env=SECRET_ENV)
    with pytest.raises(ToolContractError, match="name an environment variable") as raised:
        register(forward_caller=True, caller_secret_env=SECRET)
    assert SECRET not in str(raised.value)
    for loopback in ("http://127.0.0.1:8100/var", "http://localhost/var", "http://[::1]/var"):
        register(loopback, forward_caller=True, caller_secret_env=SECRET_ENV)
    register(forward_caller=True, caller_secret_env=SECRET_ENV)  # https off loopback


@pytest.mark.usefixtures("secret")
def test_check_app_can_act_as_a_caller(service: tuple[str, Seen]) -> None:
    url, _ = service
    app = _app(url)

    (record,) = check_app(app, {"desk_var": {}}, caller=PM)

    assert record.output is not None and record.output["result"]["var"] == {"equity": 1.2}
    with pytest.raises(ToolContractError, match="internal error"):
        check_app(app, {"desk_var": {}})


def test_a_firms_test_can_sign_for_its_own_service() -> None:
    header = sign_caller(SECRET, principal="pm.macro", tenant="local", groups=["macro"], tool="t")

    assert verify_caller(header, SECRET, tool="t").groups == {"macro"}
