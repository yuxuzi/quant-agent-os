"""examples/firm-tools: a firm's tool and data, served beside the platform's toolsets."""

from pathlib import Path
from typing import Any

import anyio
import httpx2
import pytest
from firm_data import SAMPLE_RETURNS, SqliteDatasetStore, create_sample_database
from firm_server import main, risk
from firm_tools import desk_var
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient

from quant_agent_os.api import add_key
from quant_agent_os.config import get_settings
from quant_agent_os.data import UnknownDatasetError
from quant_agent_os.registration import FirmApp
from quant_agent_os.runs import MemoryRunStore, SqliteRunStore
from quant_agent_os.testing import check_app
from quant_agent_os.tools import FileAccessPolicy, Permission, PermissionPolicy
from quant_platform import build_server_app

pytest.importorskip("empyrical")

BASE = "http://127.0.0.1:8000"
GROWTH = {"dataset": "desks", "column": "growth"}
ARGS = {"returns": GROWTH}


@pytest.fixture
def store(tmp_path: Path) -> SqliteDatasetStore:
    create_sample_database(tmp_path / "firm.db")
    return SqliteDatasetStore(tmp_path / "firm.db")


def test_the_data_store_reads_the_firms_database(store: SqliteDatasetStore) -> None:
    assert store.ids() == ["desks"]
    assert store.get("desks").column("growth") == SAMPLE_RETURNS
    assert store.get("desks").periods_per_year == 12
    with pytest.raises(UnknownDatasetError, match="Known: desks"):
        store.get("other")


def test_desk_var_gives_the_known_answer(store: SqliteDatasetStore) -> None:
    # 5% of 24 periods rounds up to rank 2; the two worst months are -3.1% and -2.2%.
    output = desk_var(store.get("desks").column("growth"))

    assert output.var == pytest.approx(0.022)
    assert output.periods == 24
    assert output.warnings == []


def test_desk_var_is_not_off_by_one_from_floating_point(tmp_path: Path) -> None:
    # 5% of 20 periods is rank 1, though (1 - 0.95) * 20 is a little over 1 as a float.
    assert desk_var([-0.10, -0.05, *[0.01] * 18]).var == pytest.approx(0.10)


def test_the_firms_app_honours_the_platform_contract(store: SqliteDatasetStore) -> None:
    (record,) = check_app(risk, {"desk_var": ARGS}, datasets=store)

    assert record.output is not None
    assert record.output["var"] == pytest.approx(0.022)
    assert record.output["data_versions"] == {"dataset:desks": store.get("desks").fingerprint}
    assert record.output["convention"] == "Historical, nearest rank, one period, no scaling"
    assert record.output["evidence"][0]["sources"][0]["source_id"] == "desks.growth"


def test_the_firms_tool_is_the_same_over_mcp_and_rest_and_is_recorded(
    store: SqliteDatasetStore, tmp_path: Path
) -> None:
    key = add_key(tmp_path / "keys.toml", "alice", ["tools:list", "tools:call", "runs:read"])
    runs = MemoryRunStore()

    async def calls(app: Any) -> tuple[Any, Any, Any, Any]:
        async with (
            app.router.lifespan_context(app),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url=BASE,
                headers={"Authorization": f"Bearer {key}"},
            ) as client,
        ):
            rest = (await client.post("/v1/tools/desk_var/call", json={"arguments": ARGS})).json()
            analytics = (
                await client.post(
                    "/v1/tools/analyze_performance/call",
                    json={"arguments": {"returns": GROWTH}},
                )
            ).json()
            async with (
                streamable_http_client(f"{BASE}/mcp", http_client=client) as (read, write),
                ClientSession(read, write) as session,
            ):
                initialised = await session.initialize()
                mcp = await session.call_tool("desk_var", ARGS)
            return rest, analytics, initialised, mcp

    # The app serve_apps would serve, built from the same arguments, without running a server.
    with build_server_app(
        [risk], datasets=store, policy=PermissionPolicy(), keys=tmp_path / "keys.toml", runs=runs
    ) as app:
        rest, analytics, initialised, mcp = anyio.run(calls, app)
    assert rest["status"] == "ok"
    assert rest["output"]["var"] == pytest.approx(0.022)
    assert mcp.structured_content == rest["output"]
    assert rest["data_versions"] == rest["output"]["data_versions"]
    assert analytics["status"] == "ok"  # the platform's analytics read the firm's data too
    assert "desk_var" in (initialised.instructions or "")
    recorded = runs.get_run(rest["run_id"])
    assert recorded is not None
    assert recorded.run.principal == "alice"
    assert recorded.calls[0].tool == "desk_var"
    assert recorded.calls[0].tool_version == "1.0.0"


