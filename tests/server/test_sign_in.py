"""Opt-in OAuth sign-in on a firm's own server (decision 0028), through `serve_http` itself.

A fake identity provider answers RFC 7662 introspection through httpx's mock transport, so the
tests run offline. Off by default, nothing changes; on, tokens and keys both reach /mcp and /v1,
the token's groups decide access, and anything uncertain is refused.
"""

import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from typer.testing import CliRunner

from quant_agent_os.api import ApiKeyError, add_key
from quant_agent_os.config import get_settings
from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.runs import SqliteRunStore
from quant_agent_os.tools import FileAccessPolicy, PermissionPolicy
from quant_platform import Catalog, compose, serve_http
from quant_platform.cli import app as cli
from quant_platform.identity.sign_in import SignIn, SignInError, load_sign_in

pytest.importorskip("empyrical")

RESOURCE = "https://qaos.kestrel.example/mcp"
PUBLIC = "https://qaos.kestrel.example"
ISSUER = "https://login.kestrel.example"
SECRET = "intro-s3cret-value"
SECRET_ENV = "KESTREL_INTROSPECTION_SECRET"
SIGN_IN = f"""
resource = "{RESOURCE}"

[[authorization_servers]]
issuer = "{ISSUER}"
introspection_url = "{ISSUER}/oauth2/introspect"
client_id = "qaos"
client_secret_env = "{SECRET_ENV}"
"""
RULES = """
[groups.equity]
tools = ["analyze_performance", "list_datasets"]
datasets = ["equity-*"]

[groups.macro]
tools = ["analyze_performance", "list_datasets"]
datasets = ["macro-*"]
"""
SERIES = [0.012, -0.004, 0.021, -0.015, 0.008, 0.017, -0.006, 0.011, 0.002, -0.009, 0.014, 0.005]


@dataclass
class Provider:
    """The fake identity provider: its tokens, and every introspection it was asked."""

    tokens: dict[str, dict[str, Any]] = field(default_factory=dict)
    asked: list[str] = field(default_factory=list)
    authorizations: list[str] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.authorizations.append(request.headers.get("authorization", ""))
        token = dict(httpx.QueryParams(request.content.decode()))["token"]
        self.asked.append(token)
        return httpx.Response(200, json=self.tokens.get(token, {"active": False}))

    def issue(self, token: str, **overrides: Any) -> str:
        self.tokens[token] = {
            "active": True,
            "iss": ISSUER,
            "aud": RESOURCE,
            "exp": int(time.time()) + 600,
            "sub": f"{token}-user",
            "client_id": "claude",
            "scope": "tools:list tools:call",
            "qaos_tenant": "local",
            "groups": ["equity"],
        } | overrides
        return token


@dataclass
class Started:
    app: Any
    output: str
    runs: Path
    result: Any = None


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> Provider:
    """Route every sign-in verifier's introspections to the fake provider."""
    fake = Provider()
    real = SignIn.verifier

    def verifier(self: SignIn, http: httpx.AsyncClient | None = None) -> Any:
        return real(self, httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))

    monkeypatch.setattr(SignIn, "verifier", verifier)
    return fake


@pytest.fixture
def store(tmp_path: Path) -> DirectoryDatasetStore:
    datasets = DirectoryDatasetStore(tmp_path / "datasets")
    for name in ("equity-book", "macro-book"):
        source = tmp_path / f"{name}.csv"
        source.write_text(
            "month,fund\n" + "".join(f"2024-{m + 1:02d},{v}\n" for m, v in enumerate(SERIES))
        )
        datasets.put(name, source)
    return datasets


@pytest.fixture
def sign_in_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "sign-in.toml"
    path.write_text(SIGN_IN, "utf-8")
    monkeypatch.setenv(SECRET_ENV, SECRET)
    return path


@pytest.fixture
def catalog(store: DirectoryDatasetStore, tmp_path: Path) -> Catalog:
    rules = tmp_path / "access.toml"
    rules.write_text(RULES, "utf-8")
    return compose(policy=PermissionPolicy(), datasets=store, access=FileAccessPolicy(rules))


