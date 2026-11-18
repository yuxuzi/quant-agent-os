"""Larchmere signs in through Microsoft Entra ID: what the platform's sign-in does with them."""

import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import jwt
import pytest

ROOT = Path(__file__).resolve().parents[2]
FUND = ROOT / "examples" / "mock-funds" / "larchmere"
sys.path.insert(0, str(FUND / "facility" / "gateway"))

import entra_mock  # noqa: E402

from quant_agent_os.mcp.http import GROUPS_CLAIM, TENANT_CLAIM  # noqa: E402
from quant_agent_os.tools import Caller, FileAccessPolicy  # noqa: E402
from quant_platform.identity.sign_in import (  # noqa: E402
    SignInError,
    SignInFile,
    load_sign_in,
    load_sign_in_file,
)

RESOURCE = "https://larchmere-qaos.example.azurecontainerapps.io/mcp"
SECRET = "shim-fixture-secret"
UPN = "tomas.okafor@larchmere.example"
GROUP_PM_B = "4b1f0c52-6a4e-4d36-9a21-0a1b2c3d0002"


class Clock:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def tenant(clock: Clock) -> entra_mock.EntraTenant:
    return entra_mock.EntraTenant(clock=clock)


@pytest.fixture
def base(tenant: entra_mock.EntraTenant) -> Iterator[str]:
    with entra_mock.running(tenant, RESOURCE, SECRET) as url:
        yield url


def sign_in_file(tmp_path: Path, tenant: entra_mock.EntraTenant, url: str, path: str) -> Path:
    """The fund's sign-in file, its introspection URL pointed at this test's loopback server."""
    text = (FUND / "site" / "deploy" / "sign-in.toml").read_text()
    text = text.replace(
        "https://larchmere-token-shim.internal.example.azurecontainerapps.io/shim/introspect",
        f"{url}{path}",
    )
    target = tmp_path / "sign-in.toml"
    target.write_text(text)
    return target


def verify(token: str, tmp_path: Path, tenant: entra_mock.EntraTenant, url: str, path: str) -> Any:
    sign_in = load_sign_in(
        sign_in_file(tmp_path, tenant, url, path), {"LARCHMERE_INTROSPECTION_SECRET": SECRET}
    )
    return anyio.run(sign_in.verifier().verify_token, token)


def test_the_tokens_have_entras_v2_shape_and_verify_against_the_jwks(
    tenant: entra_mock.EntraTenant,
) -> None:
    token = tenant.issue(UPN)
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["iss"] == f"https://login.microsoftonline.com/{tenant.tid}/v2.0"
    assert claims["ver"] == "2.0" and claims["aud"] == "api://larchmere-qaos"
    assert claims["tid"] == tenant.tid and claims["scp"] == "tools.list tools.call"
    assert claims["roles"] == ["CLO.PM"] and claims["groups"] == [GROUP_PM_B]
    assert jwt.get_unverified_header(token) == {
        "alg": "RS256",
        "typ": "JWT",
        "kid": "larchmere-key-1",
    }
    assert tenant.verify(token)["oid"] == claims["oid"]
    with pytest.raises(jwt.PyJWTError):
        tenant.verify(token[:-4] + "AAAA")


def test_entra_has_no_introspection_endpoint_so_the_platform_refuses_its_tokens(
    tmp_path: Path, tenant: entra_mock.EntraTenant, base: str
) -> None:
    # The sign-in file needs an introspection URL; pointed at Entra's own path it is a 404.
    path = f"/{tenant.tid}/oauth2/v2.0/introspect"
    assert verify(tenant.issue(UPN), tmp_path, tenant, base, path) is None


def test_entras_claims_as_they_are_do_not_satisfy_the_platform(
    tmp_path: Path, tenant: entra_mock.EntraTenant, base: str, caplog: pytest.LogCaptureFixture
) -> None:
    # A proxy that only says active and passes the claims on: aud is the app ID URI, scp not scope,
    # and there is no tenant claim of the platform's name.
    with caplog.at_level("INFO"):
        access = verify(tenant.issue(UPN), tmp_path, tenant, base, "/naive/introspect")
    assert access is None
    assert any("for another audience" in record.message for record in caplog.records)


