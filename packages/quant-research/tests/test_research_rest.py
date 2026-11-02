"""`quant-research serve-api` (B-18): research tools over REST, equal to MCP, behind keys."""

from pathlib import Path
from typing import Any

import pytest
from conftest import CORPUS
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from quant_agent_os.api import add_key
from quant_agent_os.mcp import call_tool
from quant_agent_os.runtime import ToolExecutor
from quant_agent_os.tools import PermissionPolicy, ToolRegistry
from quant_research import open_research, research_tools
from quant_research.cli import app as cli

QUERY = {"query": "What is related to short-term reversal?"}


@pytest.fixture
def store(tmp_path: Path) -> str:
    path = str(tmp_path / "knowledge.db")
    CliRunner().invoke(cli, ["ingest", str(CORPUS), "--store", path])
    return path


def _serve(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> tuple[Any, dict[str, Any]]:
    """Run serve-api with uvicorn replaced, returning the result and the app it would serve."""
    served: dict[str, Any] = {}

    def fake_run(app: Any, *, host: str, port: int) -> None:
        served.update(app=app, host=host, port=port)

    monkeypatch.setattr("quant_research.cli.run_http", fake_run)
    return CliRunner().invoke(cli, ["serve-api", *args]), served


def test_serve_api_serves_research_results_equal_to_mcp(
    monkeypatch: pytest.MonkeyPatch, store: str, tmp_path: Path
) -> None:
    keys = tmp_path / "keys.toml"
    key = add_key(keys, "alice", ["tools:list", "tools:call", "runs:read"])
    with open_research(store, read_only=True) as research:
        executor = ToolExecutor(ToolRegistry(research_tools(research.research)), PermissionPolicy())
        expected = call_tool(executor, "find_related_signals", QUERY)["structuredContent"]
    checked: list[str] = []

    def serve(app: Any, *, host: str, port: int) -> None:  # requests while the stores are open
        _check(app, key, expected)
        checked.append(f"{host}:{port}")

    monkeypatch.setattr("quant_research.cli.run_http", serve)
    result = CliRunner().invoke(cli, ["serve-api", "--store", store, "--keys", str(keys)])

    assert result.exit_code == 0, result.output
    assert checked == ["127.0.0.1:8000"]


def _check(app: Any, key: str, expected: dict[str, Any]) -> None:
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {key}"}
    names = [tool["name"] for tool in client.get("/v1/tools", headers=headers).json()]
    body = client.post(
        "/v1/tools/find_related_signals/call", headers=headers, json={"arguments": QUERY}
    ).json()
    run = client.get(f"/v1/runs/{body['run_id']}", headers=headers).json()["run"]

    assert names == ["find_related_signals", "generate_strategy", "get_source_passage"]
    assert body["status"] == "ok" and body["output"] == expected
    assert (run["actor"], run["principal"]) == ("rest", "alice")
    assert run["data_versions"] == {"corpus_snapshot": expected["corpus_snapshot"]}


def test_serve_api_refuses_to_start_without_keys(
    monkeypatch: pytest.MonkeyPatch, store: str, tmp_path: Path
) -> None:
    disabled = tmp_path / "disabled.toml"
    add_key(disabled, "old", ["tools:list"])
    disabled.write_text(disabled.read_text("utf-8") + "disabled = true\n")

    missing, served = _serve(monkeypatch, ["--store", store, "--keys", str(tmp_path / "none")])
    revoked, _ = _serve(monkeypatch, ["--store", store, "--keys", str(disabled)])

    assert missing.exit_code != 0 and "api-key new" in missing.output
    assert revoked.exit_code != 0 and "no enabled key" in revoked.output
    assert served == {}


def test_serve_api_warns_when_binding_beyond_loopback(
    monkeypatch: pytest.MonkeyPatch, store: str, tmp_path: Path
) -> None:
    keys = tmp_path / "keys.toml"
    add_key(keys, "alice", ["tools:list"])

    result, served = _serve(
        monkeypatch, ["--store", store, "--keys", str(keys), "--host", "0.0.0.0"]
    )

    assert result.exit_code == 0 and served["host"] == "0.0.0.0"
    assert "reverse proxy that terminates TLS" in result.output
