"""A firm's own tools on the platform's server (decision 0020): compose and serve."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from quant_agent_os.api import ApiKeyError, add_key
from quant_agent_os.data import MemoryDatasetStore
from quant_agent_os.runs import RunStoreError
from quant_agent_os.tools import Permission, PermissionPolicy, tool
from quant_agent_os.tools.registry import DuplicateToolError
from quant_platform import compose, serve_http
from quant_platform.composition import PLATFORM_INSTRUCTIONS


class Empty(BaseModel):
    pass


class Done(BaseModel):
    done: bool = True


def _spec(name: str) -> Any:
    @tool(name=name, description="A firm's tool.")
    def run(params: Empty) -> Done:
        return Done()

    return run


def test_extra_tools_are_served_as_the_firm_toolset() -> None:
    catalog = compose(policy=PermissionPolicy(), extra_tools=[_spec("desk_var")])

    assert "desk_var" in [spec.name for spec in catalog.tools]
    assert catalog.toolsets["desk_var"] == "firm"


def test_server_info_lists_the_firm_tools() -> None:
    catalog = compose(policy=PermissionPolicy(), extra_tools=[_spec("desk_var")])
    info = next(spec for spec in catalog.tools if spec.name == "server_info")

    assert "desk_var" in str(info.function(info.input_model()))


def test_extra_instructions_follow_the_platform_instructions() -> None:
    catalog = compose(policy=PermissionPolicy(), extra_instructions="Use desk_var for desk risk.")

    assert catalog.instructions == f"{PLATFORM_INSTRUCTIONS}\n\nUse desk_var for desk risk."


@pytest.mark.parametrize("name", ["analyze_risk", "list_datasets", "server_info"])
def test_an_extra_tool_cannot_replace_a_platform_tool(name: str) -> None:
    pytest.importorskip("empyrical")
    with pytest.raises(DuplicateToolError, match=name):
        compose(
            policy=PermissionPolicy(),
            datasets=MemoryDatasetStore(),
            extra_tools=[_spec(name)],
        )


def test_two_extra_tools_cannot_share_a_name() -> None:
    with pytest.raises(DuplicateToolError, match="desk_var"):
        compose(policy=PermissionPolicy(), extra_tools=[_spec("desk_var"), _spec("desk_var")])


def test_the_catalog_keeps_its_policy() -> None:
    policy = PermissionPolicy(granted=frozenset({Permission.NETWORK}))

    assert compose(policy=policy).policy is policy


def test_serve_http_refuses_to_start_without_a_key(tmp_path: Path) -> None:
    keys = tmp_path / "keys.toml"
    keys.write_text("", encoding="utf-8")
    catalog = compose(policy=PermissionPolicy())

    with pytest.raises(ApiKeyError, match="no enabled key"):
        serve_http(catalog, keys=keys, runs=tmp_path / "runs.db")


def test_serve_http_refuses_to_start_without_a_run_store(tmp_path: Path) -> None:
    keys = tmp_path / "keys.toml"
    add_key(keys, "alice", ["tools:call"])
    catalog = compose(policy=PermissionPolicy())

    with pytest.raises(RunStoreError):
        serve_http(catalog, keys=keys, runs=tmp_path)


def test_serve_http_binds_loopback_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    keys = tmp_path / "keys.toml"
    add_key(keys, "alice", ["tools:call"])
    served: dict[str, Any] = {}

    def fake_run(app: Any, *, host: str, port: int) -> None:
        served.update(host=host, port=port)

    monkeypatch.setattr("quant_platform.serve.run_http", fake_run)
    serve_http(compose(policy=PermissionPolicy()), keys=keys, runs=tmp_path / "runs.db")

    assert served == {"host": "127.0.0.1", "port": 8000}