def test_through_the_shim_the_platform_accepts_a_token_and_reads_guid_groups(
    tmp_path: Path, tenant: entra_mock.EntraTenant, base: str
) -> None:
    access = verify(tenant.issue(UPN), tmp_path, tenant, base, "/shim/introspect")
    assert access is not None
    assert access.scopes == [
        "tools:list",
        "tools:call",
    ]  # the shim turned tools.call into tools:call
    assert access.claims[GROUPS_CLAIM] == [GROUP_PM_B]
    assert access.claims[TENANT_CLAIM] == "local"  # the issuer's pinned tenant, not Entra's tid
    assert access.subject == "a1000000-0000-4000-8000-000000000002"  # the oid, not the UPN
    caller = Caller("tomas.okafor", groups=frozenset(access.claims[GROUPS_CLAIM]))
    policy = FileAccessPolicy(FUND / "facility" / "access.toml")
    assert policy.allows(caller, "dataset.read", "par_larch_2018_2")
    assert not policy.allows(caller, "dataset.read", "par_larch_2021_1")


@pytest.mark.parametrize(
    "mutation",
    ["expired", "wrong_audience", "tampered", "other_tenant"],
)
def test_the_shim_refuses_what_it_should(
    tmp_path: Path,
    tenant: entra_mock.EntraTenant,
    base: str,
    clock: Clock,
    mutation: str,
) -> None:
    token = {
        "expired": lambda: tenant.issue(UPN, lifetime=-10),
        "wrong_audience": lambda: tenant.issue(UPN, audience="api://another-api"),
        "tampered": lambda: tenant.issue(UPN)[:-6] + "AAAAAA",
        "other_tenant": lambda: tenant.issue(
            UPN, claims={"tid": "11111111-1111-4111-8111-111111111111"}
        ),
    }[mutation]()
    # An unsigned-over tid change is a tampered payload only if the signature is stale; here the
    # claim is signed by our key, so the shim passes it and the platform's tenant check refuses it.
    assert verify(token, tmp_path, tenant, base, "/shim/introspect") is None


def test_a_role_claim_can_be_the_groups_claim_but_cannot_tell_the_deals_apart(
    tmp_path: Path, tenant: entra_mock.EntraTenant, base: str
) -> None:
    text = sign_in_file(tmp_path, tenant, base, "/shim/introspect").read_text()
    path = tmp_path / "roles.toml"
    path.write_text(text.replace('groups_claim = "groups"', 'groups_claim = "roles"'))
    sign_in = load_sign_in(path, {"LARCHMERE_INTROSPECTION_SECRET": SECRET})
    for upn in (UPN, "ines.vargas@larchmere.example"):
        access = anyio.run(sign_in.verifier().verify_token, tenant.issue(upn))
        assert access is not None and access.claims[GROUPS_CLAIM] == [
            "CLO.PM"
        ]  # both PMs, one role


def test_the_sign_in_file_cannot_name_an_app_id_uri_as_the_resource(tmp_path: Path) -> None:
    text = (FUND / "site" / "deploy" / "sign-in.toml").read_text()
    path = tmp_path / "sign-in.toml"
    path.write_text(text.replace(RESOURCE, "api://larchmere-qaos"))
    with pytest.raises(SignInError, match="not an http"):
        load_sign_in_file(path)


def test_the_real_sign_in_file_parses(tmp_path: Path) -> None:
    parsed = load_sign_in_file(FUND / "site" / "deploy" / "sign-in.toml")
    assert isinstance(parsed, SignInFile)
    server = parsed.authorization_servers[0]
    assert (
        server.issuer.endswith("/v2.0") and server.tenant_claim_value == server.issuer.split("/")[3]
    )
