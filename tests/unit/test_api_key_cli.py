"""`quant-agent-os api-key new` (B-18): the key is printed once and only its hash is kept."""

from pathlib import Path

from typer.testing import CliRunner

from quant_agent_os.api import hash_key, load_keys
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
