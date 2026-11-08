"""Per-caller access (decision 0021): who reaches which tools and datasets, over MCP and REST.

A release gate, like the tenant isolation tests. A refused dataset must look like a missing one to
the caller and be visible to an auditor, and anything uncertain must be a refusal.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from pydantic import AnyUrl, BaseModel
from typer.testing import CliRunner

from quant_agent_os.api import ApiKeyError, KeyRing, add_key, load_keys
from quant_agent_os.config import get_settings
from quant_agent_os.data import (
    DirectoryDatasetStore,
    GuardedDatasetStore,
    UnknownDatasetError,
)
from quant_agent_os.runs import MemoryRunStore
from quant_agent_os.runtime import ToolCall, ToolExecutor
from quant_agent_os.tools import (
    AccessFileError,
    Caller,
    FileAccessPolicy,
    PermissionPolicy,
    ToolRegistry,
    current_caller,
    tool,
)
from quant_agent_os.tools.access import acting_as
from quant_platform import Catalog, compose, serve_http, serve_stdio
from quant_platform.cli import app as cli
from quant_platform.http import build_http_app, single_tenant
from quant_platform.serve import warn_about_open_access
from quant_research import ResearchApp

pytest.importorskip("empyrical")

BASE = "http://127.0.0.1:8000"
RULES = """
[groups.growth]
tools = ["analyze_performance", "list_datasets", "research_report", "whoami", "server_info"]
datasets = ["growth-*", "factors"]