@pytest.fixture
def start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> Iterator[Callable[..., Started]]:
    """Run `serve_http` up to listening; return the app it would serve and what it printed.

    ``during`` runs while the server is up (its run store open), and its result is kept.
    """
    served: list[tuple[Any, Any]] = []
    runs = tmp_path / "runs.db"

    def run(catalog: Catalog, during: Callable[[Any], Any] | None = None, **kwargs: Any) -> Started:
        def fake_run(app: Any, **_: Any) -> None:
            served.append((app, during(app) if during is not None else None))

        monkeypatch.setattr("quant_platform.serve.run_http", fake_run)
        kwargs.setdefault("keys", tmp_path / "keys.toml")
        serve_http(catalog, runs=runs, **kwargs)
        app, result = served[-1]
        return Started(app, " ".join(capsys.readouterr().err.split()), runs, result)

    yield run


def _rest(app: Any, credential: str | None, method: str, path: str, **kwargs: Any) -> Any:
    headers = {"Authorization": f"Bearer {credential}"} if credential else {}

    async def main() -> httpx.Response:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC, headers=headers
            ) as client,
        ):
            return await client.request(method, path, **kwargs)

    return anyio.run(main)


def _call(app: Any, credential: str, dataset: str) -> Any:
    body = {"arguments": {"returns": {"dataset": dataset, "column": "fund"}}}
    return _rest(app, credential, "POST", "/v1/tools/analyze_performance/call", json=body)


def _mcp(app: Any, credential: str, dataset: str) -> Any:
    async def main() -> Any:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=PUBLIC,  # the resource URL's host is admitted without --allowed-host
                headers={"Authorization": f"Bearer {credential}"},
            ) as client,
            streamable_http_client(f"{PUBLIC}/mcp", http_client=client) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools = [tool.name for tool in (await session.list_tools()).tools]
            called = await session.call_tool(
                "analyze_performance", {"returns": {"dataset": dataset, "column": "fund"}}
            )
            return tools, called

    return anyio.run(main)


# Off by default


def test_without_a_sign_in_file_nothing_changes(
    catalog: Catalog, tmp_path: Path, provider: Provider, start: Callable[..., Started]
) -> None:
    keys = tmp_path / "keys.toml"
    keys.write_text("", "utf-8")
    with pytest.raises(ApiKeyError, match="no enabled key"):
        start(catalog)

    key = add_key(keys, "svc", ["tools:list"], groups=["equity"])
    started = start(catalog)
    token = provider.issue("tok-off")

    refused = _rest(started.app, token, "GET", "/v1/tools")
    assert refused.status_code == 401 and refused.headers["www-authenticate"] == "Bearer"
    assert refused.json()["detail"] == "Unknown or revoked API key."
    metadata = _rest(started.app, None, "GET", "/.well-known/oauth-protected-resource/mcp")
    assert metadata.status_code == 404
    assert _rest(started.app, key, "GET", "/v1/tools").status_code == 200
    assert provider.asked == []  # no token ever leaves the server
    assert "signed in" not in started.output


# On: tokens and keys


def test_a_signed_in_user_reaches_tools_over_rest_and_mcp_and_is_recorded(
    catalog: Catalog, sign_in_file: Path, provider: Provider, start: Callable[..., Started]
) -> None:
    token = provider.issue("tok-pm")

    def day(app: Any) -> Any:
        return (
            _rest(app, token, "GET", "/v1/tools"),
            _call(app, token, "equity-book"),
            *_mcp(app, token, "equity-book"),
        )

    # No keys file at all: sign-in suffices.
    started = start(catalog, during=day, sign_in=sign_in_file)
    listed, called, tools, by_mcp = started.result

    assert sorted(item["name"] for item in listed.json()) == [
        "analyze_performance",
        "list_datasets",
    ]
    assert called.status_code == 200 and called.json()["status"] == "ok"
    assert sorted(tools) == ["analyze_performance", "list_datasets"]
    assert by_mcp.isError is False
    assert provider.authorizations[0].startswith("Basic ")  # the secret is sent, Basic auth
    assert provider.asked == ["tok-pm"]  # cached afterwards
    assert f"to 0 keys and to users signed in by {ISSUER}" in started.output
    with SqliteRunStore(started.runs) as runs:
        recorded = {(run.actor, run.principal, run.tenant) for run in runs.list_runs(10)}
    assert recorded == {("rest", "tok-pm-user", "local"), ("mcp-http", "tok-pm-user", "local")}


