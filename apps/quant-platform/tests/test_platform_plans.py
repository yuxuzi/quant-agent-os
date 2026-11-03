"""Plans in the tenants file (B-36, decision 0015): entitlements, limits, and their checks."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from typer.testing import CliRunner

from quant_agent_os.api import KeyRing, add_key, load_keys
from quant_agent_os.runs import MemoryRunStore
from quant_agent_os.runtime import ToolExecutor
from quant_platform.cli import app as cli
from quant_platform.composition import instructions_for
from quant_platform.http import build_http_app
from quant_platform.tenants import (
    Plan,
    TenantCatalogs,
    TenantConfig,
    TenantsFile,
    TenantsFileError,
    dump_tenants,
    load_tenants_file,
    save_tenants,
)
from quant_research import open_research

BASE = "http://127.0.0.1:8000"
PLANS = """
[plans.team]
toolsets = ["research", "analytics"]
calls_per_minute = 120
monthly_calls = 50_000

[plans.analytics-only]
toolsets = ["analytics"]
"""


def _names(executor: ToolExecutor) -> list[str]:
    return sorted(spec.name for spec in executor.registry.specs())


def test_plans_round_trip_with_the_tenants_that_name_them(tmp_path: Path) -> None:
    path = tmp_path / "tenants.toml"
    whole = TenantsFile(
        plans={
            "team": Plan(toolsets=["research", "analytics"], calls_per_minute=120),
            "free": Plan(monthly_calls=1_000, max_output_bytes=50_000),
        },
        tenants={"acme": TenantConfig(plan="team", toolsets=["research"])},
    )
    save_tenants(path, whole)

    assert load_tenants_file(path) == whole
    text = dump_tenants(whole)
    assert "[plans.team]" in text and 'plan = "team"' in text
    assert "monthly_calls = 1000" in text


@pytest.mark.parametrize(
    ("extra", "match"),
    [
        ('[tenants.acme]\nplan = "gold"\n', "plan 'gold' is not among the plans"),
        (
            '[tenants.acme]\nplan = "analytics-only"\ntoolsets = ["research"]\n',
            "lists research, which its plan 'analytics-only' does not include",
        ),
        ('[plans.packed]\npacks = ["factors-v1"]\n', "Knowledge packs are not available yet"),
        ('[plans."Gold Plan"]\n', "Plan name 'Gold Plan'"),
        ("[plans.zero]\ncalls_per_minute = 0\n", "calls_per_minute"),
    ],
)
def test_the_tenants_file_refuses_bad_plans(tmp_path: Path, extra: str, match: str) -> None:
    path = tmp_path / "tenants.toml"
    path.write_text(PLANS + extra)

    with pytest.raises(TenantsFileError, match=match):
        load_tenants_file(path)


def test_a_tenant_is_served_only_its_plans_toolsets(tmp_path: Path) -> None:
    store = tmp_path / "kb.db"
    open_research(store).close()
    whole = TenantsFile.model_validate(
        {
            "plans": {"analytics-only": {"toolsets": ["analytics"]}},
            "tenants": {
                "acme": {"store": str(store), "plan": "analytics-only"},
                "open": {"store": str(store)},
            },
        }
    )
    catalogs = TenantCatalogs(
        whole.tenants,
        run_store=MemoryRunStore(),
        datasets_root=tmp_path / "datasets",
        plans=whole.plans,
    )
    try:
        planned, unplanned = catalogs.surface("acme"), catalogs.surface("open")
        assert planned is not None and unplanned is not None
        assert set(planned.toolsets.values()) == {"analytics"}
        assert {"research", "analytics"} <= set(unplanned.toolsets.values())
        assert "find_related_signals" not in _names(planned.executor)
        assert not any(resource.tool == "get_source_passage" for resource in planned.resources)
    finally:
        catalogs.close()


def test_tenant_add_takes_a_known_plan_and_keeps_the_plans(tmp_path: Path) -> None:
    path = tmp_path / "tenants.toml"
    path.write_text(PLANS)
    runner = CliRunner()

    added = runner.invoke(cli, ["tenant", "add", "acme", "--plan", "team", "--tenants", str(path)])
    unknown = runner.invoke(
        cli, ["tenant", "add", "beta", "--plan", "gold", "--tenants", str(path)]
    )
    widened = runner.invoke(
        cli,
        [
            "tenant",
            "add",
            "gamma",
            "--plan",
            "analytics-only",
            "--toolsets",
            "research",
            "--tenants",
            str(path),
        ],
    )
    listed = runner.invoke(cli, ["tenant", "list", "--tenants", str(path)])
    whole = load_tenants_file(path)

    assert added.exit_code == 0 and whole.tenants["acme"].plan == "team"
    assert set(whole.plans) == {"team", "analytics-only"}
    assert unknown.exit_code == 1 and "not among the plans" in unknown.output
    assert widened.exit_code == 1 and "beta" not in whole.tenants and "gamma" not in whole.tenants
    assert listed.exit_code == 0 and "team" in listed.output


def _limited(tmp_path: Path) -> tuple[Any, dict[str, str], MemoryRunStore, TenantCatalogs]:
    whole = TenantsFile.model_validate(
        {
            "plans": {
                "per-minute": {"toolsets": ["analytics"], "calls_per_minute": 1},
                "per-month": {"toolsets": ["analytics"], "monthly_calls": 1},
            },
            "tenants": {"acme": {"plan": "per-minute"}, "beta": {"plan": "per-month"}},
        }
    )
    keys_path = tmp_path / "keys.toml"
    issued = {
        tenant: add_key(keys_path, f"{tenant}-user", ["tools:list", "tools:call"], tenant=tenant)
        for tenant in ("acme", "beta")
    }
    store = MemoryRunStore()
    catalogs = TenantCatalogs(
        whole.tenants, run_store=store, datasets_root=tmp_path / "datasets", plans=whole.plans
    )
    app = build_http_app(
        catalogs, KeyRing(load_keys(keys_path)), instructions=instructions_for(["analytics"])
    )
    return app, issued, store, catalogs


def test_rest_answers_429_with_retry_after_once_the_rate_is_used(tmp_path: Path) -> None:
    app, issued, store, catalogs = _limited(tmp_path)

    async def main() -> list[httpx.Response]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=BASE,
            headers={"Authorization": f"Bearer {issued['acme']}"},
        ) as client:
            return [
                await client.post("/v1/tools/list_datasets/call", json={"arguments": {}})
                for _ in range(2)
            ]

    try:
        first, second = anyio.run(main)
    finally:
        catalogs.close()

    assert first.status_code == 200 and first.json()["status"] == "ok"
    assert second.status_code == 429 and second.headers["Retry-After"] == "60"
    assert second.json()["status"] == "quota_exceeded"
    assert second.json()["retry_after_seconds"] == 60
    usage = store.list_usage(datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC))
    assert [(event.status, event.billable) for event in usage] == [
        ("ok", True),
        ("quota_exceeded", False),
    ]


def test_mcp_returns_a_structured_refusal_once_the_month_is_used(tmp_path: Path) -> None:
    app, issued, _, catalogs = _limited(tmp_path)

    async def main() -> list[Any]:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=BASE,
                headers={"Authorization": f"Bearer {issued['beta']}"},
            ) as client,
            streamable_http_client(f"{BASE}/mcp", http_client=client) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            return [await session.call_tool("list_datasets", {}) for _ in range(2)]

    try:
        first, second = anyio.run(main)
    finally:
        catalogs.close()

    assert not first.isError
    assert second.isError and second.structuredContent is not None
    assert second.structuredContent["status"] == "quota_exceeded"
    assert "used its 1 calls for" in second.structuredContent["error"]
    assert second.structuredContent["retry_after_seconds"] >= 1