[groups.value]
tools = ["analyze_performance", "list_datasets", "whoami"]
datasets = ["value-*"]
"""
SERIES = [0.012, -0.004, 0.021, -0.015, 0.008, 0.017, -0.006, 0.011, 0.002, -0.009, 0.014, 0.005]
SCOPES = ["tools:list", "tools:call"]


class Empty(BaseModel):
    pass


class Who(BaseModel):
    principal: str | None
    groups: list[str]


@tool(name="whoami", description="A firm's tool that reads who is calling, slowly.")
def whoami(params: Empty) -> Who:
    time.sleep(0.02)  # long enough for concurrent calls to overlap
    caller = current_caller()
    return Who(
        principal=caller.principal if caller else None,
        groups=sorted(caller.groups) if caller else [],
    )


def returns(dataset: str) -> dict[str, Any]:
    return {"returns": {"dataset": dataset, "column": "fund"}}


@dataclass
class Server:
    app: Any
    keys: dict[str, str]
    runs: MemoryRunStore
    access_path: Path


@pytest.fixture
def store(tmp_path: Path) -> DirectoryDatasetStore:
    datasets = DirectoryDatasetStore(tmp_path / "datasets")
    for name in ("growth-fund", "value-fund", "factors"):
        source = tmp_path / f"{name}.csv"
        source.write_text(
            "month,fund\n" + "".join(f"2024-{m + 1:02d},{v}\n" for m, v in enumerate(SERIES))
        )
        datasets.put(name, source)
    return datasets


@pytest.fixture
def server(research: ResearchApp, store: DirectoryDatasetStore, tmp_path: Path) -> Server:
    path = tmp_path / "access.toml"
    path.write_text(RULES)
    keys_file = tmp_path / "keys.toml"
    keys = {
        "alice": add_key(keys_file, "alice", SCOPES, groups=["growth"]),
        "bob": add_key(keys_file, "bob", SCOPES, groups=["value"]),
        "carol": add_key(keys_file, "carol", SCOPES),
        "dave": add_key(keys_file, "dave", SCOPES, groups=["no-such-group"]),
        "auditor": add_key(keys_file, "auditor", ["runs:read"]),
    }
    runs = MemoryRunStore()
    policy = PermissionPolicy()
    catalog = compose(
        policy=policy,
        research=research.research,
        datasets=store,
        extra_tools=[whoami],
        access=FileAccessPolicy(path),
    )
    app = build_http_app(
        single_tenant(catalog, runs, policy),
        KeyRing(load_keys(keys_file)),
        instructions=catalog.instructions,
    )
    return Server(app, keys, runs, path)


@asynccontextmanager
async def _client(server: Server, who: str) -> AsyncIterator[httpx.AsyncClient]:
    async with (
        server.app.router.lifespan_context(server.app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url=BASE,
            headers={"Authorization": f"Bearer {server.keys[who]}"},
        ) as client,
    ):
        yield client


@asynccontextmanager
async def _session(client: httpx.AsyncClient) -> AsyncIterator[ClientSession]:
    async with (
        streamable_http_client(f"{BASE}/mcp", http_client=client) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


def call(
    server: Server, who: str, interface: str, name: str, args: dict[str, Any]
) -> dict[str, Any]:
    """Return one call as {status, output, error, text}, whichever interface carried it."""

    async def main() -> dict[str, Any]:
        async with _client(server, who) as client:
            if interface == "rest":
                body = (
                    await client.post(f"/v1/tools/{name}/call", json={"arguments": args})
                ).json()
                return {
                    "status": body["status"],
                    "output": body["output"],
                    "error": body["error"],
                    "run_id": body["run_id"],
                    "text": str(body),
                }
            async with _session(client) as session:
                result = await session.call_tool(name, args)
            details = result.structuredContent or {}
            return {
                "status": details.get("status", "ok") if result.isError else "ok",
                "output": None if result.isError else details,
                "error": details.get("error") if result.isError else None,
                "text": result.content[0].text if result.content else "",
            }

    return anyio.run(main)


def listed(server: Server, who: str) -> tuple[list[str], list[str]]:
    async def main() -> tuple[list[str], list[str]]:
        async with _client(server, who) as client:
            rest = [item["name"] for item in (await client.get("/v1/tools")).json()]
            async with _session(client) as session:
                mcp = [item.name for item in (await session.list_tools()).tools]
        return sorted(rest), sorted(mcp)

    return anyio.run(main)


INTERFACES = ["rest", "mcp"]


@pytest.mark.parametrize(
    ("who", "tools"),
    [
        (
            "alice",
            [
                "analyze_performance",
                "list_datasets",
                "research_report",
                "server_info",
                "whoami",
            ],
        ),
        ("bob", ["analyze_performance", "list_datasets", "whoami"]),
        ("carol", []),  # no groups
        ("dave", []),  # a group the file does not name
    ],
)
def test_each_caller_is_listed_only_the_tools_it_may_call(
    server: Server, who: str, tools: list[str]
) -> None:
    assert listed(server, who) == (tools, tools)


@pytest.mark.parametrize("interface", INTERFACES)
def test_a_listed_tool_runs_for_its_caller(server: Server, interface: str) -> None:
    result = call(server, "alice", interface, "analyze_performance", returns("growth-fund"))

    assert result["status"] == "ok", result
    assert result["output"]["metrics"]


@pytest.mark.parametrize("interface", INTERFACES)
@pytest.mark.parametrize("who", ["bob", "carol", "dave"])
def test_a_tool_outside_a_callers_rights_is_denied(
    server: Server, who: str, interface: str
) -> None:
    result = call(server, who, interface, "research_report", {"focus": "momentum"})

    assert result["status"] == "denied"
    assert "does not include tool 'research_report'" in result["error"]


def test_a_denied_tool_is_recorded_with_who_tried(server: Server) -> None:
    run_id = call(server, "bob", "rest", "research_report", {"focus": "momentum"})["run_id"]

    stored = server.runs.get_run(run_id)
    assert stored is not None
    assert stored.run.principal == "bob"
    assert stored.calls[0].status == "denied"
    assert stored.calls[0].refused == ["tool.call research_report"]
    called = next(event for event in stored.events if event.event == "tool.called")
    assert called.principal == "bob"
    assert called.details["status"] == "denied"
    assert called.details["refused"] == ["tool.call research_report"]


@pytest.mark.parametrize("interface", INTERFACES)
def test_a_refused_dataset_answers_exactly_as_a_missing_one(server: Server, interface: str) -> None:
    refused = call(server, "alice", interface, "analyze_performance", returns("value-fund"))
    missing = call(server, "alice", interface, "analyze_performance", returns("no-such-fund"))

    assert refused["status"] == missing["status"] == "error"
    assert refused["error"].replace("value-fund", "X") == missing["error"].replace(
        "no-such-fund", "X"
    )
    assert refused["text"].count("value-fund") == 1  # only the name the caller itself gave
    assert "factors" in refused["error"]  # names what the caller may read


@pytest.mark.parametrize("interface", INTERFACES)
def test_an_error_never_names_a_dataset_the_caller_cannot_read(
    server: Server, interface: str
) -> None:
    result = call(server, "bob", interface, "analyze_performance", returns("no-such-fund"))

    assert "growth-fund" not in result["text"] and "factors" not in result["text"]
    assert "value-fund" in result["text"]


@pytest.mark.parametrize("interface", INTERFACES)
def test_a_refusal_is_for_the_auditor_and_never_shown_to_the_caller(
    server: Server, interface: str
) -> None:
    result = call(server, "alice", interface, "analyze_performance", returns("value-fund"))

    assert result["status"] == "error"
    assert "dataset.read" not in result["text"]
    assert "refused" not in result["text"].lower()


def test_the_auditor_sees_a_read_outside_the_rules_but_not_a_mere_typo(server: Server) -> None:
    refused = call(server, "alice", "rest", "analyze_performance", returns("value-fund"))
    missing = call(server, "alice", "rest", "analyze_performance", returns("growth-typo"))

    stored = server.runs.get_run(refused["run_id"])
    assert stored is not None
    assert stored.calls[0].refused == ["dataset.read value-fund"]
    assert stored.run.principal == "alice"
    other = server.runs.get_run(missing["run_id"])
    assert other is not None
    assert other.calls[0].status == "error"
    assert other.calls[0].refused == []  # inside the rules, and simply missing

    async def read() -> Any:
        async with _client(server, "auditor") as client:
            return (await client.get(f"/v1/runs/{refused['run_id']}")).json()

    assert anyio.run(read)["calls"][0]["refused"] == ["dataset.read value-fund"]


@pytest.mark.parametrize("interface", INTERFACES)
def test_list_datasets_shows_only_what_the_caller_may_read(server: Server, interface: str) -> None:
    alice = call(server, "alice", interface, "list_datasets", {})
    bob = call(server, "bob", interface, "list_datasets", {})
    named = call(server, "bob", interface, "list_datasets", {"dataset_id": "growth-fund"})

    assert [item["id"] for item in alice["output"]["datasets"]] == ["factors", "growth-fund"]
    assert [item["id"] for item in bob["output"]["datasets"]] == ["value-fund"]
    assert named["status"] == "error" and "factors" not in named["text"]


def test_data_versions_do_not_disclose_a_refused_dataset(server: Server) -> None:
    result = call(server, "bob", "rest", "list_datasets", {})

    assert list(result["output"]["data_versions"]) == ["dataset:value-fund"]


def test_server_info_names_only_the_tools_the_caller_may_call(server: Server) -> None:
    result = call(server, "alice", "rest", "server_info", {})

    assert [item["name"] for item in result["output"]["tools"]] == [
        "analyze_performance",
        "list_datasets",
        "research_report",
        "server_info",
        "whoami",
    ]


def test_a_resource_follows_the_same_rights(server: Server) -> None:
    async def read(who: str, uri: str) -> str:
        async with _client(server, who) as client, _session(client) as session:
            try:
                return (await session.read_resource(AnyUrl(uri))).contents[0].text
            except McpError as error:
                return f"refused: {error}"

    assert "columns" in anyio.run(read, "alice", "qaos://dataset/growth-fund")
    bob = anyio.run(read, "bob", "qaos://dataset/growth-fund")
    assert bob.startswith("refused") and "No dataset 'growth-fund'" in bob
    assert "factors" not in bob
    # carol may not call list_datasets at all, so nothing matches the address
    assert anyio.run(read, "carol", "qaos://dataset/growth-fund").startswith("refused")


def test_resources_are_listed_only_for_tools_the_caller_may_call(server: Server) -> None:
    async def templates(who: str) -> list[str]:
        async with _client(server, who) as client, _session(client) as session:
            found = (await session.list_resource_templates()).resourceTemplates
            return [item.name for item in found]

    assert "dataset" in anyio.run(templates, "alice")
    assert anyio.run(templates, "carol") == []


@pytest.mark.parametrize("interface", INTERFACES)
def test_research_reports_nested_read_keeps_the_caller(server: Server, interface: str) -> None:
    arguments = {"focus": "momentum"}
    allowed = call(
        server, "alice", interface, "research_report", arguments | returns("growth-fund")
    )
    refused = call(server, "alice", interface, "research_report", arguments | returns("value-fund"))

    assert allowed["output"]["metrics"]
    assert refused["output"]["metrics"] == {}
    step = next(s for s in refused["output"]["steps"] if s["tool"] == "analyze_performance")
    assert step["status"] == "error"
    assert "growth-fund" in step["error"] and "value-fund" in step["error"]
    assert "dataset:value-fund" not in refused["output"]["data_versions"]


def test_a_nested_refusal_reaches_the_outer_calls_record(server: Server) -> None:
    run_id = call(
        server, "alice", "rest", "research_report", {"focus": "momentum"} | returns("value-fund")
    )["run_id"]

    stored = server.runs.get_run(run_id)
    assert stored is not None
    assert stored.calls[0].refused == ["dataset.read value-fund"]


def test_a_firms_tool_is_told_its_real_caller(server: Server) -> None:
    result = call(server, "alice", "mcp", "whoami", {})

    assert result["output"] == {"principal": "alice", "groups": ["growth"]}


def test_parallel_callers_never_see_each_others_identity_or_data(server: Server) -> None:
    """A leaked caller would be a silent cross-user read, so run them interleaved."""
    people = {"alice": "growth-fund", "bob": "value-fund"}

    async def one(who: str, n: int, client: httpx.AsyncClient) -> None:
        interface = "rest" if n % 2 else "mcp"
        if interface == "rest":
            me = (await client.post("/v1/tools/whoami/call", json={"arguments": {}})).json()
            assert me["output"]["principal"] == who
            listed = (
                await client.post("/v1/tools/list_datasets/call", json={"arguments": {}})
            ).json()
            assert [item["id"] for item in listed["output"]["datasets"]][-1] == people[who]
            return
        async with _session(client) as session:
            me_mcp = await session.call_tool("whoami", {})
            assert (me_mcp.structuredContent or {})["principal"] == who
            tools = [item.name for item in (await session.list_tools()).tools]
            assert ("research_report" in tools) == (who == "alice")

    async def main() -> None:
        async with (
            server.app.router.lifespan_context(server.app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=server.app),
                base_url=BASE,
                headers={"Authorization": f"Bearer {server.keys['alice']}"},
            ) as alice,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=server.app),
                base_url=BASE,
                headers={"Authorization": f"Bearer {server.keys['bob']}"},
            ) as bob,
        ):
            for _ in range(5):
                await asyncio.gather(
                    *(one("alice", n, alice) for n in range(3)),
                    *(one("bob", n, bob) for n in range(3)),
                )

    anyio.run(main)


def test_the_executor_refuses_when_no_caller_is_bound(store: DirectoryDatasetStore) -> None:
    class AllowAll:
        def allows(self, caller: Caller, action: str, resource: str) -> bool:
            return True

    executor = ToolExecutor(ToolRegistry([whoami]), PermissionPolicy(), access=AllowAll())

    assert executor.execute(ToolCall(tool="whoami")).status == "denied"
    assert not executor.serves("whoami")
    with acting_as(Caller("alice")):
        assert executor.execute(ToolCall(tool="whoami")).status == "ok"
    guarded = GuardedDatasetStore(store, AllowAll())
    assert guarded.ids() == []
    with pytest.raises(UnknownDatasetError):
        guarded.get("growth-fund")


@pytest.mark.parametrize("answer", ["raises", "not-a-bool"])
def test_a_failing_access_policy_refuses(answer: str, store: DirectoryDatasetStore) -> None:
    class Broken:
        def allows(self, caller: Caller, action: str, resource: str) -> Any:
            if answer == "raises":
                raise ConnectionError("the entitlement service is down")
            return "yes"

    executor = ToolExecutor(ToolRegistry([whoami]), PermissionPolicy(), access=Broken())
    with acting_as(Caller("alice")):
        record = executor.execute(ToolCall(tool="whoami"))
        assert record.status == "denied"
        assert "entitlement" not in (record.error or "")
        assert GuardedDatasetStore(store, Broken()).ids() == []


@pytest.fixture
def rules(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "access.toml"
    path.write_text(RULES)
    yield path


ALICE = Caller("alice", groups=frozenset({"growth"}))


def _reloaded(path: Path, write: Callable[[], None]) -> FileAccessPolicy:
    policy = FileAccessPolicy(path)
    assert policy.allows(ALICE, "tool.call", "whoami")
    write()
    return policy


def test_the_file_is_deny_by_default_and_matches_patterns(rules: Path) -> None:
    policy = FileAccessPolicy(rules)

    assert policy.allows(ALICE, "dataset.read", "growth-fund")
    assert not policy.allows(ALICE, "dataset.read", "value-fund")
    assert not policy.allows(ALICE, "tool.call", "growth-fund")  # rules are per action
    assert not policy.allows(ALICE, "tool.call", "WHOAMI")  # case-sensitive
    assert not policy.allows(Caller("alice"), "tool.call", "whoami")


def test_a_revocation_applies_without_a_restart(rules: Path) -> None:
    policy = _reloaded(rules, lambda: rules.write_text('[groups.growth]\ntools = ["other"]\n'))

    assert not policy.allows(ALICE, "tool.call", "whoami")


@pytest.mark.parametrize(
    "broken", ["[groups.growth\n", '[groups.growth]\ntool = ["whoami"]\n', 'groups = "x"\n']
)
def test_an_invalid_file_allows_nothing_until_it_is_fixed(rules: Path, broken: str) -> None:
    policy = _reloaded(rules, lambda: rules.write_text(broken))

    assert not policy.allows(ALICE, "tool.call", "whoami")
    rules.write_text(RULES + "\n")
    assert policy.allows(ALICE, "tool.call", "whoami")


def test_a_missing_file_allows_nothing(rules: Path) -> None:
    policy = _reloaded(rules, rules.unlink)

    assert not policy.allows(ALICE, "tool.call", "whoami")


def test_a_server_will_not_start_on_a_missing_or_invalid_file(tmp_path: Path) -> None:
    with pytest.raises(AccessFileError, match="No access file"):
        FileAccessPolicy(tmp_path / "absent.toml")
    (tmp_path / "bad.toml").write_text("[groups.a]\nnope = []\n")
    with pytest.raises(AccessFileError, match="Invalid access file"):
        FileAccessPolicy(tmp_path / "bad.toml")


def test_without_an_access_policy_every_key_reaches_everything_as_before(
    store: DirectoryDatasetStore, tmp_path: Path
) -> None:
    keys_file = tmp_path / "keys.toml"
    key = add_key(keys_file, "carol", SCOPES)
    policy = PermissionPolicy()
    catalog = compose(policy=policy, datasets=store)
    open_server = Server(
        build_http_app(
            single_tenant(catalog, MemoryRunStore(), policy),
            KeyRing(load_keys(keys_file)),
            instructions=catalog.instructions,
        ),
        {"carol": key},
        MemoryRunStore(),
        tmp_path,
    )

    assert "analyze_performance" in listed(open_server, "carol")[0]
    result = call(open_server, "carol", "rest", "analyze_performance", returns("value-fund"))
    assert result["status"] == "ok"


@pytest.fixture
def start_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> Callable[[Catalog, Path], str]:
    """Run `serve_http` up to listening, and return what it printed: the start-up warnings."""
    monkeypatch.setattr("quant_platform.serve.run_http", lambda *args, **kwargs: None)

    def start(catalog: Catalog, keys: Path) -> str:
        serve_http(catalog, keys=keys, runs=tmp_path / "runs.db")
        return " ".join(capsys.readouterr().err.split())

    return start


def test_groups_with_no_access_file_are_called_out(
    tmp_path: Path, rules: Path, start_up: Callable[[Catalog, Path], str]
) -> None:
    keys = tmp_path / "keys.toml"
    add_key(keys, "plain", SCOPES)
    open_catalog = compose(policy=PermissionPolicy())

    assert "warning" not in start_up(open_catalog, keys)

    add_key(keys, "desk", SCOPES, groups=["growth"])
    assert "groups do nothing" in start_up(open_catalog, keys)

    guarded = compose(policy=PermissionPolicy(), access=FileAccessPolicy(rules))
    assert "warning" not in start_up(guarded, keys)


def test_an_access_file_the_catalog_ignores_is_called_out(
    tmp_path: Path,
    rules: Path,
    monkeypatch: pytest.MonkeyPatch,
    start_up: Callable[[Catalog, Path], str],
) -> None:
    monkeypatch.setenv("QUANT_AGENT_OS_ACCESS_FILE", str(rules))
    get_settings.cache_clear()
    keys = tmp_path / "keys.toml"
    add_key(keys, "plain", SCOPES)

    assert "no access policy" in start_up(compose(policy=PermissionPolicy()), keys)
    applied = compose(policy=PermissionPolicy(), access=FileAccessPolicy(rules))
    assert "warning" not in start_up(applied, keys)
    get_settings.cache_clear()


def test_an_access_file_another_policy_overrides_is_called_out(
    tmp_path: Path,
    rules: Path,
    monkeypatch: pytest.MonkeyPatch,
    start_up: Callable[[Catalog, Path], str],
) -> None:
    """A custom server whose catalog has its own policy must not seem to apply the file."""
    monkeypatch.setenv("QUANT_AGENT_OS_ACCESS_FILE", str(rules))
    get_settings.cache_clear()
    keys = tmp_path / "keys.toml"
    add_key(keys, "plain", SCOPES)
    other = tmp_path / "other.toml"
    other.write_text(rules.read_text("utf-8"), "utf-8")

    overridden = compose(policy=PermissionPolicy(), access=FileAccessPolicy(other))
    assert "its rules do not apply" in start_up(overridden, keys)
    get_settings.cache_clear()


def test_the_warning_reads_the_keys_already_loaded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A keys file that breaks after loading must not stop the server from starting."""
    keys = tmp_path / "keys.toml"
    add_key(keys, "desk", SCOPES, groups=["growth"])
    ring = KeyRing.from_file(keys)
    keys.write_text("not toml [", "utf-8")

    warn_about_open_access(compose(policy=PermissionPolicy()), ring)  # raises nothing
    assert "Traceback" not in capsys.readouterr().err


