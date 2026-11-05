"""Unit tests for settings, logging redaction, and the CLI."""

import logging

import pytest
from typer.testing import CliRunner

from quant_agent_os.cli import app
from quant_agent_os.config import Settings
from quant_agent_os.observability import RedactSecretsFilter
from quant_agent_os.tools import Permission


def test_settings_read_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUANT_AGENT_OS_LOG_LEVEL", "DEBUG")

    settings = Settings(_env_file=None)

    assert settings.log_level == "DEBUG"
    assert settings.granted_permissions == frozenset({Permission.READ_LOCAL_DATA})


def test_logging_filter_redacts_secrets() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "key=%s", ("sk-secret",), None)

    RedactSecretsFilter(["sk-secret"]).filter(record)

    assert record.getMessage() == "key=[REDACTED]"


def test_cli_info() -> None:
    info = CliRunner().invoke(app, ["info"])

    assert info.exit_code == 0
    assert "granted_permissions" in info.output


def test_permissions_can_be_granted_through_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUANT_AGENT_OS_GRANTED_PERMISSIONS", '["read_local_data", "network"]')

    settings = Settings(_env_file=None)

    assert settings.granted_permissions == frozenset(
        {Permission.READ_LOCAL_DATA, Permission.NETWORK}
    )
