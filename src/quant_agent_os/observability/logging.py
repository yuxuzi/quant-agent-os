"""Logging setup that keeps configured secrets out of every log record.

Two formats, chosen by ``QUANT_AGENT_OS_LOG_FORMAT``: ``text`` (the default), and ``json``, one
object per line with ``time`` (ISO 8601, UTC), ``level``, ``logger``, ``message``, and
``exception`` when the record carries one. Both redact the message and the exception text alike.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import TextIO

REDACTED = "[REDACTED]"
_SECRET_NAME = r"(?:api[_-]?key|token|secret|password|passwd|authorization|credential)"
# Secrets that text such as an exception message commonly carries: a URL's password, a bearer
# token, and a value given under a secret-sounding name ("password=...", "token: ...").
_SECRET_TEXT = (
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://[^/\s:@]+:)[^/\s@]+@", re.I), rf"\g<1>{REDACTED}@"),
    (re.compile(r"(\bbearer\s+)[A-Za-z0-9._~+/=-]+", re.I), rf"\g<1>{REDACTED}"),
    (
        re.compile(
            rf"(\b\w*{_SECRET_NAME}\w*[\"']?\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;)}}\]]+)", re.I
        ),
        rf"\g<1>{REDACTED}",
    ),
)
HANDLER_NAME = "quant-agent-os"
LOG_FORMATS = ("text", "json")
TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def redact_text(text: str) -> str:
    """Return ``text`` with the secrets it commonly carries replaced by `REDACTED`.

    Covers a password in a URL, a bearer token, and a value after a secret-sounding name, such
    as ``password=...``. It is a safety net for messages that quote what they were given; it
    cannot find a secret that nothing marks.
    """
    for pattern, replacement in _SECRET_TEXT:
        text = pattern.sub(replacement, text)
    return text


class RedactSecretsFilter(logging.Filter):
    """Replace known secret values, and what `redact_text` finds, in messages and exception text."""

    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self._secrets = [secret for secret in secrets if secret]

    def _scrub(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        return redact_text(text)

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact the record in place, its message and its exception text; never drop it."""
        message = record.getMessage()
        scrubbed = self._scrub(message)
        if scrubbed != message:
            record.msg, record.args = scrubbed, None
        if record.exc_info:
            # Cached on the record, as the formatters cache it, so every handler shows it redacted.
            exception = record.exc_text or logging.Formatter().formatException(record.exc_info)
            record.exc_text = self._scrub(exception)
        return True


class JsonFormatter(logging.Formatter):
    """Format each record as one JSON object on one line."""

    def format(self, record: logging.LogRecord) -> str:
        """Return the record's JSON object; its message and exception text are already redacted."""
        entry = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = record.exc_text or self.formatException(record.exc_info)
        return json.dumps(entry)


# The platform's packages; each logs under its own name.
PACKAGES = (
    "quant_agent_os",
    "quant_platform",
    "quant_research",
    "quant_analytics",
    "quant_app_factory",
)


class _StderrHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Writes to ``sys.stderr`` as it is at each record, so a later redirection is followed."""

    def __init__(self) -> None:
        super().__init__()

    @property
    def stream(self) -> TextIO:
        return sys.stderr

    @stream.setter
    def stream(self, _: TextIO) -> None:
        pass


def configure_logging(
    level: str = "INFO", secrets: Iterable[str] = (), log_format: str = "text"
) -> logging.Logger:
    """Send the platform's log records at ``level`` and above to stderr, secrets redacted.

    ``log_format`` is ``text`` or ``json`` (see the module's docstring). Applies to every package
    in `PACKAGES` and returns the core package's logger. Calling it again replaces its own handler
    rather than adding another. Records still propagate, so an application's own logging
    configuration sees them too. Raises `ValueError` for an unknown level name or log format.
    """
    if log_format not in LOG_FORMATS:
        raise ValueError(
            f"The log format must be one of {', '.join(LOG_FORMATS)}, not {log_format!r}."
        )
    handler = _StderrHandler()
    handler.set_name(HANDLER_NAME)
    handler.setFormatter(
        JsonFormatter() if log_format == "json" else logging.Formatter(TEXT_FORMAT)
    )
    handler.addFilter(RedactSecretsFilter(secrets))
    for name in PACKAGES:
        logger = logging.getLogger(name)
        logger.setLevel(level.upper())
        logger.handlers = [h for h in logger.handlers if h.get_name() != HANDLER_NAME]
        logger.addHandler(handler)
    return logging.getLogger(PACKAGES[0])
