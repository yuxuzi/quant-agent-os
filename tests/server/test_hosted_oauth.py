"""OAuth 2.1 tokens for remote MCP (B-34, decision 0013): introspection, audience, issuers."""

import base64
import json
import time
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from portfolio_tools import build_registry
from pydantic import SecretStr

from quant_agent_os.api import KeyRing, add_key, build_app, load_keys
from quant_agent_os.mcp.http import KeyVerifier, add_mcp_route
from quant_agent_os.runs import MemoryRunStore
from quant_agent_os.runtime import ToolExecutor
from quant_agent_os.serving import Surface
from quant_agent_os.tools import PermissionPolicy
from quant_platform.hosted.oauth import (
    AuthorizationServer,
    FirstOf,
    IntrospectionVerifier,
    protected_resource_routes,
)

RESOURCE = "https://qaos.example/mcp"
ACME = "https://login.acme.example"
GLOBEX = "https://login.globex.example"
ARGUMENTS = {"series_id": "s", "returns": [0.01, -0.02, 0.03], "periods_per_year": 12}


def _jwt(issuer: str, marker: str) -> str:
    """A JWT-shaped token: only its issuer is read, unverified, to choose a provider."""

    def part(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return f"{part({'alg': 'RS256'})}.{part({'iss': issuer, 'jti': marker})}.signature"


class FakeProvider:
    """Introspection endpoints for two providers; records what each was asked."""

    def __init__(self) -> None:
        self.tokens: dict[str, dict[str, Any]] = {}
        self.asked: list[tuple[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("Basic ")
        token = dict(httpx.QueryParams(request.content.decode()))["token"]
        self.asked.append((request.url.host, token))
        return httpx.Response(200, json=self.tokens.get(token, {"active": False}))

    def claims(self, issuer: str, tenant: str, **overrides: Any) -> dict[str, Any]:
        return {
            "active": True,
            "iss": issuer,
            "aud": [RESOURCE],
            "exp": int(time.time()) + 600,
            "sub": "user-7",
            "client_id": "claude",
            "scope": "tools:list tools:call admin:all",
            "qaos_tenant": tenant,
        } | overrides


def _server(issuer: str) -> AuthorizationServer:
    host = issuer.removeprefix("https://")
    return AuthorizationServer(
        issuer=issuer,
        introspection_url=f"https://{host}/introspect",
        client_id="qaos",
        client_secret=SecretStr("s3cret"),
    )


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


def _verifier(provider: FakeProvider, *issuers: str) -> IntrospectionVerifier:
    return IntrospectionVerifier(
        [_server(issuer) for issuer in issuers],
        resource=RESOURCE,
        tenants_by_issuer={ACME: {"acme"}, GLOBEX: {"globex"}},
        http=httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)),
    )


def _verify(verifier: IntrospectionVerifier, token: str) -> Any:
    return anyio.run(verifier.verify_token, token)


def test_an_active_token_for_this_server_names_its_tenant_and_known_scopes(
    provider: FakeProvider,
) -> None:
    token = _jwt(ACME, "a")
    provider.tokens[token] = provider.claims(ACME, "acme")
    verifier = _verifier(provider, ACME, GLOBEX)

    access = _verify(verifier, token)
    again = _verify(verifier, token)

    assert access is not None and again == access
    assert access.claims["tenant"] == "acme" and access.subject == "user-7"
    assert access.scopes == ["tools:list", "tools:call"]  # unknown scopes are dropped
    assert provider.asked == [("login.acme.example", token)]  # cached, and asked only acme


@pytest.mark.parametrize(
    "overrides",
    [
        {"active": False},
        {"exp": int(time.time()) - 1},
        {"aud": ["https://other.example/mcp"]},
        {"qaos_tenant": "globex"},  # globex is pinned to another issuer
        {"qaos_tenant": None},
        {"iss": GLOBEX},  # the provider says another issuer issued it
    ],
)
def test_tokens_that_are_inactive_expired_foreign_or_unpinned_are_refused(
    provider: FakeProvider, overrides: dict[str, Any]
) -> None:
    token = _jwt(ACME, "b")
    provider.tokens[token] = provider.claims(ACME, "acme", **overrides)

    assert _verify(_verifier(provider, ACME, GLOBEX), token) is None


def test_a_token_is_never_sent_to_a_provider_that_cannot_have_issued_it(
    provider: FakeProvider,
) -> None:
    two = _verifier(provider, ACME, GLOBEX)

    assert _verify(two, _jwt("https://evil.example", "c")) is None  # unknown issuer
    assert _verify(two, "opaque-token") is None  # opaque, two providers: asking would leak it
    assert _verify(two, "qaos_an-api-key") is None  # an API key never leaves the server
    assert provider.asked == []

    provider.tokens["opaque-token"] = provider.claims(ACME, "acme")
    assert _verify(_verifier(provider, ACME), "opaque-token") is not None
    assert provider.asked == [("login.acme.example", "opaque-token")]


def test_a_provider_failure_refuses_the_token_without_logging_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    verifier = IntrospectionVerifier(
        [_server(ACME)],
        resource=RESOURCE,
        tenants_by_issuer={ACME: {"acme"}},
        http=httpx.AsyncClient(transport=httpx.MockTransport(down)),
    )
    token = _jwt(ACME, "secret-token-value")

    assert _verify(verifier, token) is None
    assert "introspection" in caplog.text and token not in caplog.text


# Bounded introspection (security review F-1)


def test_a_refusal_is_cached_but_a_provider_failure_is_not(provider: FakeProvider) -> None:
    verifier = _verifier(provider, ACME)
    refused = _jwt(ACME, "refused")

    assert _verify(verifier, refused) is None
    assert _verify(verifier, refused) is None
    assert provider.asked == [("login.acme.example", refused)]

    calls: list[str] = []

    def down(request: httpx.Request) -> httpx.Response:
        calls.append("asked")
        raise httpx.ConnectError("down", request=request)

    flaky = IntrospectionVerifier(
        [_server(ACME)],
        resource=RESOURCE,
        tenants_by_issuer={ACME: {"acme"}},
        http=httpx.AsyncClient(transport=httpx.MockTransport(down)),
    )
    _verify(flaky, refused)
    _verify(flaky, refused)
    assert calls == ["asked", "asked"]


def test_each_provider_is_asked_at_most_its_budget_and_a_flood_warns_once(
    provider: FakeProvider, caplog: pytest.LogCaptureFixture
) -> None:
    verifier = IntrospectionVerifier(
        [_server(ACME), _server(GLOBEX)],
        resource=RESOURCE,
        tenants_by_issuer={ACME: {"acme"}, GLOBEX: {"globex"}},
        http=httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)),
        introspections_per_minute=3,
    )
    valid = _jwt(GLOBEX, "valid")
    provider.tokens[valid] = provider.claims(GLOBEX, "globex")

    for marker in range(10):  # a flood of different made-up tokens naming acme
        assert _verify(verifier, _jwt(ACME, f"made-up-{marker}")) is None

    assert [host for host, _ in provider.asked] == ["login.acme.example"] * 3
    assert caplog.text.count("budget is used up") == 1
    assert _verify(verifier, valid) is not None  # globex has its own budget


