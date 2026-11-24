# ruff: noqa: E501
"""Wrenfield Credit Opportunities (mock fund): its AWS estate. A Cognito-shaped user pool and the
platform's sign-in, the lake read by DuckDB, the RDS path, and the deployment descriptors.
Every service is a local stand-in; nothing here reaches the network beyond loopback."""

import asyncio
import base64
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import anyio
import pytest
from pydantic import SecretStr

WRENFIELD = Path(__file__).parents[2] / "examples" / "mock-funds" / "wrenfield"
for _sub in ("facility/warehouse", "site", "facility/gateway"):
    if str(WRENFIELD / _sub) not in sys.path:
        sys.path.insert(0, str(WRENFIELD / _sub))

import build_wrenfield as bw  # noqa: E402
import wrenfield_cognito_idp as cognito  # noqa: E402
import wrenfield_server as ws  # noqa: E402
from wrenfield_store import WrenfieldDatasetStore, _named  # noqa: E402

from quant_agent_os.mcp.http import GROUPS_CLAIM  # noqa: E402
from quant_agent_os.runs import MemoryRunStore  # noqa: E402
from quant_agent_os.tools import FileAccessPolicy, PermissionPolicy  # noqa: E402
from quant_platform import build_server_app  # noqa: E402
from quant_platform.identity.oauth import AuthorizationServer, IntrospectionVerifier  # noqa: E402
from quant_platform.identity.sign_in import load_sign_in, load_sign_in_file  # noqa: E402

pytest.importorskip("mcp")
pytest.importorskip("pyarrow")
pytest.importorskip("duckdb")

RESOURCE = "http://127.0.0.1:8000/mcp"
SECRET = "wf-introspection-fixture"


class Clock:
    def __init__(self) -> None:
        self.offset = 0.0

    def __call__(self) -> float:
        import time

        return time.time() + self.offset


