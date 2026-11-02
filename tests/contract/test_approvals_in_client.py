"""Approving a consequential call without leaving the client (B-35, decision 0014).

Over HTTP, a waiting call sends the client to the approval page; an approver key (never the
agent's) approves it once; the retried call runs once, in its original run. Locally, a stdio
server can ask the client to confirm instead, only when the operator opts in.
"""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from mcp import types
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import BaseModel

from quant_agent_os.api import ApiKeyError, KeyRing, add_key, build_app, load_keys
from quant_agent_os.api.approval_page import approval_routes
from quant_agent_os.approval_desk import ApprovalDesk
from quant_agent_os.mcp import build_server
from quant_agent_os.mcp.http import KeyVerifier, add_mcp_route
from quant_agent_os.runs import MemoryRunStore, RunRecorder
from quant_agent_os.runtime import ToolExecutor
from quant_agent_os.serving import Surface
from quant_agent_os.tools import Permission, PermissionPolicy, ToolRegistry, tool

BASE = "http://127.0.0.1:8000"
WRITES: list[str] = []


class Note(BaseModel):
    text: str
    api_token: str = "none"


@tool(name="save_note", description="Writes a note.", permissions=[Permission.WRITE_LOCAL_DATA])
def save_note(params: Note) -> Note:
    WRITES.append(params.text)
    return params


REGISTRY = ToolRegistry([save_note])
ELIGIBLE = PermissionPolicy(
    granted=frozenset({Permission.READ_LOCAL_DATA, Permission.WRITE_LOCAL_DATA})
)


class Tenants:
    def __init__(self, store: MemoryRunStore) -> None:
        executor = ToolExecutor(REGISTRY, ELIGIBLE, approvals=store)
        self.surfaces = {name: Surface(executor, run_store=store) for name in ("local", "acme")}

    def surface(self, tenant: str) -> Surface | None:
        return self.surfaces.get(tenant)


@pytest.fixture
def server(tmp_path: Path) -> tuple[Any, dict[str, str], MemoryRunStore, ApprovalDesk]:
    WRITES.clear()
    path = tmp_path / "keys.toml"
    issued = {
        "agent": add_key(path, "ann", ["tools:list", "tools:call"], tenant="acme"),
        "self": add_key(path, "ann", ["runs:approve"], tenant="acme"),
        "approver": add_key(path, "bo", ["runs:approve"], tenant="acme"),
        "outsider": add_key(path, "cy", ["runs:approve"], tenant="local"),
        "local-agent": add_key(path, "dee", ["tools:list", "tools:call"]),
        "local-self": add_key(path, "dee", ["runs:approve"]),
    }
    keys = KeyRing(load_keys(path))
    store = MemoryRunStore()
    desk = ApprovalDesk(store, keys, base_url=BASE)
    tenancy = Tenants(store)
    app = build_app(None, keys, None, tenancy=tenancy)
    app.router.routes.extend(approval_routes(desk))
    add_mcp_route(app, tenancy, name="test", verifier=KeyVerifier(keys), desk=desk)
    return app, issued, store, desk


@asynccontextmanager
async def _http(app: Any, headers: dict[str, str]) -> AsyncIterator[httpx.AsyncClient]:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=BASE, headers=headers
        ) as client,
    ):
        yield client


