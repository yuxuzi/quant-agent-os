"""`quant-agent-os api-key new` (B-18): the key is printed once and only its hash is kept."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quant_agent_os.api import ApiKey, ApiKeyError, KeyRing, add_key, hash_key, load_keys
from quant_platform.cli import app


def test_a_new_key_is_printed_once_and_stored_as_a_hash(tmp_path: Path) -> None:
    path = tmp_path / "keys.toml"
    result = CliRunner().invoke(
        app,
        [
            "keys",
            "new",
            "--principal",
            "alice",
            "--scope",
            "tools:call",
            "--scope",
            "tools:list",
            "--keys",
            str(path),
        ],
    )

    assert result.exit_code == 0, result.output
    key = result.stdout.strip()
    assert key.startswith("qaos_") and key not in path.read_text("utf-8")
    (stored,) = load_keys(path)
    assert (stored.principal, stored.sha256) == ("alice", hash_key(key))
    assert stored.scopes == {"tools:call", "tools:list"}


def test_unknown_scopes_and_bad_principals_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "keys.toml"
    runner = CliRunner()

    scope = runner.invoke(
        app, ["keys", "new", "--principal", "a", "--scope", "admin", "--keys", str(path)]
    )
    principal = runner.invoke(
        app, ["keys", "new", "--principal", "a b", "--scope", "runs:read", "--keys", str(path)]
    )

    assert scope.exit_code == 2 and "unknown scope admin" in scope.stderr
    assert principal.exit_code == 1 and "principal" in principal.stderr
    assert not path.exists()


def _write_key(path: Path, key: str, extra: str = "") -> None:
    path.write_text(
        f'[[keys]]\nprincipal = "alice"\nsha256 = "{hash_key(key)}"\n'
        f'scopes = ["tools:list"]\n{extra}',
        "utf-8",
    )


def test_an_expired_key_is_refused_and_a_future_one_accepted() -> None:
    now = datetime.now(UTC)
    future = ApiKey(
        principal="a",
        sha256=hash_key("future"),
        scopes=frozenset({"tools:list"}),
        expires=now + timedelta(hours=1),
    )
    past = future.model_copy(update={"sha256": hash_key("past"), "expires": now})
    ring = KeyRing([future, past])

    assert ring.authenticate("future") == future
    assert ring.authenticate("past") is None
    assert len(ring) == 1


def test_an_expiry_must_name_its_timezone(tmp_path: Path) -> None:
    path = tmp_path / "keys.toml"
    _write_key(path, "key", "expires = 2027-01-31T00:00:00\n")

    with pytest.raises(ApiKeyError, match="expires"):
        load_keys(path)


def test_add_key_writes_an_expiry_the_server_reads(tmp_path: Path) -> None:
    path = tmp_path / "keys.toml"
    when = datetime(2099, 1, 31, tzinfo=UTC)

    key = add_key(path, "alice", ["tools:list"], expires=when)

    assert load_keys(path)[0].expires == when
    assert KeyRing.from_file(path).authenticate(key) is not None


def test_a_ring_follows_its_file_without_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "keys.toml"
    first = add_key(path, "alice", ["tools:list"])
    ring = KeyRing.from_file(path)
    assert ring.authenticate(first) is not None

    second = add_key(path, "bob", ["tools:list"])  # issued while running
    assert ring.authenticate(second) is not None

    _write_key(path, second)  # first revoked while running
    assert ring.authenticate(first) is None
    assert ring.authenticate(second) is not None


@pytest.mark.parametrize("broken", ["this is not toml", None])
def test_a_ring_accepts_nothing_while_its_file_is_broken(
    tmp_path: Path, broken: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "keys.toml"
    key = add_key(path, "alice", ["tools:list"])
    ring = KeyRing.from_file(path)

    if broken is None:
        path.unlink()
    else:
        path.write_text(broken, "utf-8")
    assert ring.authenticate(key) is None
    assert "Accepting no API keys" in caplog.text
    assert key not in caplog.text

    _write_key(path, key)  # fixed
    assert ring.authenticate(key) is not None