def test_api_keys_keep_working_beside_tokens(
    catalog: Catalog,
    sign_in_file: Path,
    provider: Provider,
    tmp_path: Path,
    start: Callable[..., Started],
) -> None:
    key = add_key(tmp_path / "keys.toml", "svc", ["tools:list", "tools:call"], groups=["macro"])
    started = start(catalog, sign_in=sign_in_file)

    assert _call(started.app, key, "macro-book").json()["status"] == "ok"
    assert _mcp(started.app, key, "macro-book")[1].isError is False
    revoked = "qaos_" + "x" * 43
    assert _rest(started.app, revoked, "GET", "/v1/tools").status_code == 401
    assert provider.asked == []  # a key, even an unknown one, is never introspected


@pytest.mark.parametrize(
    "overrides",
    [
        {"active": False},
        {"exp": int(time.time()) - 1},
        {"aud": "https://other.example/mcp"},
        {"iss": "https://login.other.example"},
        {"qaos_tenant": "another-fund"},
        {"qaos_tenant": None},
        {"groups": "equity"},  # not a list: uncertain, so refused rather than read as no groups
    ],
    ids=["inactive", "expired", "audience", "issuer", "tenant", "no-tenant", "groups-shape"],
)
def test_uncertain_tokens_are_refused(
    catalog: Catalog,
    sign_in_file: Path,
    provider: Provider,
    start: Callable[..., Started],
    overrides: dict[str, Any],
) -> None:
    started = start(catalog, sign_in=sign_in_file)
    token = provider.issue("tok-bad", **overrides)

    refused = _rest(started.app, token, "GET", "/v1/tools")
    by_mcp = _rest(started.app, token, "POST", "/mcp", json={})

    assert refused.status_code == 401 and by_mcp.status_code == 401
    assert 'resource_metadata="' in refused.headers["www-authenticate"]


def test_a_token_lacking_a_scope_is_refused_that_route(
    catalog: Catalog, sign_in_file: Path, provider: Provider, start: Callable[..., Started]
) -> None:
    started = start(catalog, sign_in=sign_in_file)
    token = provider.issue("tok-lister", scope="tools:list runs:approve")

    assert _rest(started.app, token, "GET", "/v1/tools").status_code == 200
    refused = _call(started.app, token, "equity-book")
    assert refused.status_code == 403 and "lacks the 'tools:call' scope" in refused.text


def test_the_groups_claim_decides_access_and_a_refused_dataset_looks_missing(
    catalog: Catalog, sign_in_file: Path, provider: Provider, start: Callable[..., Started]
) -> None:
    started = start(catalog, sign_in=sign_in_file)
    equity = provider.issue("tok-equity", groups=["equity"])
    macro = provider.issue("tok-macro", groups=["macro"])
    nobody = provider.issue("tok-nobody", groups=[])

    refused = _call(started.app, equity, "macro-book").json()
    missing = _call(started.app, equity, "no-such-book").json()

    assert _call(started.app, macro, "macro-book").json()["status"] == "ok"
    assert refused["status"] == "error" == missing["status"]
    assert refused["error"].replace("macro-book", "X") == missing["error"].replace(
        "no-such-book", "X"
    )
    assert "macro" not in refused["error"].replace("macro-book", "")
    assert _rest(started.app, nobody, "GET", "/v1/tools").json() == []


def test_a_provider_with_no_tenant_claim_pins_its_users_to_the_tenant(
    catalog: Catalog, sign_in_file: Path, provider: Provider, start: Callable[..., Started]
) -> None:
    sign_in_file.write_text(SIGN_IN + 'tenant_claim = ""\ngroups_claim = "roles"\n', "utf-8")
    started = start(catalog, sign_in=sign_in_file)
    token = provider.issue("tok-entra", qaos_tenant=None, groups=None, roles=["equity"])

    assert _call(started.app, token, "equity-book").json()["status"] == "ok"


