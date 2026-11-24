"""Sign-in by JWT, checked against the provider's keys (decision 0031), offline.

Fake Entra ID and Amazon Cognito issuers sign tokens with RSA keys made here and serve their
JWKS through httpx2's mock transport. Tokens reach the platform's HTTP app (`build_server_app`)
as a client would send them, and the access file is keyed by readable aliases of Entra's group
GUIDs. Every threat the verifier is meant to stop has a case that must be refused.
"""

import base64
import hashlib
import hmac
import json
import logging
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import anyio
import httpx2
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.runs import MemoryRunStore
from quant_agent_os.tools import FileAccessPolicy, PermissionPolicy
from quant_platform import build_server_app
from quant_platform.identity.jwks import JwksServer, JwksVerifier
from quant_platform.identity.oauth import FirstOf, IntrospectionVerifier
from quant_platform.identity.sign_in import SignIn, SignInError, load_sign_in

pytest.importorskip("empyrical")

RESOURCE = "https://qaos.kestrel.example/mcp"
TID = "b1d0c2de-7a61-4f9e-a3b2-5c0d9e8f1a22"
ENTRA = f"https://login.microsoftonline.com/{TID}/v2.0"
ENTRA_KEYS = f"https://login.microsoftonline.com/{TID}/discovery/v2.0/keys"
POOL = "https://cognito-idp.eu-west-1.amazonaws.com/eu-west-1_Kestrel"
CLIENT = "3n4b5v6c7x8z"
EQUITY = "6f1c2a10-0000-4000-8000-0000000e0001"
MACRO = "6f1c2a10-0000-4000-8000-0000000e0002"
SERIES = [0.012, -0.004, 0.021, -0.015, 0.008, 0.017, -0.006, 0.011, 0.002, -0.009, 0.014, 0.005]
RULES = """
[groups.research-eq]
tools = ["analyze_performance", "list_datasets"]
datasets = ["equity-*"]

[groups.research-macro]
tools = ["analyze_performance", "list_datasets"]
datasets = ["macro-*"]

[groups.desk]
tools = ["analyze_performance"]
datasets = ["equity-*"]
"""
ENTRA_FILE = f"""
resource = "{RESOURCE}"

[[authorization_servers]]
issuer = "{ENTRA}"
verify = "jwks"
jwks_uri = "{ENTRA_KEYS}"
audiences = ["api://kestrel-qaos"]
tenant_claim = ""
principal_claim = "preferred_username"
scope_claims = ["scp", "roles"]
groups_claims = ["groups", "roles"]
provider_tenant_claim = "tid"

[authorization_servers.scope_map]
"tools.list" = "tools:list"
"tools.call" = "tools:call"
"Tools.Call" = "tools:call"

[authorization_servers.group_aliases]
"{EQUITY}" = "research-eq"
"{MACRO}" = "research-macro"
"""
COGNITO_FILE = f"""
resource = "{RESOURCE}"

[[authorization_servers]]
issuer = "{POOL}"
verify = "jwks"
client_ids = ["{CLIENT}"]
token_use = "access"
tenant_claim = ""
scope_map = {{ "qaos/tools.call" = "tools:call", "qaos/tools.list" = "tools:list" }}
groups_claims = ["cognito:groups"]
"""


