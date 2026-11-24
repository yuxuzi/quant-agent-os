"""NAME or NAME_FILE for each secret the platform reads (quant_agent_os.config.secrets)."""

from pathlib import Path

import pytest

from quant_agent_os.config import SecretConfigError, read_secret, secret_sources


def test_the_variable_is_used_as_it_is() -> None:
    assert read_secret("K", {"K": "abc"}) == "abc"


def test_neither_variable_set_is_none() -> None:
    assert read_secret("K", {}) is None
    assert read_secret("K", {"OTHER": "x", "OTHER_FILE": "y"}) is None


def test_an_empty_variable_counts_as_unset(tmp_path: Path) -> None:
    path = tmp_path / "k"
    path.write_text("from-file\n", "utf-8")

    assert read_secret("K", {"K": "", "K_FILE": str(path)}) == "from-file"
    assert read_secret("K", {"K": ""}) is None


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("one\n", "one"),  # one trailing line break goes
        ("one", "one"),  # none to go
        ("one\r\n", "one"),  # a Windows editor's line break is one too
        ("one\n\n", "one\n"),  # only one
        ("two  \n", "two  "),  # spaces are part of the secret
    ],
)
def test_the_file_is_read_as_utf8_and_loses_one_trailing_line_break(
    tmp_path: Path, content: str, expected: str
) -> None:
    path = tmp_path / "secret"
    path.write_bytes(content.encode("utf-8"))

    assert read_secret("K", {"K_FILE": str(path)}) == expected


def test_both_set_is_refused_naming_both_variables_and_no_value(tmp_path: Path) -> None:
    path = tmp_path / "k"
    path.write_text("file-value\n", "utf-8")

    with pytest.raises(SecretConfigError) as found:
        read_secret("K", {"K": "env-value", "K_FILE": str(path)})

    message = str(found.value)
    assert "K or K_FILE" in message
    assert "env-value" not in message and "file-value" not in message


def test_an_unreadable_file_is_named_by_variable_and_path_not_content(tmp_path: Path) -> None:
    missing = tmp_path / "missing.key"

    with pytest.raises(SecretConfigError) as found:
        read_secret("K", {"K_FILE": str(missing)})

    message = str(found.value)
    assert f"K_FILE names {missing}, which cannot be read" in message
    assert "No such file" in message


def test_a_directory_is_an_unreadable_file(tmp_path: Path) -> None:
    with pytest.raises(SecretConfigError, match=r"K_FILE names .*, which cannot be read"):
        read_secret("K", {"K_FILE": str(tmp_path)})


def test_an_empty_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "k"
    path.write_text("\n", "utf-8")

    with pytest.raises(SecretConfigError, match=r"K_FILE names .*, which holds no secret"):
        read_secret("K", {"K_FILE": str(path)})


def test_a_file_that_is_not_utf8_is_refused_without_its_bytes(tmp_path: Path) -> None:
    path = tmp_path / "k"
    path.write_bytes(b"\xff\xfe-secret-bytes")

    with pytest.raises(SecretConfigError) as found:
        read_secret("K", {"K_FILE": str(path)})

    assert "not UTF-8 text" in str(found.value)
    assert "secret-bytes" not in str(found.value)


def test_the_process_environment_is_the_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "k"
    path.write_text("from-file", "utf-8")
    monkeypatch.setenv("QAOS_TEST_SECRET_FILE", str(path))
    monkeypatch.delenv("QAOS_TEST_SECRET", raising=False)

    assert read_secret("QAOS_TEST_SECRET") == "from-file"


def test_the_home_env_file_is_read_under_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    secret_file = tmp_path / "k"
    secret_file.write_text("from-file", "utf-8")
    (home / ".env").write_text(f"K_FILE={secret_file}\nJ=from-dotenv\n", "utf-8")
    monkeypatch.setenv("QUANT_AGENT_OS_HOME", str(home))
    monkeypatch.delenv("K", raising=False)
    monkeypatch.delenv("K_FILE", raising=False)
    monkeypatch.delenv("J", raising=False)

    sources = secret_sources()

    assert read_secret("K", sources) == "from-file"
    assert read_secret("J", sources) == "from-dotenv"
    monkeypatch.setenv("J", "from-env")  # the environment wins over .env
    assert read_secret("J", secret_sources()) == "from-env"


def test_postgres_takes_its_password_from_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    psycopg = pytest.importorskip("psycopg")
    from quant_research.infrastructure.postgres_store import PostgresKnowledgeStore
    from quant_research.infrastructure.store_common import StoreError

    seen: dict[str, object] = {}

    def refuse(url: str, **options: object) -> None:
        seen.update(options)
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(psycopg, "connect", refuse)
    password = tmp_path / "pg"
    password.write_text("pg-file-secret\n", "utf-8")
    monkeypatch.delenv("PGPASSWORD", raising=False)
    monkeypatch.setenv("PGPASSWORD_FILE", str(password))

    with pytest.raises(StoreError, match="connection refused"):
        PostgresKnowledgeStore("postgresql://reader@db.example/kb")
    assert seen["password"] == "pg-file-secret"

    seen.clear()
    with pytest.raises(StoreError):
        PostgresKnowledgeStore("postgresql://reader:url-secret@db.example/kb")
    assert "password" not in seen  # the URL's own password is left to libpq, as before