@pytest.fixture(scope="module")
def estate(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("cloud-estate")
    bw.build(root)
    return root


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def pool(clock: Clock) -> Iterator[cognito.RunningPool]:
    with cognito.start(clock=clock) as running:
        yield running


def _post(url: str, form: dict[str, str], auth: str | None = None) -> tuple[int, dict[str, Any]]:
    from urllib.parse import urlencode

    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if auth:
        headers["Authorization"] = auth
    request = urllib.request.Request(
        url, data=urlencode(form).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        with error:
            return error.code, json.loads(error.read())


def _tokens(pool: cognito.RunningPool, user: str) -> dict[str, Any]:
    status, body = _post(
        f"{pool.base_url}/oauth2/token",
        {
            "grant_type": "password",
            "client_id": "wf-cli",
            "username": user,
            "password": f"wf-{user}",
        },
    )
    assert status == 200, body
    return body


def _claims(token: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(cognito.unb64(token.split(".")[1]))
    return loaded


def _verifier(pool: cognito.RunningPool, url: str) -> IntrospectionVerifier:
    return IntrospectionVerifier(
        [
            AuthorizationServer(
                issuer=pool.pool.issuer,
                introspection_url=url,
                client_id="qaos-server",
                client_secret=SecretStr(SECRET),
                tenant_claim="qaos_tenant",
                tenant_claim_value="wrenfield",
                groups_claim="cognito:groups",
            )
        ],
        resource=RESOURCE,
        tenants_by_issuer={pool.pool.issuer: {"wrenfield"}},
    )


# ---- the user pool ----------------------------------------------------------------------------


def test_cognito_tokens_have_cognitos_shape(pool: cognito.RunningPool) -> None:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    body = _tokens(pool, "pm-hy")
    access, ident = _claims(body["access_token"]), _claims(body["id_token"])
    assert access["iss"] == "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_WrnFld001"
    assert access["token_use"] == "access" and access["client_id"] == "wf-cli"
    assert access["cognito:groups"] == ["pm-hy-ls"]
    assert access["scope"] == "qaos/tools.list qaos/tools.call"  # section 10: both scopes
    assert "aud" not in access  # the fact the platform's audience check trips on
    assert (
        ident["token_use"] == "id"
        and ident["aud"] == "wf-cli"
        and ident["cognito:username"] == "pm-hy"
    )
    assert access["sub"] != "pm-hy"  # a UUID in Cognito, not the name
    jwks = json.loads(
        urllib.request.urlopen(f"{pool.base_url}/.well-known/jwks.json", timeout=5).read()
    )
    jwk = jwks["keys"][0]
    assert jwk["kid"] == json.loads(cognito.unb64(body["access_token"].split(".")[0]))["kid"]
    public = rsa.RSAPublicNumbers(
        int.from_bytes(cognito.unb64(jwk["e"]), "big"),
        int.from_bytes(cognito.unb64(jwk["n"]), "big"),
    ).public_key()
    head, payload, signature = body["access_token"].split(".")
    public.verify(
        cognito.unb64(signature), f"{head}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256()
    )
    discovery = json.loads(
        urllib.request.urlopen(
            f"{pool.base_url}/.well-known/openid-configuration", timeout=5
        ).read()
    )
    assert "introspection_endpoint" not in discovery and "jwks_uri" in discovery
    assert _post(f"{pool.base_url}/oauth2/introspect", {"token": body["access_token"]})[0] == 404


def test_the_platform_cannot_sign_in_against_cognito_directly(pool: cognito.RunningPool) -> None:
    """Measured: the verifier has to POST to an introspection URL, and Cognito has none."""
    token = _tokens(pool, "pm-hy")["access_token"]
    verifier = _verifier(pool, f"{pool.base_url}/oauth2/introspect")
    assert anyio.run(verifier.verify_token, token) is None


def test_a_token_with_no_aud_would_fail_the_audience_check_even_with_introspection(
    pool: cognito.RunningPool,
) -> None:
    """If the provider echoed the access token's claims as they are, `aud` is absent and the
    platform refuses it (RFC 8707 audience); the shim has to state it."""
    claims = _claims(_tokens(pool, "pm-hy")["access_token"])
    assert (
        "aud" not in claims
        and "qaos_tenant" not in claims
        and claims["scope"] == "qaos/tools.list qaos/tools.call"
    )


# ---- sign-in by signature (decision 0031): the fund's introspection shim is retired -------------


def _jwks_sign_in(tmp_path: Path, pool: cognito.RunningPool) -> Path:
    """The deployed sign-in file, with its keys at the loopback address the mock serves them on.
    Deployed, the keys are found through the issuer's openid-configuration instead."""
    text = (WRENFIELD / "site" / "wrenfield_sign_in.toml").read_text()
    path = tmp_path / "sign-in.toml"
    jwks = f"{pool.base_url}/.well-known/jwks.json"
    path.write_text(text.replace('verify = "jwks"', f'verify = "jwks"\njwks_uri = "{jwks}"'))
    return path


def _verify(verifier: Any, token: str) -> Any:
    return asyncio.run(verifier.verify_token(token))


def test_a_cognito_token_signs_in_by_its_signature_with_its_groups(
    pool: cognito.RunningPool, tmp_path: Path
) -> None:
    verifier = load_sign_in(_jwks_sign_in(tmp_path, pool)).verifier()
    access = _verify(verifier, _tokens(pool, "pm-hy")["access_token"])
    assert access is not None and access.claims is not None
    assert access.claims[GROUPS_CLAIM] == ["pm-hy-ls"]
    assert access.subject == "pm-hy"  # the username, not the sub UUID (principal_claim)
    batch = _post(
        f"{pool.base_url}/oauth2/token", {"grant_type": "client_credentials"},
        "Basic " + base64.b64encode(b"wf-batch:wf-batch-fixture").decode(),
    )  # fmt: skip
    machine = _verify(verifier, batch[1]["access_token"])
    assert machine is not None and machine.claims is not None
    assert machine.claims[GROUPS_CLAIM] == ["operators"] and machine.subject == "wf-batch"
    nobody = _verify(verifier, _tokens(pool, "no-groups")["access_token"])
    assert nobody is not None and nobody.claims is not None and nobody.claims[GROUPS_CLAIM] == []


def test_the_verifier_refuses_what_it_should(
    pool: cognito.RunningPool, tmp_path: Path, clock: Clock
) -> None:
    verifier = load_sign_in(_jwks_sign_in(tmp_path, pool)).verifier()
    body = _tokens(pool, "pm-hy")
    assert _verify(verifier, body["id_token"]) is None  # an ID token is no credential
    head, _payload, signature = body["access_token"].split(".")
    forged_payload = cognito.b64(
        json.dumps({**_claims(body["access_token"]), "cognito:groups": ["risk"]}).encode()
    )
    assert _verify(verifier, f"{head}.{forged_payload}.{signature}") is None
    assert _verify(verifier, "not-a-token") is None
    other = cognito.start()
    try:
        assert _verify(verifier, _tokens(other, "pm-hy")["access_token"]) is None  # another pool
    finally:
        other.stop()
    pool.pool.clock = lambda: 1_000.0  # issued in 1970 with the same key: expired now
    try:
        expired = _tokens(pool, "pm-hy")["access_token"]
    finally:
        pool.pool.clock = clock
    assert _verify(verifier, expired) is None


def test_a_pm_token_carries_both_scopes_the_listing_and_the_call_need(
    pool: cognito.RunningPool, tmp_path: Path
) -> None:
    verifier = load_sign_in(_jwks_sign_in(tmp_path, pool)).verifier()
    access = _verify(verifier, _tokens(pool, "pm-hy")["access_token"])
    assert access is not None and set(access.scopes) == {"tools:list", "tools:call"}


def test_the_sign_in_file_names_cognitos_claims(tmp_path: Path) -> None:
    sign_in = load_sign_in(WRENFIELD / "site" / "wrenfield_sign_in.toml")  # needs no secret
    (server,) = sign_in.jwks_servers  # verify = "jwks": no introspection server, no secret
    assert server.issuer == "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_WrnFld001"
    assert server.groups_claims == ["cognito:groups"] and server.principal_claim == "username"
    assert sign_in.tenants() == {"local"}  # the pool sends no tenant claim: one tenant
    assert (
        load_sign_in_file(WRENFIELD / "site" / "wrenfield_sign_in.toml").missing_secrets({}) == []
    )


def test_a_signed_in_pm_calls_a_tool_over_rest_and_is_held_to_their_book(
    pool: cognito.RunningPool,
    estate: Path,
    tmp_path: Path,
) -> None:
    from starlette.testclient import TestClient

    from quant_agent_os.api import add_key

    sign_in = _jwks_sign_in(tmp_path, pool)
    keys = tmp_path / "keys.toml"
    add_key(keys, "ops", ["tools:list"])
    store = WrenfieldDatasetStore(estate)
    args = {
        "mv": {"dataset": "mv_hy_ls"}, "sprdur": {"dataset": "bond_sprdur"}, "oas": {"dataset": "bond_oas"},
        "static": {"dataset": "bond_static"}, "rating": {"dataset": "bond_rating"},
        "capital": {"dataset": "nav", "column": "hy_ls"}, "group_by": "none",
    }  # fmt: skip
    runs = MemoryRunStore()
    with (
        build_server_app(
            [ws.build_app()], datasets=store, policy=PermissionPolicy(), keys=keys, runs=runs,
            access=FileAccessPolicy(WRENFIELD / "facility" / "access.toml"), sign_in=sign_in,
        ) as app,
        TestClient(app, base_url="http://127.0.0.1:8000") as client,
    ):  # fmt: skip

        def call(user: str, arguments: dict[str, Any]) -> dict[str, Any]:
            token = _tokens(pool, user)["access_token"]
            reply: dict[str, Any] = client.post(
                "/v1/tools/wrenfield_spread_duration_dts/call", json={"arguments": arguments},
                headers={"Authorization": f"Bearer {token}"},
            ).json()  # fmt: skip
            return reply

        mine = call("pm-hy", args)
        other = call("pm-hy", {**args, "mv": {"dataset": "mv_ig_rv"}})
        risk = call(
            "risk-head",
            {
                **args,
                "mv": {"dataset": "mv_ig_rv"},
                "capital": {"dataset": "nav", "column": "ig_rv"},
            },
        )
    assert mine["status"] == "ok" and mine["output"]["total_dts_bp_years"] == pytest.approx(
        1103.350619, abs=1e-4
    )
    assert other["status"] == "error" and "No dataset 'mv_ig_rv'" in other["error"]
    assert risk["status"] == "ok"
    recorded = runs.get_run(mine["run_id"])
    assert recorded is not None and recorded.run.principal == "pm-hy"  # a name, not a sub


def test_analysts_query_the_lake_in_place_with_duckdb(estate: Path) -> None:
    import duckdb

    sql = (WRENFIELD / "site" / "sql" / "wrenfield_duckdb_analyst.sql").read_text()
    lake = estate / "s3" / bw.LAKE
    rows = duckdb.connect().execute(sql.replace("{lake}", str(lake))).fetchall()
    assert sum(row[1] for row in rows) == 147  # the three defaulted bonds have no spread
    partitions = (
        duckdb.connect()
        .execute(
            f"SELECT count(DISTINCT dt) FROM read_parquet('{lake}/curated/cds_daily/dt=*/part-0000.parquet', "
            "hive_partitioning = true)"
        )
        .fetchone()
    )
    assert partitions == (103,)


def test_the_postgres_parameter_style_and_ddl_are_the_same_shape() -> None:
    sql, values = _named("SELECT 1 FROM pos_eod WHERE book = :book", {"book": "hy_ls"}, True)
    assert sql.endswith("%(book)s") and values == {"book": "hy_ls"}
    assert _named("SELECT :book", {"book": "x"}, False)[0] == "SELECT :book"
    ddl = (WRENFIELD / "site" / "sql" / "wrenfield_oms_postgres.sql").read_text()
    with closing(sqlite3.connect(":memory:")) as db:
        tables = {row[0] for row in db.execute("SELECT 1 WHERE 0")}  # an empty database is fine
        assert tables == set()
    for table in (
        "pos_eod",
        "pos_cds",
        "pos_index",
        "trd_trades",
        "fin_repo_rates",
        "pnl_daily",
        "ref_books",
    ):
        assert f"CREATE TABLE {table}" in ddl
    assert "GRANT rds_iam TO qaos_reader" in ddl and "default_transaction_read_only" in ddl


@pytest.mark.skipif(
    not os.environ.get("WRENFIELD_TEST_POSTGRES_URL"),
    reason="WRENFIELD_TEST_POSTGRES_URL is not set",
)
def test_the_store_over_postgres_equals_the_store_over_sqlite(estate: Path) -> None:
    import psycopg

    url = os.environ["WRENFIELD_TEST_POSTGRES_URL"]
    ddl = (WRENFIELD / "site" / "sql" / "wrenfield_oms_postgres.sql").read_text()
    ddl = ddl.split("-- Roles:")[0]
    with (
        psycopg.connect(url, autocommit=True) as pg,
        closing(sqlite3.connect(estate / "rds" / "wrenfield_oms.sqlite")) as lite,
    ):
        pg.execute("DROP SCHEMA IF EXISTS oms CASCADE")
        pg.execute(ddl)
        for table in (
            "ref_books",
            "ref_issuer_master",
            "pos_eod",
            "pos_cds",
            "pos_index",
            "trd_trades",
            "fin_repo_rates",
            "fin_borrow_fees",
            "pnl_daily",
            "cmp_restricted",
        ):
            rows = lite.execute(f"SELECT * FROM {table}").fetchall()
            if rows:
                marks = ",".join(["%s"] * len(rows[0]))
                with pg.cursor() as cursor:
                    cursor.executemany(f"INSERT INTO oms.{table} VALUES ({marks})", rows)
    over_sqlite = WrenfieldDatasetStore(estate)
    over_postgres = WrenfieldDatasetStore(estate, oms_url=url, environ={})
    for dataset_id in (
        "mv_hy_ls",
        "w_ig_rv",
        "ret_net_distressed",
        "nav",
        "borrow_fee",
        "cds_protection",
    ):
        a, b = over_sqlite.get(dataset_id), over_postgres.get(dataset_id)
        assert a.index == b.index and list(a.columns) == list(b.columns)
        for name in a.columns:
            assert b.columns[name] == pytest.approx(a.columns[name], abs=1e-4)


# ---- deployment descriptors (never run here) -------------------------------------------------


def test_the_deployment_descriptors_are_consistent_with_the_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deploy = WRENFIELD / "site" / "deploy"
    task = json.loads((deploy / "ecs-task-definition.json").read_text())
    qaos = next(c for c in task["containerDefinitions"] if c["name"] == "qaos")
    assert task["requiresCompatibilities"] == ["FARGATE"] and qaos["readonlyRootFilesystem"] is True
    assert all(s["valueFrom"].startswith("arn:aws:secretsmanager:") for s in qaos["secrets"])
    # The platform's secret is a file an init container writes (section 11). The fund's own
    # warehouse password stays an environment variable, as the section says.
    init = next(c for c in task["containerDefinitions"] if c["name"] == "secrets-to-files")
    assert [c["name"] for c in task["containerDefinitions"]] == ["qaos", "secrets-to-files"]
    assert "pg-reader" in init["entryPoint"][-1] and "introspection" not in json.dumps(init)
    assert [s["name"] for s in init["secrets"]] == ["PGPASSWORD"]
    assert [s["name"] for s in qaos["secrets"]] == ["WRENFIELD_OMS_PASSWORD"]
    env = {item["name"]: item.get("value") for item in qaos["environment"]}
    assert env["PGPASSWORD_FILE"] == "/run/secrets/pg-reader"
    assert "WRENFIELD_INTROSPECTION_SECRET_FILE" not in env
    assert env["QUANT_AGENT_OS_LOG_FORMAT"] == "json"
    sign_in = (WRENFIELD / "site" / "wrenfield_sign_in.toml").read_text()
    assert 'verify = "jwks"' in sign_in and "introspection_url" not in sign_in
    assert qaos["logConfiguration"]["logDriver"] == "awslogs"
    assert "/healthz" in " ".join(qaos["healthCheck"]["command"])
    from quant_agent_os.config import get_settings

    for item in qaos["environment"]:
        if item["name"].startswith("QUANT_AGENT_OS_"):
            monkeypatch.setenv(item["name"], item["value"])
    monkeypatch.setenv("QUANT_AGENT_OS_HOME", "/tmp/wrenfield-home")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert (
            settings.store.startswith("postgresql://")
            and settings.runs_file.as_posix() == "/data/runs/runs.db"
        )
    finally:
        get_settings.cache_clear()
    assert (
        'ENTRYPOINT ["python", "/home/app/wrenfield_server.py"]'
        in (deploy / "Dockerfile").read_text()
    )
    assert "EnableIAMDatabaseAuthentication: true" in (deploy / "infrastructure.yaml").read_text()
