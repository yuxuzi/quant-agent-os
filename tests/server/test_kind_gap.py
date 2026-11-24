"""A provider that accepts tokens by client id must say which kind (security review F-16)."""

from quant_platform.identity.sign_in import kind_gap


def test_client_ids_without_token_use_draw_a_warning() -> None:
    gap = kind_gap(["app-client"], None)

    assert gap is not None and 'token_use = "access"' in gap


def test_a_stated_token_use_or_audiences_only_draw_none() -> None:
    assert kind_gap(["app-client"], "access") is None
    assert kind_gap([], None) is None