def b64(data: bytes) -> str:
    """Return URL-safe base64 without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def raw(header: dict[str, Any], payload: dict[str, Any], signature: bytes = b"") -> str:
    """Return a compact JWT from parts, with whatever signature is given."""
    parts = [b64(json.dumps(part).encode()) for part in (header, payload)]
    return ".".join([*parts, b64(signature)])


class Idp:
    """A fake identity provider: RSA keys by kid, a JWKS, and a discovery document."""

    def __init__(self, issuer: str, keys_url: str, *, discovery: bool = False) -> None:
        self.issuer = issuer
        self.keys_url = keys_url
        self.discovery = discovery
        self.keys: dict[str, Any] = {}
        self.fetches = 0
        self.failing = False
        self.add_key("k1")

    def add_key(self, kid: str) -> None:
        """Start publishing a new signing key."""
        self.keys[kid] = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def public_pem(self, kid: str = "k1") -> bytes:
        """Return a key's public half in PEM form."""
        return (
            self.keys[kid]
            .public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )

    def jwks(self) -> dict[str, Any]:
        """Return the published key set."""
        keys = []
        for kid, key in self.keys.items():
            numbers = key.public_key().public_numbers()
            as_b64 = lambda n: b64(n.to_bytes((n.bit_length() + 7) // 8, "big"))  # noqa: E731
            keys.append(
                {
                    "kty": "RSA",
                    "use": "sig",
                    "kid": kid,
                    "n": as_b64(numbers.n),
                    "e": as_b64(numbers.e),
                }
            )
        return {"keys": keys}

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        """Answer the key and discovery requests, or fail them while `failing`."""
        if self.failing:
            return httpx2.Response(503)
        url = str(request.url)
        if url == self.keys_url:
            self.fetches += 1
            return httpx2.Response(200, json=self.jwks())
        if self.discovery and url == f"{self.issuer}/.well-known/openid-configuration":
            return httpx2.Response(200, json={"issuer": self.issuer, "jwks_uri": self.keys_url})
        return httpx2.Response(404)

    def sign(
        self, claims: dict[str, Any], *, kid: str | None = "k1", alg: str = "RS256", key: Any = None
    ) -> str:
        """Return a JWT for ``claims`` signed by one of this provider's keys (or ``key``)."""
        private = (key or self.keys.get(kid or "k1", self.keys["k1"])).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        headers = {"kid": kid} if kid is not None else {}
        return jwt.encode(claims, private, algorithm=alg, headers=headers)


def entra_claims(**overrides: Any) -> dict[str, Any]:
    """Return an Entra v2.0 delegated access token's claims for a research analyst."""
    now = int(time.time())
    return {
        "iss": ENTRA,
        "aud": "api://kestrel-qaos",
        "exp": now + 600,
        "nbf": now - 5,
        "iat": now - 5,
        "tid": TID,
        "oid": "a0000000-0000-4000-8000-000000000002",
        "sub": "pairwise-subject",
        "preferred_username": "dev.shah@kestrel.example",
        "scp": "tools.list tools.call",
        "groups": [EQUITY],
        "ver": "2.0",
    } | overrides


def cognito_claims(**overrides: Any) -> dict[str, Any]:
    """Return an Amazon Cognito access token's claims: no ``aud``, a ``client_id``."""
    now = int(time.time())
    return {
        "iss": POOL,
        "sub": "7c1f-user",
        "client_id": CLIENT,
        "token_use": "access",
        "scope": "openid qaos/tools.list qaos/tools.call",
        "exp": now + 600,
        "iat": now - 5,
        "cognito:groups": ["research-macro"],
        "username": "ines",
    } | overrides


@pytest.fixture
def entra() -> Idp:
    return Idp(ENTRA, ENTRA_KEYS)


@pytest.fixture
def cognito() -> Idp:
    return Idp(POOL, f"{POOL}/.well-known/jwks.json", discovery=True)


@pytest.fixture
def idps(monkeypatch: pytest.MonkeyPatch, entra: Idp, cognito: Idp) -> list[Idp]:
    """Answer every verifier's key requests from the fake providers."""
    providers = [entra, cognito]

    def handler(request: httpx2.Request) -> httpx2.Response:
        for idp in providers:
            response = idp.handler(request)
            if response.status_code != 404:
                return response
        return httpx2.Response(404)

    real = SignIn.verifier
    monkeypatch.setattr(
        SignIn,
        "verifier",
        lambda self, http=None: real(
            self, httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        ),
    )
    return providers


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


class Server:
    """The firm's HTTP app, with a client that sends a token as a connector would."""

    def __init__(self, client: TestClient, runs: MemoryRunStore) -> None:
        self.client = client
        self.runs = runs

    def call(self, token: str, dataset: str = "equity-book") -> httpx2.Response:
        """Call analyze_performance on ``dataset`` with ``token``."""
        body = {"arguments": {"returns": {"dataset": dataset, "column": "fund"}}}
        return self.client.post(
            "/v1/tools/analyze_performance/call",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )

    def listed(self, token: str) -> httpx2.Response:
        """List the tools ``token`` may see."""
        return self.client.get("/v1/tools", headers={"Authorization": f"Bearer {token}"})


@pytest.fixture
def serve(
    tmp_path: Path, store: DirectoryDatasetStore, idps: list[Idp]
) -> Iterator[Callable[[str], Server]]:
    """Start the app for a sign-in file's text; every app started is closed at the end."""
    opened: list[Any] = []

    def start(text: str) -> Server:
        rules = tmp_path / "access.toml"
        rules.write_text(RULES, "utf-8")
        sign_in = tmp_path / f"sign-in-{len(opened)}.toml"
        sign_in.write_text(text, "utf-8")
        runs = MemoryRunStore()
        manager = build_server_app(
            [],
            datasets=store,
            policy=PermissionPolicy(),
            access=FileAccessPolicy(rules),
            keys=tmp_path / "keys.toml",
            runs=runs,
            sign_in=sign_in,
        )
        app = manager.__enter__()
        client = TestClient(app, base_url="https://qaos.kestrel.example")
        client.__enter__()
        opened.append((manager, client))
        return Server(client, runs)

    yield start
    for manager, client in opened:
        client.__exit__(None, None, None)
        manager.__exit__(None, None, None)


# What a signed-in user and a machine reach


def test_an_entra_user_reaches_what_their_aliased_groups_allow_and_is_recorded(
    serve: Callable[[str], Server], entra: Idp
) -> None:
    server = serve(ENTRA_FILE)
    token = entra.sign(entra_claims())

    allowed = server.call(token, "equity-book").json()
    denied = server.call(token, "macro-book").json()

    assert allowed["status"] == "ok"
    assert denied["status"] == "error"  # a dataset the group does not name: as if missing
    assert server.listed(token).status_code == 200
    principals = {run.principal for run in server.runs.list_runs(10)}
    assert principals == {f"dev.shah@kestrel.example@{TID}"}  # who, and the provider's tenant


def test_an_entra_machine_token_is_entitled_by_its_app_role(
    serve: Callable[[str], Server], entra: Idp
) -> None:
    server = serve(ENTRA_FILE)
    # Client credentials: roles, no scp, no groups, and the app's own object id as subject.
    claims = entra_claims(roles=["Tools.Call", "research-eq"], preferred_username=None)
    for name in ("scp", "groups"):
        del claims[name]
    claims["sub"] = "service-principal-oid"
    token = entra.sign(claims)

    assert server.call(token, "equity-book").json()["status"] == "ok"  # entitled by the role
    assert server.call(token, "macro-book").json()["status"] == "error"
    assert {run.principal for run in server.runs.list_runs(10)} == {f"service-principal-oid@{TID}"}


def test_a_cognito_access_token_is_found_by_discovery_and_its_client_id(
    serve: Callable[[str], Server], cognito: Idp
) -> None:
    server = serve(COGNITO_FILE)
    token = cognito.sign(cognito_claims())

    assert server.call(token, "macro-book").json()["status"] == "ok"
    assert server.call(token, "equity-book").json()["status"] == "error"
    assert cognito.fetches == 1  # the keys, found through the issuer's discovery document


def test_a_cognito_id_token_is_refused_where_an_access_token_is_needed(
    serve: Callable[[str], Server], cognito: Idp
) -> None:
    server = serve(COGNITO_FILE)
    id_token = cognito.sign(cognito_claims(token_use="id", aud=CLIENT))
    other_client = cognito.sign(cognito_claims(client_id="someone-else"))

    assert server.listed(id_token).status_code == 401
    assert server.listed(other_client).status_code == 401
    assert server.listed(cognito.sign(cognito_claims())).status_code == 200


# What must be refused


def _alg_none(idp: Idp) -> str:
    return raw({"alg": "none", "kid": "k1", "typ": "JWT"}, entra_claims())


def _confused(idp: Idp) -> str:
    """HS256 over the header and payload, keyed by the public key's PEM bytes."""
    signing = raw({"alg": "HS256", "kid": "k1", "typ": "JWT"}, entra_claims())
    mac = hmac.new(idp.public_pem(), signing.rsplit(".", 1)[0].encode(), hashlib.sha256)
    return signing.rsplit(".", 1)[0] + "." + b64(mac.digest())


def _tampered(idp: Idp) -> str:
    head, _, signature = idp.sign(entra_claims()).split(".")
    forged = b64(json.dumps(entra_claims(groups=[MACRO])).encode())
    return f"{head}.{forged}.{signature}"


def _other_key(idp: Idp) -> str:
    stranger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return idp.sign(entra_claims(), key=stranger)


@pytest.mark.parametrize(
    "make",
    [
        lambda i: i.sign(entra_claims(iss="https://login.microsoftonline.com/other/v2.0")),
        lambda i: i.sign(entra_claims(iss=ENTRA + "/")),
        lambda i: i.sign(entra_claims(aud="api://another-api")),
        lambda i: i.sign(entra_claims(aud=[])),
        lambda i: i.sign(entra_claims(aud=None)),
        lambda i: i.sign(entra_claims(exp=int(time.time()) - 120)),
        lambda i: i.sign(entra_claims(nbf=int(time.time()) + 3600)),
        lambda i: i.sign(entra_claims(iat=int(time.time()) + 3600)),
        lambda i: jwt.encode(
            {k: v for k, v in entra_claims().items() if k != "exp"}, "x" * 40, "HS256"
        ),
        lambda i: i.sign({k: v for k, v in entra_claims().items() if k != "exp"}),
        _alg_none,
        _confused,
        _tampered,
        _other_key,
        lambda i: i.sign(entra_claims(), kid="k9"),
        lambda i: i.sign(entra_claims(), kid=None),
        lambda i: i.sign(entra_claims(tid=None)),
        lambda i: i.sign(entra_claims(groups="research-eq")),
        lambda i: i.sign(entra_claims(scp=["tools.call", 7])),
        lambda i: "not-a-jwt",
        lambda i: i.sign(entra_claims())[:-4] + "AAAA",
    ],
    ids=[
        "issuer",
        "issuer-slash",
        "audience",
        "audience-empty",
        "audience-missing",
        "expired",
        "not-before",
        "issued-in-future",
        "no-exp-hs",
        "no-exp",
        "alg-none",
        "alg-confusion",
        "tampered-payload",
        "other-key",
        "unknown-kid",
        "no-kid",
        "no-provider-tenant",
        "groups-shape",
        "scope-shape",
        "opaque",
        "bad-signature",
    ],
)
def test_uncertain_tokens_are_refused(
    serve: Callable[[str], Server], entra: Idp, make: Callable[[Idp], str]
) -> None:
    server = serve(ENTRA_FILE)

    assert server.listed(make(entra)).status_code == 401
    assert server.listed(entra.sign(entra_claims())).status_code == 200  # the file itself is fine


def test_a_token_with_the_crit_header_is_refused(
    serve: Callable[[str], Server], entra: Idp
) -> None:
    server = serve(ENTRA_FILE)
    key = entra.keys["k1"].private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    token = jwt.encode(
        entra_claims(), key, "RS256", headers={"kid": "k1", "crit": ["b64"], "b64": True}
    )

    assert server.listed(token).status_code == 401


def test_the_other_algorithm_family_is_refused_unless_the_file_allows_it(
    serve: Callable[[str], Server], entra: Idp
) -> None:
    ps256 = entra.sign(entra_claims(), alg="PS256")

    assert serve(ENTRA_FILE).listed(ps256).status_code == 401
    allowed = ENTRA_FILE.replace(
        'verify = "jwks"', 'verify = "jwks"\nalgorithms = ["RS256", "PS256"]'
    )
    assert serve(allowed).listed(ps256).status_code == 200


def test_too_many_groups_are_read_as_none_so_access_fails_closed(
    serve: Callable[[str], Server], entra: Idp, caplog: pytest.LogCaptureFixture
) -> None:
    server = serve(ENTRA_FILE)
    claims = entra_claims(_claim_names={"groups": "src1"})
    del claims["groups"]
    token = entra.sign(claims)

    with caplog.at_level(logging.WARNING):
        result = server.call(token, "equity-book").json()

    assert result["status"] == "denied"  # signed in, but with no group, reaches nothing
    assert "too many groups" in caplog.text
    assert token not in caplog.text


def test_no_log_line_holds_a_token(
    serve: Callable[[str], Server], entra: Idp, caplog: pytest.LogCaptureFixture
) -> None:
    server = serve(ENTRA_FILE)
    tokens = [
        entra.sign(entra_claims(aud="api://another-api")),
        entra.sign(entra_claims(), kid="k9"),
        entra.sign(entra_claims(exp=int(time.time()) - 600)),
    ]

    with caplog.at_level(logging.DEBUG):
        for token in tokens:
            assert server.listed(token).status_code == 401

    assert not any(part in caplog.text for token in tokens for part in token.split("."))


def test_a_machine_without_a_scope_the_file_maps_cannot_call(
    serve: Callable[[str], Server], entra: Idp
) -> None:
    server = serve(ENTRA_FILE)
    claims = entra_claims(scp="tools.list", roles=["research-eq"])

    assert server.listed(entra.sign(claims)).status_code == 200
    assert server.call(entra.sign(claims)).status_code in (401, 403)  # lacks tools:call


def test_a_scope_cannot_grant_approving(serve: Callable[[str], Server], entra: Idp) -> None:
    server = serve(ENTRA_FILE)
    token = entra.sign(entra_claims(scp="tools.list tools.call runs:approve"))

    assert server.call(token).json()["status"] == "ok"
    approve = server.client.post(
        "/v1/runs/none/approve", headers={"Authorization": f"Bearer {token}"}
    )
    assert approve.status_code in (401, 403, 404)


# Keys: fetching, rotation, failure


def _verifier(idp: Idp, clock: Callable[[], float], **settings: Any) -> JwksVerifier:
    server = JwksServer(
        issuer=ENTRA,
        jwks_uri=ENTRA_KEYS,
        audiences=["api://kestrel-qaos"],
        tenant_claim="",
        **settings,
    )
    return JwksVerifier(
        [server],
        resource=RESOURCE,
        tenants_by_issuer={ENTRA: {"local"}},
        http=httpx2.AsyncClient(transport=httpx2.MockTransport(idp.handler)),
        clock=clock,
    )


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_an_unknown_kid_fetches_keys_again_but_at_most_once_in_an_interval(entra: Idp) -> None:
    clock = Clock()
    verifier = _verifier(entra, clock)

    async def run() -> list[bool]:
        outcome = []
        outcome.append(await verifier.verify_token(entra.sign(entra_claims())) is not None)
        entra.add_key("k2")  # the provider rotates
        new = entra.sign(entra_claims(), kid="k2")
        outcome.append(await verifier.verify_token(new) is not None)  # too soon to look again
        clock.now += 31
        outcome.append(await verifier.verify_token(new) is not None)
        for _ in range(5):  # a flood of unknown ids
            await verifier.verify_token(entra.sign(entra_claims(), kid="unknown"))
        return outcome

    assert anyio.run(run) == [True, False, True]
    assert entra.fetches == 2


def test_a_failed_fetch_refuses_and_is_tried_again_only_after_the_interval(entra: Idp) -> None:
    clock = Clock()
    verifier = _verifier(entra, clock)
    entra.failing = True

    async def run() -> list[bool]:
        token = entra.sign(entra_claims())
        outcome = [await verifier.verify_token(token) is not None]
        entra.failing = False
        outcome.append(await verifier.verify_token(token) is not None)  # not asked again yet
        clock.now += 31
        outcome.append(await verifier.verify_token(token) is not None)
        return outcome

    assert anyio.run(run) == [False, False, True]


def test_keys_are_kept_for_their_time_and_a_stale_set_with_no_answer_refuses(entra: Idp) -> None:
    clock = Clock()
    verifier = _verifier(entra, clock)

    async def run() -> list[bool]:
        token = entra.sign(entra_claims())
        outcome = [await verifier.verify_token(token) is not None]
        clock.now += 60  # inside the time to live: no new fetch
        outcome.append(await verifier.verify_token(token) is not None)
        clock.now += 4000
        entra.failing = True  # the keys are due, and the provider does not answer
        outcome.append(await verifier.verify_token(token) is not None)
        return outcome

    assert anyio.run(run) == [True, True, False]
    assert entra.fetches == 1


def test_discovery_must_name_the_configured_issuer(cognito: Idp) -> None:
    cognito.issuer = "https://cognito-idp.eu-west-1.amazonaws.com/eu-west-1_Other"
    server = JwksServer(issuer=POOL, client_ids=[CLIENT], tenant_claim="")
    verifier = JwksVerifier(
        [server],
        resource=RESOURCE,
        tenants_by_issuer={POOL: {"local"}},
        http=httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(
                    200,
                    json={"issuer": "https://elsewhere.example", "jwks_uri": f"{POOL}/keys"},
                )
            )
        ),
    )

    assert anyio.run(verifier.verify_token, cognito.sign(cognito_claims())) is None