def test_stdio_refuses_a_catalog_with_an_access_policy(rules: Path) -> None:
    catalog = compose(policy=PermissionPolicy(), access=FileAccessPolicy(rules))

    with pytest.raises(ValueError, match="no authenticated caller"):
        serve_stdio(catalog, record=False)


def test_qaos_serve_takes_an_access_file(
    monkeypatch: pytest.MonkeyPatch,
    store: DirectoryDatasetStore,
    rules: Path,
    tmp_path: Path,
) -> None:
    keys_file = tmp_path / "keys.toml"
    key = add_key(keys_file, "bob", SCOPES, groups=["value"])
    seen: dict[str, Any] = {}

    def fake_run(app: Any, *, host: str, port: int) -> None:
        async def main() -> Any:
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url=BASE,
                    headers={"Authorization": f"Bearer {key}"},
                ) as client,
            ):
                return (await client.get("/v1/tools")).json()

        seen["tools"] = [item["name"] for item in anyio.run(main)]

    monkeypatch.setattr("quant_platform.serve.run_http", fake_run)
    args = ["serve", "--http", "--toolsets", "analytics", "--datasets", str(store.root)]
    args += ["--keys", str(keys_file), "--runs", str(tmp_path / "runs.db")]

    result = CliRunner().invoke(cli, [*args, "--access", str(rules)])
    assert result.exit_code == 0, result.output
    assert seen["tools"] == ["analyze_performance", "list_datasets"]

    refused = CliRunner().invoke(cli, [*args, "--access", str(tmp_path / "absent.toml")])
    assert refused.exit_code == 1 and "No access file" in refused.output

    monkeypatch.setenv("QUANT_AGENT_OS_ACCESS_FILE", str(tmp_path / "absent.toml"))
    from quant_agent_os.config import get_settings

    get_settings.cache_clear()
    assert CliRunner().invoke(cli, args).exit_code == 1  # the setting is honoured too


def test_qaos_keys_new_records_groups(tmp_path: Path) -> None:
    path = tmp_path / "keys.toml"
    result = CliRunner().invoke(
        cli,
        [
            "keys",
            "new",
            "--principal",
            "alice",
            "--scope",
            "tools:call",
            "--keys",
            str(path),
            "--group",
            "growth",
            "--group",
            "risk",
        ],
    )

    assert result.exit_code == 0, result.output
    assert load_keys(path)[0].groups == {"growth", "risk"}


def test_a_malformed_group_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ApiKeyError):
        add_key(tmp_path / "keys.toml", "alice", ["tools:call"], groups=['x"\nscopes = ["*'])
