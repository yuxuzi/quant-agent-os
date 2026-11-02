"""The REST interface (B-18, decision 0011): same tools and results as MCP, behind scoped keys."""

import os
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from portfolio_tools import build_registry
from pydantic import BaseModel

from quant_agent_os.api import (
    ApiKey,
    ApiKeyError,
    ApiUnavailableError,
    KeyRing,
    add_key,
    build_app,
    hash_key,
    is_loopback,
    load_keys,
)
from quant_agent_os.mcp import call_tool, tool_definitions
from quant_agent_os.runs import MemoryRunStore, RunRecorder
from quant_agent_os.runtime import PlanStep, ToolExecutor
from quant_agent_os.tools import READ_ONLY, Permission, PermissionPolicy, ToolRegistry, tool

ARGUMENTS = {"series_id": "s", "returns": [0.01, -0.02, 0.03], "periods_per_year": 12}


class Query(BaseModel):
    url: str


class Note(BaseModel):
    text: str


@tool(name="fetch_page", description="Needs the network.", permissions=[Permission.NETWORK])
def fetch_page(params: Query) -> Query:
    return params


@tool(name="save_note", description="Writes a file.", permissions=[Permission.WRITE_LOCAL_DATA])
def save_note(params: Note) -> Note:
    return params


REGISTRY = ToolRegistry([*build_registry().specs(), fetch_page, save_note])
EXECUTOR = ToolExecutor(REGISTRY, PermissionPolicy())


