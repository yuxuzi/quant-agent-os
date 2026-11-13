"""Unit tests for settings, logging redaction, and the CLI."""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from quant_agent_os.config import ConfigFileError, Settings, get_settings, home_dir
from quant_agent_os.observability import RedactSecretsFilter, configure_logging
from quant_agent_os.observability.logging import HANDLER_NAME, PACKAGES
from quant_agent_os.tools import Permission


def test_settings_read_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUANT_AGENT_OS_LOG_LEVEL", "DEBUG")

    settings = Settings()

    assert settings.log_level == "DEBUG"
    assert settings.granted_permissions == frozenset({Permission.READ_LOCAL_DATA})


def test_logging_filter_redacts_secrets() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "key=%s", ("sk-secret",), None)

    RedactSecretsFilter(["sk-secret"]).filter(record)

    assert record.getMessage() == "key=[REDACTED]"


@pytest.fixture
def package_loggers() -> Iterator[None]:
    """Restore the platform's loggers after a test configures them."""
    saved = {
        name: (logging.getLogger(name).level, list(logging.getLogger(name).handlers))
        for name in PACKAGES
    }
    yield
    for name, (level, handlers) in saved.items():
        logging.getLogger(name).setLevel(level)
        logging.getLogger(name).handlers = handlers


@pytest.mark.usefixtures("package_loggers")
def test_the_log_level_applies_to_every_package_once(caplog: pytest.LogCaptureFixture) -> None:
    configure_logging("debug", secrets=["sk-secret"])
    configure_logging("DEBUG", secrets=["sk-secret"])

    logging.getLogger("quant_research.ingest").debug("using key %s", "sk-secret")

    for name in PACKAGES:
        logger = logging.getLogger(name)
        assert logger.level == logging.DEBUG
        assert [h.get_name() for h in logger.handlers].count(HANDLER_NAME) == 1
    assert "using key [REDACTED]" in caplog.text  # still propagates, redacted on the way


@pytest.mark.usefixtures("package_loggers")
def test_an_unknown_log_level_stops_qaos(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from quant_platform.cli import app

    monkeypatch.setenv("QUANT_AGENT_OS_LOG_LEVEL", "LOUD")
    get_settings.cache_clear()
    try:
        result = CliRunner().invoke(app, ["data", "list"])
    finally:
        get_settings.cache_clear()

    assert result.exit_code == 1
    assert "LOUD" in result.output


def test_permissions_can_be_granted_through_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUANT_AGENT_OS_GRANTED_PERMISSIONS", '["read_local_data", "network"]')

    settings = Settings()

    assert settings.granted_permissions == frozenset(
        {Permission.READ_LOCAL_DATA, Permission.NETWORK}
    )


def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("QUANT_AGENT_OS_HOME", str(home))
    return home


def test_every_file_defaults_to_the_one_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)

    settings = Settings()

    assert home_dir() == home
    assert settings.store == str(home / "knowledge.db")
    assert settings.datasets_dir == home / "datasets"
    assert settings.runs_file == home / "runs.db"
    assert settings.api_keys_file == home / "api-keys.toml"


def test_the_environment_beats_the_homes_env_file_which_beats_config_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    (home / "config.toml").write_text('log_level = "ERROR"\nstore = "from-config.db"\n')
    (home / ".env").write_text("QUANT_AGENT_OS_LOG_LEVEL=WARNING\n")

    assert Settings().log_level == "WARNING"
    assert Settings().store == "from-config.db"

    monkeypatch.setenv("QUANT_AGENT_OS_LOG_LEVEL", "DEBUG")
    assert Settings().log_level == "DEBUG"


def test_a_env_file_in_the_working_directory_is_never_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _home(tmp_path, monkeypatch)
    (tmp_path / ".env").write_text('QUANT_AGENT_OS_GRANTED_PERMISSIONS=["network"]\n')
    monkeypatch.chdir(tmp_path)

    assert Settings().granted_permissions == frozenset({Permission.READ_LOCAL_DATA})


@pytest.mark.parametrize("key", ["granted_permissions", "preapproved_permissions"])
def test_config_toml_cannot_set_permissions(
    key: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    (home / "config.toml").write_text(f'{key} = ["network"]\n')

    with pytest.raises(ConfigFileError, match="QUANT_AGENT_OS_GRANTED_PERMISSIONS"):
        Settings()


def test_a_broken_config_toml_is_refused_with_its_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    (home / "config.toml").write_text("not toml")

    with pytest.raises(ConfigFileError, match=r"config\.toml"):
        Settings()


def test_the_summary_hides_a_password_in_the_store_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUANT_AGENT_OS_STORE", "postgresql://qa:s3cret@db/kb")

    assert Settings().public_summary()["store"] == "postgresql://***@db/kb"
