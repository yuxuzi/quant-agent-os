"""Unit tests for settings, providers, logging redaction, and the CLI."""

import logging

import pytest
from typer.testing import CliRunner

from quant_agent_os.cli import app
from quant_agent_os.config import Settings
from quant_agent_os.observability import RedactSecretsFilter
from quant_agent_os.providers import FakeModelProvider, ModelProvider, ProviderError
from quant_agent_os.tools import Permission


def test_settings_read_prefixed_environment_and_hide_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUANT_AGENT_OS_API_KEY", "sk-test-123")
    monkeypatch.setenv("QUANT_AGENT_OS_MAX_PLAN_STEPS", "3")

    settings = Settings(_env_file=None)

    assert settings.max_plan_steps == 3
    assert "sk-test-123" not in repr(settings)
    assert settings.public_summary()["api_key"] == "set"
    assert settings.granted_permissions == frozenset({Permission.READ_LOCAL_DATA})


def test_fake_provider_replays_scripted_responses() -> None:
    provider = FakeModelProvider(["first"])

    assert isinstance(provider, ModelProvider)
    assert provider.generate("prompt") == "first"
    assert provider.prompts == ["prompt"]
    with pytest.raises(ProviderError):
        provider.generate("again")


def test_logging_filter_redacts_secrets() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "key=%s", ("sk-secret",), None)

    RedactSecretsFilter(["sk-secret"]).filter(record)

    assert record.getMessage() == "key=[REDACTED]"


def test_cli_info_and_plan_schema() -> None:
    runner = CliRunner()

    info = runner.invoke(app, ["info"])
    schema = runner.invoke(app, ["plan-schema"])

    assert info.exit_code == 0
    assert "model_provider" in info.output
    assert schema.exit_code == 0
    assert '"steps"' in schema.output


def test_permissions_can_be_granted_through_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUANT_AGENT_OS_GRANTED_PERMISSIONS", '["read_local_data", "network"]')

    settings = Settings(_env_file=None)

    assert settings.granted_permissions == frozenset(
        {Permission.READ_LOCAL_DATA, Permission.NETWORK}
    )