def test_the_pilot_gateway_configuration_is_accepted(
    catalog: Catalog,
    sign_in_file: Path,
    provider: Provider,
    start: Callable[..., Started],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W1's mock provider: loopback http, its own tenant name, and groups in `groups`."""
    gateway = "http://127.0.0.1:9400"
    sign_in_file.write_text(
        f'resource = "http://127.0.0.1:8000/mcp"\n\n[[authorization_servers]]\n'
        f'issuer = "{gateway}"\nintrospection_url = "{gateway}/introspect"\n'
        f'client_id = "qaos-server"\nclient_secret_env = "{SECRET_ENV}"\n'
        'tenant_claim_value = "kestrel"\n',
        "utf-8",
    )
    started = start(catalog, sign_in=sign_in_file)
    local = {"iss": gateway, "aud": "http://127.0.0.1:8000/mcp"}
    pm = provider.issue("tok-kestrel", qaos_tenant="kestrel", **local)
    stranger = provider.issue("tok-other-fund", qaos_tenant="local", **local)

    assert _call(started.app, pm, "equity-book").json()["status"] == "ok"
    assert _rest(started.app, stranger, "GET", "/v1/tools").status_code == 401


def test_a_tenant_claim_value_without_a_claim_is_refused(sign_in_file: Path) -> None:
    sign_in_file.write_text(SIGN_IN + 'tenant_claim = ""\ntenant_claim_value = "kestrel"\n')

    with pytest.raises(SignInError, match="needs a tenant_claim"):
        load_sign_in(sign_in_file)


def test_the_protected_resource_metadata_is_published(
    catalog: Catalog, sign_in_file: Path, provider: Provider, start: Callable[..., Started]
) -> None:
    started = start(catalog, sign_in=sign_in_file)

    metadata = _rest(started.app, None, "GET", "/.well-known/oauth-protected-resource/mcp")
    anonymous = _rest(started.app, None, "GET", "/v1/tools")

    assert metadata.json()["resource"] == RESOURCE
    assert metadata.json()["authorization_servers"] == [f"{ISSUER}/"]
    assert "tools:call" in metadata.json()["scopes_supported"]
    url = f"{PUBLIC}/.well-known/oauth-protected-resource/mcp"
    assert anonymous.headers["www-authenticate"] == f'Bearer resource_metadata="{url}"'


def test_sign_in_without_an_access_file_is_called_out(
    sign_in_file: Path,
    provider: Provider,
    store: DirectoryDatasetStore,
    start: Callable[..., Started],
) -> None:
    started = start(compose(policy=PermissionPolicy(), datasets=store), sign_in=sign_in_file)

    assert "sign-in is on, but there is no access file" in started.output


def test_the_setting_turns_sign_in_on_and_stdio_says_it_does_not_apply(
    catalog: Catalog,
    sign_in_file: Path,
    provider: Provider,
    monkeypatch: pytest.MonkeyPatch,
    start: Callable[..., Started],
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("QUANT_AGENT_OS_SIGN_IN_FILE", str(sign_in_file))
    get_settings.cache_clear()
    started = start(catalog)
    assert _call(started.app, provider.issue("tok-env"), "equity-book").status_code == 200

    from quant_platform import serve_stdio

    monkeypatch.setattr("quant_platform.serve.run_stdio", lambda *a, **k: None)
    serve_stdio(compose(policy=PermissionPolicy()), record=False)
    assert "sign-in does not apply" in " ".join(capsys.readouterr().err.split())


# Misconfiguration refuses to start


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda s: s.replace("https://login.kestrel.example/oauth2", "http://idp.example"),
            "https",
        ),
        (lambda s: s.replace(f'"{RESOURCE}"', '"http://qaos.kestrel.example/mcp"'), "https"),
        (lambda s: s + 'tenent = "local"\n', "Extra inputs"),
        (lambda s: s + 'tenant = "another-fund"\n', "serves tenant 'local' only"),
        (lambda s: s.replace("[[authorization_servers]]", "[[unused]]"), "authorization_servers"),
        (lambda s: s + s[s.index("[[") :], "an issuer appears twice"),
        (lambda s: "not toml [", "Invalid sign-in file"),
    ],
    ids=["http-introspection", "http-resource", "typo", "tenant", "no-server", "twice", "toml"],
)
def test_a_bad_sign_in_file_refuses_to_start(
    sign_in_file: Path,
    catalog: Catalog,
    start: Callable[..., Started],
    change: Callable[[str], str],
    message: str,
) -> None:
    sign_in_file.write_text(change(SIGN_IN), "utf-8")

    with pytest.raises(SignInError, match=message):
        start(catalog, sign_in=sign_in_file)