def test_with_access_rules_the_firms_tool_reads_only_what_the_caller_may(
    store: SqliteDatasetStore, tmp_path: Path
) -> None:
    rules = tmp_path / "access.toml"
    rules.write_text('[groups.risk]\ntools = ["desk_var"]\ndatasets = ["other"]\n')
    keys = tmp_path / "keys.toml"
    key = add_key(keys, "alice", ["tools:call"], groups=["risk"])

    with (
        build_server_app(
            [risk],
            datasets=store,
            policy=PermissionPolicy(),
            access=FileAccessPolicy(rules),
            keys=keys,
            runs=MemoryRunStore(),
        ) as app,
        TestClient(app, base_url=BASE) as client,
    ):
        result = client.post(
            "/v1/tools/desk_var/call",
            json={"arguments": ARGS},
            headers={"Authorization": f"Bearer {key}"},
        ).json()

    assert result["status"] == "error"
    assert "No dataset 'desks'" in result["error"]


def test_a_firms_tool_gets_no_permission_it_is_not_granted(tmp_path: Path) -> None:
    def call_home() -> float:
        """Calls home, which needs the network."""
        return 1.0

    home = FirmApp("home")
    home.expose(call_home, permissions=[Permission.NETWORK])
    key = add_key(tmp_path / "keys.toml", "alice", ["tools:call"])
    runs = tmp_path / "runs.db"

    with (
        build_server_app(
            [home], policy=PermissionPolicy(), keys=tmp_path / "keys.toml", runs=runs
        ) as app,
        TestClient(app, base_url=BASE) as client,
    ):
        answer = client.post(
            "/v1/tools/call_home/call",
            json={"arguments": {}},
            headers={"Authorization": f"Bearer {key}"},
        )

    assert answer.json()["status"] == "denied"
    # The run store given as a path was opened for the app and closed after it; the call is in it.
    with SqliteRunStore(runs) as recorded:
        (run,) = recorded.list_runs(5)
    assert run.principal == "alice"


def test_the_firms_server_starts_from_the_usual_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    add_key(tmp_path / "keys.toml", "alice", ["tools:call"])
    monkeypatch.setenv("FIRM_DB", str(tmp_path / "firm.db"))
    monkeypatch.setenv("QUANT_AGENT_OS_API_KEYS_FILE", str(tmp_path / "keys.toml"))
    monkeypatch.setenv("QUANT_AGENT_OS_RUNS_FILE", str(tmp_path / "runs.db"))
    monkeypatch.setenv("QUANT_AGENT_OS_STORE", str(tmp_path / "absent.db"))
    get_settings.cache_clear()
    served: dict[str, Any] = {}

    def fake_run(app: Any, *, host: str, port: int) -> None:
        served.update(host=host, port=port)

    monkeypatch.setattr("quant_platform.serve.run_http", fake_run)
    try:
        main()
    finally:
        get_settings.cache_clear()

    assert served == {"host": "127.0.0.1", "port": 8000}


def test_building_the_server_app_refuses_as_serving_would(
    store: SqliteDatasetStore, tmp_path: Path
) -> None:
    from quant_agent_os.api import ApiKeyError

    with (
        pytest.raises(ApiKeyError, match="No API keys file"),
        build_server_app([risk], datasets=store, keys=tmp_path / "none.toml", runs=tmp_path / "r"),
    ):
        pass
    assert not (tmp_path / "r").exists()  # refused before the run store was opened