def test_introspections_in_flight_per_provider_are_bounded(provider: FakeProvider) -> None:
    release = anyio.Event()
    asked: list[str] = []

    async def slow(request: httpx.Request) -> httpx.Response:
        asked.append("asked")
        await release.wait()
        return httpx.Response(200, json={"active": False})

    verifier = IntrospectionVerifier(
        [_server(ACME)],
        resource=RESOURCE,
        tenants_by_issuer={ACME: {"acme"}},
        http=httpx.AsyncClient(transport=httpx.MockTransport(slow)),
        max_concurrent=1,
    )
    results: list[Any] = []

    async def main() -> None:
        async with anyio.create_task_group() as group:
            group.start_soon(lambda: _collect(verifier, _jwt(ACME, "first"), results))
            await anyio.wait_all_tasks_blocked()
            results.append(await verifier.verify_token(_jwt(ACME, "second")))  # refused at once
            release.set()

    anyio.run(main)

    assert asked == ["asked"] and results == [None, None]


async def _collect(verifier: IntrospectionVerifier, token: str, into: list[Any]) -> None:
    into.append(await verifier.verify_token(token))


def test_mcp_clients_use_tokens_or_keys_and_401s_point_to_the_metadata(
    provider: FakeProvider, tmp_path: Path
) -> None:
    token = _jwt(ACME, "d")
    provider.tokens[token] = provider.claims(ACME, "acme")
    key = add_key(tmp_path / "keys.toml", "svc", ["tools:list"], tenant="acme")
    keys = KeyRing(load_keys(tmp_path / "keys.toml"))
    store = MemoryRunStore()

    class Acme:
        surface_ = Surface(ToolExecutor(build_registry(), PermissionPolicy()), run_store=store)

        def surface(self, tenant: str) -> Surface | None:
            return self.surface_ if tenant == "acme" else None

    routes, metadata_url = protected_resource_routes(RESOURCE, [ACME])
    app = build_app(None, keys, None, tenancy=Acme())
    app.router.routes.extend(routes)
    add_mcp_route(
        app,
        Acme(),
        name="test",
        verifier=FirstOf(KeyVerifier(keys), _verifier(provider, ACME)),
        resource_metadata_url=metadata_url,
    )

    async def main() -> Any:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
            ) as anonymous,
        ):
            refused = await anonymous.post("/mcp", json={})
            metadata = await anonymous.get("/.well-known/oauth-protected-resource/mcp")
            results = []
            for credential in (token, key):
                async with (
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://127.0.0.1:8000",
                        headers={"Authorization": f"Bearer {credential}"},
                    ) as client,
                    streamable_http_client("http://127.0.0.1:8000/mcp", http_client=client) as (
                        read,
                        write,
                        _,
                    ),
                    ClientSession(read, write) as session,
                ):
                    await session.initialize()
                    results.append(await session.call_tool("annualized_return", ARGUMENTS))
            return refused, metadata, results

    refused, metadata, (by_token, by_key) = anyio.run(main)

    assert refused.status_code == 401
    assert metadata_url in refused.headers["www-authenticate"]
    assert metadata.json()["authorization_servers"] == [f"{ACME}/"]
    assert metadata.json()["resource"] == RESOURCE
    assert by_token.isError is False
    assert by_key.isError and "lacks the 'tools:call' scope" in by_key.content[0].text
    (run,) = store.list_runs(5)
    assert (run.principal, run.tenant, run.actor) == ("user-7", "acme", "mcp-http")
