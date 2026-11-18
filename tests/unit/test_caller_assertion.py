"""The signed caller assertion a firm's service checks (decision 0030): format and refusals."""

import base64
import json
import re
import textwrap
from pathlib import Path
from typing import Any

import pytest

from quant_agent_os.caller_assertion import (
    BadSignatureError,
    ExpiredAssertionError,
    MalformedAssertionError,
    UnsupportedVersionError,
    WeakSecretError,
    WrongToolError,
)
from quant_agent_os.registration import (
    CALLER_HEADER,
    CallerAssertionError,
    VerifiedCaller,
    sign_caller,
    verify_caller,
)

SECRET = "s3cret-shared-with-the-risk-service-0123456789"
NOW = 1_763_460_000.0


def _sign(**overrides: Any) -> str:
    claims: dict[str, Any] = {
        "principal": "pm.equity",
        "tenant": "local",
        "groups": {"equity-desk", "pm"},
        "tool": "risk_desk_var",
        "now": NOW,
    }
    claims.update(overrides)
    return sign_caller(SECRET, **claims)


def _flip(header: str) -> str:
    """The header with its signature's first character changed."""
    version, payload, signature = header.split(".")
    return f"{version}.{payload}.{'A' if signature[0] != 'A' else 'B'}{signature[1:]}"


def _payload(header: str) -> dict[str, Any]:
    payload = header.split(".")[1]
    decoded: dict[str, Any] = json.loads(base64.urlsafe_b64decode(payload + "=" * 4))
    return decoded


def test_the_format_is_versioned_compact_and_documented() -> None:
    header = _sign()

    assert CALLER_HEADER == "X-QAOS-Caller"
    assert re.fullmatch(r"v1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}", header)
    claims = _payload(header)
    assert set(claims) == {"sub", "ten", "grp", "tool", "iat", "exp", "nonce"}
    assert claims["grp"] == ["equity-desk", "pm"]
    assert claims["exp"] - claims["iat"] == 60
    assert re.fullmatch(r"[0-9a-f]{32}", claims["nonce"])
    assert _payload(_sign())["nonce"] != claims["nonce"]
    assert SECRET not in header


def test_a_valid_assertion_gives_the_caller() -> None:
    caller = verify_caller(_sign(), SECRET, tool="risk_desk_var", now=NOW + 30)

    assert caller == VerifiedCaller(
        principal="pm.equity",
        tenant="local",
        groups=frozenset({"equity-desk", "pm"}),
        tool="risk_desk_var",
        issued_at=int(NOW),
        expires_at=int(NOW) + 60,
        nonce=caller.nonce,
    )
    assert verify_caller(_sign(), SECRET.encode(), now=NOW).principal == "pm.equity"


def test_a_tampered_assertion_is_refused() -> None:
    header = _sign()
    version, _, signature = header.split(".")
    claims = _payload(header)
    claims["grp"] = ["risk"]
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()

    with pytest.raises(BadSignatureError):
        verify_caller(f"{version}.{forged}.{signature}", SECRET, now=NOW)
    with pytest.raises(BadSignatureError):
        verify_caller(_flip(header), SECRET, now=NOW)
    with pytest.raises(BadSignatureError):
        verify_caller(_sign(), SECRET + "x", now=NOW)


def test_an_expired_or_future_assertion_is_refused() -> None:
    header = _sign()

    verify_caller(header, SECRET, now=NOW + 60 + 9)  # within the default leeway
    with pytest.raises(ExpiredAssertionError, match="expired"):
        verify_caller(header, SECRET, now=NOW + 60 + 10)
    with pytest.raises(ExpiredAssertionError, match="expired"):
        verify_caller(header, SECRET, now=NOW + 61, leeway=0)
    with pytest.raises(ExpiredAssertionError, match="future"):
        verify_caller(header, SECRET, now=NOW - 11)
    with pytest.raises(ExpiredAssertionError, match="too long"):
        verify_caller(_sign(lifetime=3600), SECRET, now=NOW)


def test_an_assertion_for_another_tool_is_refused() -> None:
    with pytest.raises(WrongToolError):
        verify_caller(_sign(), SECRET, tool="risk_stress", now=NOW)


@pytest.mark.parametrize(
    ("header", "error"),
    [
        (None, MalformedAssertionError),
        ("", MalformedAssertionError),
        ("v1.only-two", MalformedAssertionError),
        ("v2.abc.def", UnsupportedVersionError),
        ("v1." + "a" * 9000 + ".b", MalformedAssertionError),
        ("v1.é.b", MalformedAssertionError),
    ],
)
def test_a_malformed_header_is_refused(header: str | None, error: type[Exception]) -> None:
    with pytest.raises(error):
        verify_caller(header, SECRET, now=NOW)


def test_a_signed_payload_that_lacks_a_field_is_refused() -> None:
    import hashlib
    import hmac

    claims = _payload(_sign())
    del claims["sub"]
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    digest = hmac.new(SECRET.encode(), f"v1.{payload}".encode(), hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    with pytest.raises(MalformedAssertionError, match="field"):
        verify_caller(f"v1.{payload}.{signature}", SECRET, now=NOW)


def test_a_short_secret_is_refused_on_both_sides() -> None:
    with pytest.raises(WeakSecretError):
        sign_caller("short", principal="p", tenant="local", groups=(), tool="t")
    with pytest.raises(WeakSecretError):
        verify_caller(_sign(), "short", now=NOW)


def test_every_refusal_is_a_caller_assertion_error_that_never_names_the_secret() -> None:
    for header in ("", "v2.a.b", _flip(_sign())):
        with pytest.raises(CallerAssertionError) as raised:
            verify_caller(header, SECRET, now=NOW)
        assert SECRET not in str(raised.value)


def _documented_verifier() -> Any:
    text = (Path(__file__).parents[2] / "docs" / "deployment.md").read_text("utf-8")
    pattern = r"( *)```python\n(\1import base64.*?def verify_caller\(.*?)\1```"
    ((_, block),) = re.findall(pattern, text, re.S)
    code = textwrap.dedent(block)
    assert len(code.splitlines()) <= 30, "the documented verifier should stay about 30 lines"
    namespace: dict[str, Any] = {}
    exec(compile(code, "deployment.md", "exec"), namespace)
    return namespace["verify_caller"]


def test_the_documented_stdlib_verifier_agrees() -> None:
    verify = _documented_verifier()
    header = sign_caller(
        SECRET, principal="pm.equity", tenant="local", groups={"pm"}, tool="risk_desk_var"
    )

    claims = verify(header, SECRET.encode(), "risk_desk_var")

    assert (claims["sub"], claims["grp"]) == ("pm.equity", ["pm"])
    for bad, tool in ((_flip(header), "risk_desk_var"), (header, "risk_stress")):
        with pytest.raises(ValueError):
            verify(bad, SECRET.encode(), tool)
    with pytest.raises(ValueError):
        verify(_sign(), SECRET.encode(), "risk_desk_var")  # issued at NOW, long expired