def test_plain_http_is_allowed_on_loopback(sign_in_file: Path) -> None:
    local = SIGN_IN.replace(ISSUER, "http://127.0.0.1:9400").replace(
        RESOURCE, "http://localhost:8000/mcp"
    )
    sign_in_file.write_text(local, "utf-8")

    assert load_sign_in(sign_in_file).issuers == ["http://127.0.0.1:9400"]


def test_qaos_serve_refuses_a_missing_secret_or_file_and_shows_no_secret(
    sign_in_file: Path,
    store: DirectoryDatasetStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("quant_platform.serve.run_http", lambda *a, **k: None)
    args = ["serve", "--http", "--toolsets", "analytics", "--datasets", str(store.root)]
    args += ["--runs", str(tmp_path / "runs.db"), "--keys", str(tmp_path / "keys.toml")]

    started = CliRunner().invoke(cli, [*args, "--sign-in", str(sign_in_file)])
    assert started.exit_code == 0, started.output
    assert SECRET not in started.output

    monkeypatch.delenv(SECRET_ENV)
    unset = CliRunner().invoke(cli, [*args, "--sign-in", str(sign_in_file)])
    assert unset.exit_code == 1 and f"Set {SECRET_ENV}" in unset.output

    absent = CliRunner().invoke(cli, [*args, "--sign-in", str(tmp_path / "absent.toml")])
    assert absent.exit_code == 1 and "No sign-in file" in absent.output

    stdio = CliRunner().invoke(cli, ["serve", "--sign-in", str(sign_in_file)])
    assert stdio.exit_code == 2 and "need --http" in stdio.output


def test_the_secret_can_come_from_the_homes_env_file(
    sign_in_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(SECRET_ENV)
    home = Path(get_settings().runs_file).parent
    home.mkdir(parents=True, exist_ok=True)
    (home / ".env").write_text(f"{SECRET_ENV}={SECRET}\n", "utf-8")

    (server,) = load_sign_in(sign_in_file).servers
    assert server.client_secret.get_secret_value() == SECRET
    assert SECRET not in repr(server) and SECRET not in str(load_sign_in(sign_in_file))


def test_the_secret_never_reaches_the_logs(
    catalog: Catalog,
    sign_in_file: Path,
    provider: Provider,
    start: Callable[..., Started],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    started = start(catalog, sign_in=sign_in_file)
    token = provider.issue("tok-wrong-audience", aud="https://other.example/mcp")

    _rest(started.app, token, "GET", "/v1/tools")

    assert "Refused a token from" in caplog.text and "audience" in caplog.text
    assert SECRET not in caplog.text and SECRET not in started.output
    assert token not in caplog.text


# qaos doctor


def _doctor(monkeypatch: pytest.MonkeyPatch, sign_in: Path | None) -> tuple[int, str]:
    if sign_in is not None:
        monkeypatch.setenv("QUANT_AGENT_OS_SIGN_IN_FILE", str(sign_in))
    get_settings.cache_clear()
    result = CliRunner().invoke(cli, ["doctor"], env={"COLUMNS": "1000"})
    return result.exit_code, " ".join(result.output.split())


def test_doctor_reports_sign_in_without_contacting_the_provider(
    sign_in_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_network(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("doctor must not contact the identity provider")

    monkeypatch.setattr(httpx.AsyncClient, "post", no_network)
    monkeypatch.setattr(httpx.Client, "post", no_network)

    code, off = _doctor(monkeypatch, None)
    assert "sign-in" in off and "takes API keys only" in off

    code, on = _doctor(monkeypatch, sign_in_file)
    assert f"sign-in: {ISSUER}" in on and "complete; tenant local" in on
    assert "every signed-in user reaches everything" in on
    assert SECRET not in on

    monkeypatch.delenv(SECRET_ENV)
    code, unset = _doctor(monkeypatch, sign_in_file)
    assert code == 1 and f"set {SECRET_ENV}" in unset

    sign_in_file.write_text(SIGN_IN.replace("https://login", "http://login"), "utf-8")
    code, bad = _doctor(monkeypatch, sign_in_file)
    assert code == 1 and "must use https" in bad