@pytest.fixture
def keys_file(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    path = tmp_path / "keys.toml"
    issued = {
        "alice": add_key(path, "alice", ["tools:list", "tools:call", "runs:read"]),
        "lister": add_key(path, "lister", ["tools:list"]),
    }
    return path, issued


@pytest.fixture
def store() -> MemoryRunStore:
    return MemoryRunStore()


@pytest.fixture
def client(keys_file: tuple[Path, dict[str, str]], store: MemoryRunStore) -> TestClient:
    path, _ = keys_file
    return TestClient(build_app(REGISTRY, KeyRing(load_keys(path)), store))


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def test_health_needs_no_key_and_no_schema_pages_are_public(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_requests_without_a_valid_key_are_refused(
    client: TestClient, keys_file: tuple[Path, dict[str, str]]
) -> None:
    _, issued = keys_file
    missing = client.get("/v1/tools")
    wrong = client.get("/v1/tools", headers=_auth("qaos_not-a-key"))
    scoped = client.post(
        "/v1/tools/max_drawdown/call",
        headers=_auth(issued["lister"]),
        json={"arguments": ARGUMENTS},
    )

    assert missing.status_code == wrong.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert "qaos_not-a-key" not in wrong.text
    assert scoped.status_code == 403 and "tools:call" in scoped.json()["detail"]
    assert client.get("/v1/runs", headers=_auth(issued["lister"])).status_code == 403


def test_tool_list_and_results_equal_mcp_and_direct_calls(
    client: TestClient, keys_file: tuple[Path, dict[str, str]]
) -> None:
    _, issued = keys_file
    listed = client.get("/v1/tools", headers=_auth(issued["lister"])).json()
    response = client.post(
        "/v1/tools/annualized_return/call",
        headers=_auth(issued["alice"]),
        json={"arguments": ARGUMENTS},
    )

    direct = EXECUTOR.execute(PlanStep(tool="annualized_return", arguments=ARGUMENTS))
    via_mcp = call_tool(EXECUTOR, "annualized_return", ARGUMENTS)
    assert listed == tool_definitions(EXECUTOR)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["error"] is None
    assert body["output"] == direct.output == via_mcp["structuredContent"]
    assert body["run_id"] and body["call_id"]


def test_tool_failures_are_results_with_the_executor_status(
    client: TestClient, keys_file: tuple[Path, dict[str, str]]
) -> None:
    _, issued = keys_file

    def status(name: str, arguments: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        response = client.post(
            f"/v1/tools/{name}/call", headers=_auth(issued["alice"]), json={"arguments": arguments}
        )
        return response.status_code, response.json()

    invalid = status("max_drawdown", {"series_id": "s", "returns": []})
    denied = status("fetch_page", {"url": "https://example.com"})

    mcp_invalid = call_tool(EXECUTOR, "max_drawdown", {"series_id": "s", "returns": []})
    assert invalid[0] == 200
    assert {key: invalid[1][key] for key in ("status", "error", "hint", "retryable")} == (
        mcp_invalid["structuredContent"]
    )
    assert denied[0] == 200 and denied[1]["status"] == "denied"
    assert denied[1]["retryable"] is False


def test_unknown_tools_and_bad_bodies_are_refused_without_a_run(
    keys_file: tuple[Path, dict[str, str]], store: MemoryRunStore
) -> None:
    path, issued = keys_file
    client = TestClient(build_app(REGISTRY, KeyRing(load_keys(path)), store, max_request_bytes=64))
    headers = _auth(issued["alice"])

    unknown = client.post("/v1/tools/place_trade/call", headers=headers, json={"arguments": {}})
    not_wrapped = client.post("/v1/tools/max_drawdown/call", headers=headers, json={"x": 1})
    not_json = client.post("/v1/tools/max_drawdown/call", headers=headers, content=b"[1, 2")
    too_large = client.post(
        "/v1/tools/max_drawdown/call", headers=headers, json={"arguments": ARGUMENTS}
    )

    assert unknown.status_code == 404
    assert not_wrapped.status_code == not_json.status_code == 422
    assert too_large.status_code == 413
    assert store.list_runs() == []


def test_runs_record_the_rest_actor_and_the_keys_principal(
    client: TestClient, keys_file: tuple[Path, dict[str, str]]
) -> None:
    _, issued = keys_file
    headers = _auth(issued["alice"])
    run_id = client.post(
        "/v1/tools/max_drawdown/call", headers=headers, json={"arguments": ARGUMENTS}
    ).json()["run_id"]

    runs = client.get("/v1/runs", headers=headers).json()
    stored = client.get(f"/v1/runs/{run_id}", headers=headers).json()

    assert [(run["actor"], run["principal"]) for run in runs] == [("rest", "alice")]
    assert stored["events"] and {event["principal"] for event in stored["events"]} == {"alice"}
    assert client.get("/v1/runs/nope", headers=headers).status_code == 404


def test_consequential_calls_wait_for_an_operator_approval_no_key_can_give(
    keys_file: tuple[Path, dict[str, str]], store: MemoryRunStore
) -> None:
    path, issued = keys_file
    policy = PermissionPolicy(granted=READ_ONLY | {Permission.WRITE_LOCAL_DATA})
    client = TestClient(build_app(REGISTRY, KeyRing(load_keys(path)), store, policy=policy))
    headers = _auth(issued["alice"])

    waiting = client.post(
        "/v1/tools/save_note/call", headers=headers, json={"arguments": {"text": "hi"}}
    ).json()
    run_id = waiting["run_id"]
    approve = client.post(f"/v1/runs/{run_id}/approve", headers=headers)

    assert waiting["status"] == "approval_required" and waiting["output"] is None
    assert approve.status_code in (404, 405)
    operator = RunRecorder(store, actor="sdk")
    operator.approve(run_id, approved_by="operator")
    (resumed,) = operator.resume(ToolExecutor(REGISTRY, policy, approvals=store), run_id)
    assert resumed.status == "ok"
    assert client.get(f"/v1/runs/{run_id}", headers=headers).json()["run"]["status"] == (
        "completed"
    )


def test_the_app_needs_an_enabled_key(tmp_path: Path, store: MemoryRunStore) -> None:
    revoked = ApiKey(principal="old", sha256=hash_key("k"), scopes={"tools:list"}, disabled=True)

    with pytest.raises(ValueError, match="at least one enabled API key"):
        build_app(REGISTRY, KeyRing([revoked]), store)
    assert KeyRing([revoked]).authenticate("k") is None


def test_keys_are_stored_only_as_hashes_in_a_private_file(
    keys_file: tuple[Path, dict[str, str]],
) -> None:
    path, issued = keys_file
    text = path.read_text("utf-8")

    assert all(key.startswith("qaos_") and key not in text for key in issued.values())
    assert hash_key(issued["alice"]) in text
    loaded = {key.principal: key for key in load_keys(path)}
    assert loaded["lister"].scopes == {"tools:list"}
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


def test_bad_key_files_and_principals_are_refused(tmp_path: Path) -> None:
    broken = tmp_path / "broken.toml"
    broken.write_text('[[keys]]\nprincipal = "a"\nsha256 = "xyz"\nscopes = ["tools:list"]\n')
    twice = tmp_path / "twice.toml"
    entry = f'[[keys]]\nprincipal = "a"\nsha256 = "{"0" * 64}"\nscopes = ["runs:read"]\n'
    twice.write_text(entry + entry)

    with pytest.raises(ApiKeyError, match="api-key new"):
        load_keys(tmp_path / "absent.toml")
    with pytest.raises(ApiKeyError, match="Invalid API keys file"):
        load_keys(broken)
    with pytest.raises(ApiKeyError, match="appears twice"):
        load_keys(twice)
    with pytest.raises(ApiKeyError):
        add_key(broken, "bob", ["tools:list"])
    with pytest.raises(ValueError, match="principal"):
        add_key(tmp_path / "keys.toml", 'bob"\nscopes = ["runs:read"]', ["tools:list"])


def test_loopback_detection() -> None:
    assert is_loopback("127.0.0.1") and is_loopback("::1") and is_loopback("localhost")
    assert not is_loopback("0.0.0.0") and not is_loopback("example.com")


def test_a_missing_extra_gives_the_install_hint(
    monkeypatch: pytest.MonkeyPatch, store: MemoryRunStore
) -> None:
    monkeypatch.setitem(sys.modules, "fastapi", None)

    with pytest.raises(ApiUnavailableError, match="quant-agent-os\\[api\\]"):
        build_app(REGISTRY, KeyRing([]), store)