def test_a_symmetric_key_in_the_published_set_is_never_used(entra: Idp) -> None:
    secret = b"shared-secret-shared-secret-1234"
    forged = jwt.encode(entra_claims(), secret, "HS256", headers={"kid": "h1"})
    published = {"keys": [{"kty": "oct", "kid": "h1", "k": b64(secret)}, *entra.jwks()["keys"]]}
    verifier = _verifier(entra, Clock(), algorithms=["RS256", "ES256"])
    verifier.http = httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json=published))
    )

    assert anyio.run(verifier.verify_token, forged) is None
    assert anyio.run(verifier.verify_token, entra.sign(entra_claims())) is not None


# The sign-in file


def test_a_file_with_introspecting_and_signature_checked_providers_asks_each(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KESTREL_INTROSPECTION_SECRET", "s3cret")
    path = tmp_path / "sign-in.toml"
    path.write_text(
        ENTRA_FILE
        + """
[[authorization_servers]]
issuer = "https://login.kestrel.example"
introspection_url = "https://login.kestrel.example/oauth2/introspect"
client_id = "qaos"
client_secret_env = "KESTREL_INTROSPECTION_SECRET"
""",
        "utf-8",
    )

    sign_in = load_sign_in(path)

    assert sign_in.issuers == ["https://login.kestrel.example", ENTRA]
    assert isinstance(sign_in.verifier(), FirstOf)


def test_a_file_that_introspects_still_gets_the_introspection_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("S", "s3cret")
    path = tmp_path / "sign-in.toml"
    path.write_text(
        f'resource = "{RESOURCE}"\n[[authorization_servers]]\nissuer = "https://idp.example"\n'
        'introspection_url = "https://idp.example/i"\nclient_id = "c"\nclient_secret_env = "S"\n',
        "utf-8",
    )

    assert isinstance(load_sign_in(path).verifier(), IntrospectionVerifier)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda s: s.replace('audiences = ["api://kestrel-qaos"]', ""), "needs audiences"),
        (
            lambda s: s.replace('verify = "jwks"', 'verify = "jwks"\nclient_secret_env = "S"'),
            "apply only with verify",
        ),
        (
            lambda s: s.replace(
                "jwks_uri =", 'introspection_url = "https://x.example/i"\nignored ='
            ),
            "Extra inputs",
        ),
        (lambda s: s.replace('"tools:list"', '"runs:approve"'), "not a scope a token may grant"),
        (
            lambda s: s.replace('"tools:list"', '"tools:everything"'),
            "not a scope a token may grant",
        ),
        (lambda s: s.replace(f"{ENTRA_KEYS}", "http://keys.example/jwks"), "https"),
        (
            lambda s: s.replace('verify = "jwks"', 'verify = "jwks"\nalgorithms = ["HS256"]'),
            "algorithms",
        ),
        (
            lambda s: s.replace('verify = "jwks"', 'verify = "jwks"\nalgorithms = ["none"]'),
            "algorithms",
        ),
        (
            lambda s: s.replace('verify = "jwks"', 'verify = "jwks"\nleeway_seconds = 3600'),
            "leeway",
        ),
        (
            lambda s: s.replace('verify = "jwks"', 'verify = "jwks"\ngroups_claim = "g"'),
            "groups_claims",
        ),
        (lambda s: s.replace('verify = "jwks"', 'verify = "other"'), "verify"),
        (lambda s: s.replace('"scp", "roles"', '"bad claim"'), "claim name"),
    ],
    ids=[
        "no-audience",
        "secret-with-jwks",
        "typo",
        "approve",
        "unknown-scope",
        "http-keys",
        "hs256",
        "none",
        "leeway",
        "both-groups",
        "mode",
        "claim-name",
    ],
)
def test_a_bad_jwks_sign_in_file_refuses_to_start(
    tmp_path: Path, change: Callable[[str], str], message: str
) -> None:
    path = tmp_path / "sign-in.toml"
    path.write_text(change(ENTRA_FILE), "utf-8")

    with pytest.raises(SignInError, match=message):
        load_sign_in(path)


def test_introspection_settings_refuse_the_keys_only_settings(tmp_path: Path) -> None:
    path = tmp_path / "sign-in.toml"
    path.write_text(
        f'resource = "{RESOURCE}"\n[[authorization_servers]]\nissuer = "https://idp.example"\n'
        'introspection_url = "https://idp.example/i"\nclient_id = "c"\nclient_secret_env = "S"\n'
        'audiences = ["x"]\n',
        "utf-8",
    )

    with pytest.raises(SignInError, match="apply only with verify"):
        load_sign_in(path)


def test_the_example_files_load_and_need_no_secret(tmp_path: Path) -> None:
    for name, text in (("entra", ENTRA_FILE), ("cognito", COGNITO_FILE)):
        path = tmp_path / f"{name}.toml"
        path.write_text(text, "utf-8")
        sign_in = load_sign_in(path, {})
        assert sign_in.servers == []
        assert len(sign_in.jwks_servers) == 1
