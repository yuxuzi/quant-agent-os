"""Logging setup that keeps configured secrets out of every log record."""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterable
from typing import TextIO

REDACTED = "[REDACTED]"
HANDLER_NAME = "quant-agent-os"


class RedactSecretsFilter(logging.Filter):
    """Replace known secret values in log messages and arguments."""

    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self._secrets = [secret for secret in secrets if secret]

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact the record in place; never drop it."""
        if self._secrets:
            message = record.getMessage()
            for secret in self._secrets:
                message = message.replace(secret, REDACTED)
            record.msg, record.args = message, None
        return True


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


def configure_logging(level: str = "INFO", secrets: Iterable[str] = ()) -> logging.Logger:
    """Send the platform's log records at ``level`` and above to stderr, secrets redacted.

    Applies to every package in `PACKAGES` and returns the core package's logger. Calling it
    again replaces its own handler rather than adding another. Records still propagate, so an
    application's own logging configuration sees them too. Raises `ValueError` for an unknown
    level name.
    """
    handler = _StderrHandler()
    handler.set_name(HANDLER_NAME)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactSecretsFilter(secrets))
    for name in PACKAGES:
        logger = logging.getLogger(name)
        logger.setLevel(level.upper())
        logger.handlers = [h for h in logger.handlers if h.get_name() != HANDLER_NAME]
        logger.addHandler(handler)
    return logging.getLogger(PACKAGES[0])