def _call(app: Any, key: str, text: str = "hello") -> Any:
    """Call save_note over MCP; return the result, or the elicitation error's data."""

    async def main() -> Any:
        async with (
            _http(app, {"Authorization": f"Bearer {key}"}) as client,
            streamable_http_client(f"{BASE}/mcp", http_client=client) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            try:
                return await session.call_tool(
                    "save_note", {"text": text, "api_token": "s3cret-value"}
                )
            except McpError as error:
                return error.error

    return anyio.run(main)


def _page(app: Any, url: str, form: dict[str, str] | None = None) -> httpx.Response:
    async def main() -> httpx.Response:
        async with _http(app, {}) as client:
            if form is None:
                return await client.get(url)
            return await client.post(url, data=form)

    return anyio.run(main)


def _token(page: httpx.Response) -> str:
    found = re.search(r"name=token value=([0-9a-f]+)", page.text)
    assert found is not None
    return found.group(1)


def test_a_waiting_call_is_approved_on_the_page_and_runs_once_in_its_run(server: Any) -> None:
    app, issued, store, _ = server

    waiting = _call(app, issued["agent"])
    assert waiting.code == types.URL_ELICITATION_REQUIRED
    (elicitation,) = waiting.data["elicitations"]
    url = elicitation["url"]
    page = _page(app, url)
    assert page.status_code == 200 and "save_note" in page.text and "write_local_data" in page.text
    assert "s3cret-value" not in page.text and "[REDACTED]" in page.text
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert page.headers["x-frame-options"] == "DENY"
    assert WRITES == []

    decided = _page(
        app, url, {"token": _token(page), "key": issued["approver"], "decision": "approve"}
    )
    assert decided.status_code == 200 and "Approved" in decided.text
    assert issued["approver"] not in decided.text

    ran = _call(app, issued["agent"])
    again = _call(app, issued["agent"])

    assert ran.isError is False and WRITES == ["hello"]
    assert again.code == types.URL_ELICITATION_REQUIRED  # an approval is used once
    request = store.get_request(elicitation["elicitationId"])
    assert request is not None and request.status == "used" and request.decided_by == "bo"
    stored = store.get_run(request.run_id)
    assert stored is not None and stored.run.status == "completed"
    events = [event.event for event in stored.events]
    assert {"approval.requested", "approval.granted"} <= set(events)


def test_only_an_approver_of_the_same_tenant_and_not_the_requester_can_approve(
    server: Any,
) -> None:
    app, issued, _, _ = server
    url = _call(app, issued["agent"]).data["elicitations"][0]["url"]
    token = _token(_page(app, url))

    def decide(key: str, form_token: str = token) -> httpx.Response:
        return _page(app, url, {"token": form_token, "key": key, "decision": "approve"})

    assert decide(issued["agent"]).status_code == 403  # the agent's own key
    assert decide(issued["outsider"]).status_code == 403  # another tenant's approver
    assert "your own request" in decide(issued["self"]).text  # four eyes outside local
    assert decide(issued["approver"], form_token="0" * 64).status_code == 403  # CSRF
    assert decide("qaos_unknown").status_code == 403
    assert decide(issued["approver"]).status_code == 200
    assert "already approved" in decide(issued["approver"]).text


def test_the_local_tenant_may_approve_its_own_request(server: Any) -> None:
    app, issued, _, _ = server
    url = _call(app, issued["local-agent"]).data["elicitations"][0]["url"]
    token = _token(_page(app, url))

    decided = _page(app, url, {"token": token, "key": issued["local-self"], "decision": "approve"})

    assert decided.status_code == 200
    assert _call(app, issued["local-agent"]).isError is False


def test_denied_changed_or_expired_calls_do_not_run(server: Any) -> None:
    app, issued, store, desk = server
    url = _call(app, issued["agent"], "first").data["elicitations"][0]["url"]
    token = _token(_page(app, url))
    _page(app, url, {"token": token, "key": issued["approver"], "decision": "deny"})

    assert _call(app, issued["agent"], "first").code == types.URL_ELICITATION_REQUIRED
    url = _call(app, issued["agent"], "second").data["elicitations"][0]["url"]
    token = _token(_page(app, url))
    _page(app, url, {"token": token, "key": issued["approver"], "decision": "approve"})
    assert _call(app, issued["agent"], "changed").code == types.URL_ELICITATION_REQUIRED
    assert WRITES == []

    desk.lifetime = timedelta(0)
    url = _call(app, issued["agent"], "late").data["elicitations"][0]["url"]
    expired = _page(app, url)
    assert "can no longer be decided" in expired.text
    assert "approval.denied" in {
        event.event for run in store.list_runs(20) for event in store.get_run(run.run_id).events
    }


def test_an_approver_key_can_never_call_tools(tmp_path: Path) -> None:
    with pytest.raises(ApiKeyError, match="runs:approve cannot also have tools:call"):
        add_key(tmp_path / "keys.toml", "eve", ["tools:call", "runs:approve"])


# Local stdio: confirmation in the client, only when the operator opts in.


def _stdio_call(answer: types.ElicitResult | None, *, confirm: bool = True) -> Any:
    store = MemoryRunStore()
    executor = ToolExecutor(REGISTRY, ELIGIBLE, approvals=store)
    recorder = RunRecorder(store, actor="mcp")
    server = build_server(executor, name="test", recorder=recorder, confirm_in_client=confirm)
    asked: list[str] = []

    async def respond(context: Any, params: types.ElicitRequestParams) -> types.ElicitResult:
        asked.append(params.message)
        assert answer is not None
        return answer

    async def main() -> Any:
        async with create_connected_server_and_client_session(
            server, elicitation_callback=respond
        ) as session:
            return await session.call_tool("save_note", {"text": "local"})

    return anyio.run(main), asked, store


def test_a_local_operator_can_confirm_in_the_client() -> None:
    WRITES.clear()
    result, asked, store = _stdio_call(
        types.ElicitResult(action="accept", content={"approve": True})
    )

    assert result.isError is False and WRITES == ["local"]
    assert asked and "write_local_data" in asked[0]
    (run,) = store.list_runs(5)
    stored = store.get_run(run.run_id)
    assert stored is not None and stored.run.status == "completed"
    granted = [e for e in stored.events if e.event == "approval.granted"]
    assert granted and granted[0].details["approved_by"] == "client-confirmation"


@pytest.mark.parametrize(
    "answer",
    [
        types.ElicitResult(action="decline"),
        types.ElicitResult(action="accept", content={"approve": False}),
    ],
)
def test_a_declined_confirmation_runs_nothing(answer: types.ElicitResult) -> None:
    WRITES.clear()
    result, _, _ = _stdio_call(answer)

    assert result.isError and "approval_required" in result.content[0].text
    assert WRITES == []


def test_without_the_opt_in_nothing_is_asked() -> None:
    WRITES.clear()
    result, asked, _ = _stdio_call(None, confirm=False)

    assert result.isError and asked == [] and WRITES == []


def test_confirmation_is_refused_over_http_and_without_a_run_store() -> None:
    executor = ToolExecutor(REGISTRY, ELIGIBLE)
    with pytest.raises(ValueError, match="stdio with a recorder"):
        build_server(executor, name="test", confirm_in_client=True)
