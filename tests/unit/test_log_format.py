"""The log formats (QUANT_AGENT_OS_LOG_FORMAT): one JSON object per line, or the text as before."""

import json
import logging
import re
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from quant_agent_os.api import add_key
from quant_agent_os.config import Settings, get_settings
from quant_agent_os.observability import configure_logging
from quant_agent_os.observability.logging import PACKAGES
from quant_agent_os.tools import PermissionPolicy
from quant_platform import compose, serve_http
from quant_platform.cli import app as cli


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


def _lines(err: str) -> list[str]:
    return [line for line in err.splitlines() if line.strip()]


@pytest.mark.usefixtures("package_loggers")
def test_each_json_line_is_one_object_with_the_keys(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("info", log_format="json")

    logging.getLogger("quant_research.ingest").warning("indexed %d documents", 3)

    (line,) = _lines(capsys.readouterr().err)
    entry = json.loads(line)
    assert set(entry) == {"time", "level", "logger", "message"}
    assert (entry["level"], entry["logger"], entry["message"]) == (
        "WARNING",
        "quant_research.ingest",
        "indexed 3 documents",
    )
    assert datetime.fromisoformat(entry["time"]).utcoffset() == timedelta(0)


@pytest.mark.usefixtures("package_loggers")
def test_an_exception_is_one_more_key_on_the_same_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("info", log_format="json")

    try:
        raise ValueError("bad row 7")
    except ValueError:
        logging.getLogger("quant_platform.load").exception("load failed")

    (line,) = _lines(capsys.readouterr().err)
    entry = json.loads(line)
    assert entry["message"] == "load failed"
    assert "ValueError: bad row 7" in entry["exception"]


@pytest.mark.usefixtures("package_loggers")
def test_json_redacts_the_message_and_the_exception_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("info", secrets=["sk-live-0123"], log_format="json")

    try:
        raise RuntimeError("call failed: Authorization: Bearer abc.def-123, key sk-live-0123")
    except RuntimeError:
        logging.getLogger("quant_research.embed").exception("embedder refused sk-live-0123")

    err = capsys.readouterr().err
    assert "sk-live-0123" not in err and "abc.def-123" not in err
    (line,) = _lines(err)
    entry = json.loads(line)
    assert entry["message"] == "embedder refused [REDACTED]"
    assert "Authorization: [REDACTED]" in entry["exception"]
    assert "key [REDACTED]" in entry["exception"]


@pytest.mark.usefixtures("package_loggers")
def test_the_text_format_is_unchanged_and_redacts_exception_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("info", secrets=["sk-live-0123"])
    logging.getLogger("quant_research.ingest").warning("indexed %d documents", 3)
    try:
        raise RuntimeError("key sk-live-0123")
    except RuntimeError:
        logging.getLogger("quant_research.embed").exception("failed")

    first, *rest = _lines(capsys.readouterr().err)
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} WARNING quant_research\.ingest "
        r"indexed 3 documents",
        first,
    )
    err = first + "\n" + "\n".join(rest)
    assert "sk-live-0123" not in err and "RuntimeError: key [REDACTED]" in err


def test_the_default_is_text_and_an_unknown_value_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("QUANT_AGENT_OS_LOG_FORMAT", raising=False)
    assert Settings().log_format == "text"

    monkeypatch.setenv("QUANT_AGENT_OS_LOG_FORMAT", "xml")
    with pytest.raises(
        ValidationError, match="QUANT_AGENT_OS_LOG_FORMAT must be one of text, json"
    ):
        Settings()
    monkeypatch.setenv("QUANT_AGENT_OS_LOG_FORMAT", "JSON")  # exact values only
    with pytest.raises(ValidationError, match="must be one of text, json"):
        Settings()


def test_configure_refuses_an_unknown_format() -> None:
    with pytest.raises(ValueError, match="log format must be one of text, json"):
        configure_logging("info", log_format="yaml")


def test_qaos_refuses_an_unknown_format_at_start_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUANT_AGENT_OS_LOG_FORMAT", "xml")
    get_settings.cache_clear()
    try:
        result = CliRunner().invoke(cli, ["data", "list"])
    finally:
        get_settings.cache_clear()

    assert result.exit_code == 1
    assert "QUANT_AGENT_OS_LOG_FORMAT must be one of text, json" in result.output


@pytest.mark.usefixtures("package_loggers")
def test_a_served_server_logs_in_the_configured_format(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("QUANT_AGENT_OS_LOG_FORMAT", "json")
    get_settings.cache_clear()
    monkeypatch.setattr("quant_platform.serve.run_http", lambda app, **_: None)
    add_key(tmp_path / "keys.toml", "team", ["tools:list"])
    try:
        serve_http(
            compose(policy=PermissionPolicy()),
            keys=tmp_path / "keys.toml",
            runs=tmp_path / "runs.db",
        )
    finally:
        get_settings.cache_clear()
    capsys.readouterr()

    logging.getLogger("quant_platform.serve").warning("ready to serve")

    assert json.loads(_lines(capsys.readouterr().err)[-1])["message"] == "ready to serve"
