"""A sign-in scope map that grants tools:call and never tools:list is warned about, offline."""

from pathlib import Path

import pytest

from quant_platform.identity.sign_in import load_sign_in, scope_gap
from quant_platform.onboarding import _check_sign_in
from quant_platform.serve import warn_about_scopes

COGNITO = """
resource = "https://qaos.example.com/mcp"

[[authorization_servers]]
issuer = "https://cognito-idp.eu-west-1.amazonaws.com/eu-west-1_Example"
verify = "jwks"
client_ids = ["3n4b5v6c7x8z"]
token_use = "access"
tenant_claim = ""
groups_claims = ["cognito:groups"]

[authorization_servers.scope_map]
{scopes}
"""


def _file(tmp_path: Path, scopes: str) -> Path:
    path = tmp_path / "sign-in.toml"
    path.write_text(COGNITO.replace("{scopes}", scopes), "utf-8")
    return path


def test_a_scope_map_that_grants_the_call_without_the_listing_is_a_gap() -> None:
    assert scope_gap({"qaos/tools.call": "tools:call"}) is not None
    assert scope_gap({"qaos/tools.call": "tools:call", "qaos/tools.list": "tools:list"}) is None
    assert scope_gap({"qaos/tools.list": "tools:list"}) is None
    assert scope_gap({}) is None  # no mapping: the provider's own names pass unchanged


def test_doctor_warns_on_a_listing_gap_and_is_quiet_when_both_are_granted(tmp_path: Path) -> None:
    rows: list[tuple[str, str, str]] = []

    def add(check: str, status: str, detail: str) -> None:
        rows.append((check, status, detail))

    _check_sign_in(_file(tmp_path, '"qaos/tools.call" = "tools:call"'), add, access=True)
    warned = [row for row in rows if row[0].startswith("sign-in scopes:")]
    assert len(warned) == 1 and warned[0][1] == "warn" and "tools:list" in warned[0][2]

    rows.clear()
    both = '"qaos/tools.list" = "tools:list"\n"qaos/tools.call" = "tools:call"'
    _check_sign_in(_file(tmp_path, both), add, access=True)
    assert not [row for row in rows if row[0].startswith("sign-in scopes:")]


def test_serve_warns_at_start_up_naming_the_provider(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    issuer = "https://cognito-idp.eu-west-1.amazonaws.com/eu-west-1_Example"
    warn_about_scopes(load_sign_in(_file(tmp_path, '"qaos/tools.call" = "tools:call"')))
    err = capsys.readouterr().err
    assert issuer in err and "never tools:list" in err

    warn_about_scopes(None)  # no sign-in: nothing to say
    warn_about_scopes(
        load_sign_in(
            _file(tmp_path, '"qaos/tools.list" = "tools:list"\n"qaos/tools.call" = "tools:call"')
        )
    )
    assert capsys.readouterr().err == ""
