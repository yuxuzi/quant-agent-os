"""examples/firm-tools: a firm's tool and data, served beside the platform's toolsets."""

from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from firm_data import SAMPLE_RETURNS, SqliteDatasetStore, create_sample_database
from firm_server import build_catalog, main
from firm_tools import DeskVarInput, firm_tools
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel

from quant_agent_os.api import KeyRing, add_key, load_keys
from quant_agent_os.config import get_settings
from quant_agent_os.data import UnknownDatasetError
from quant_agent_os.runs import MemoryRunStore
from quant_agent_os.testing import check_tool
from quant_agent_os.tools import FileAccessPolicy, Permission, PermissionPolicy, tool
from quant_platform import compose
from quant_platform.http import build_http_app, single_tenant

pytest.importorskip("empyrical")

BASE = "http://127.0.0.1:8000"
ARGS = {"dataset": "desks", "column": "growth"}


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
    output = firm_tools(store)[0].function(DeskVarInput(**ARGS))

    assert output.var == pytest.approx(0.022)
    assert output.periods == 24
    assert output.data_versions == {"dataset:desks": store.get("desks").fingerprint}


def test_the_firms_tool_honours_the_platform_contract(store: SqliteDatasetStore) -> None:
    assert check_tool(firm_tools(store)[0], ARGS).status == "ok"


def test_the_firms_tool_is_the_same_over_mcp_and_rest_and_is_recorded(
    store: SqliteDatasetStore, tmp_path: Path
) -> None:
    key = add_key(tmp_path / "keys.toml", "alice", ["tools:list", "tools:call", "runs:read"])
    runs = MemoryRunStore()
    catalog = build_catalog(store, PermissionPolicy())
    app = build_http_app(
        single_tenant(catalog, runs, catalog.policy),
        KeyRing(load_keys(tmp_path / "keys.toml")),
        instructions=catalog.instructions,
    )

    async def calls() -> tuple[Any, Any, Any, Any]:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=BASE,
                headers={"Authorization": f"Bearer {key}"},
            ) as client,
        ):
            rest = (await client.post("/v1/tools/desk_var/call", json={"arguments": ARGS})).json()
            analytics = (
                await client.post(
                    "/v1/tools/analyze_performance/call",
                    json={"arguments": {"returns": ARGS}},
                )
            ).json()
            async with (
                streamable_http_client(f"{BASE}/mcp", http_client=client) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                initialised = await session.initialize()
                mcp = await session.call_tool("desk_var", ARGS)
            return rest, analytics, initialised, mcp

    rest, analytics, initialised, mcp = anyio.run(calls)
    direct = firm_tools(store)[0].function(DeskVarInput(**ARGS)).model_dump(mode="json")

    assert rest["status"] == "ok"
    assert rest["output"] == direct
    assert mcp.structuredContent == direct
    assert rest["data_versions"] == direct["data_versions"]
    assert analytics["status"] == "ok"  # the platform's analytics read the firm's data too
    assert "desk_var" in (initialised.instructions or "")
    recorded = runs.get_run(rest["run_id"])
    assert recorded is not None
    assert recorded.run.principal == "alice"
    assert recorded.calls[0].tool == "desk_var"
    assert recorded.calls[0].tool_version == "1.0.0"
    assert catalog.toolsets["desk_var"] == "firm"


def test_with_access_rules_the_firms_tool_reads_only_what_the_caller_may(
    store: SqliteDatasetStore, tmp_path: Path
) -> None:
    rules = tmp_path / "access.toml"
    rules.write_text('[groups.risk]\ntools = ["desk_var"]\ndatasets = ["other"]\n')
    keys = tmp_path / "keys.toml"
    key = add_key(keys, "alice", ["tools:call"], groups=["risk"])
    catalog = build_catalog(store, PermissionPolicy(), access=FileAccessPolicy(rules))
    app = build_http_app(
        single_tenant(catalog, MemoryRunStore(), catalog.policy),
        KeyRing(load_keys(keys)),
        instructions=catalog.instructions,
    )

    async def call() -> Any:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=BASE,
                headers={"Authorization": f"Bearer {key}"},
            ) as client,
        ):
            return (await client.post("/v1/tools/desk_var/call", json={"arguments": ARGS})).json()

    result = anyio.run(call)
    assert result["status"] == "error"
    assert "No dataset 'desks'" in result["error"]


def test_a_firms_tool_gets_no_permission_it_is_not_granted(tmp_path: Path) -> None:
    class Empty(BaseModel):
        pass

    @tool(name="call_home", description="Needs the network.", permissions=[Permission.NETWORK])
    def call_home(params: Empty) -> Empty:
        return Empty()

    key = add_key(tmp_path / "keys.toml", "alice", ["tools:call"])
    catalog = compose(policy=PermissionPolicy(), extra_tools=[call_home])
    app = build_http_app(
        single_tenant(catalog, MemoryRunStore(), catalog.policy),
        KeyRing(load_keys(tmp_path / "keys.toml")),
        instructions=catalog.instructions,
    )

    async def call() -> Any:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=BASE,
                headers={"Authorization": f"Bearer {key}"},
            ) as client,
        ):
            return (await client.post("/v1/tools/call_home/call", json={"arguments": {}})).json()

    assert anyio.run(call)["status"] == "denied"


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
