"""Logging setup that keeps configured secrets out of every log record."""

from __future__ import annotations

import logging
from collections.abc import Iterable

REDACTED = "[REDACTED]"


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


def configure_logging(level: str = "INFO", secrets: Iterable[str] = ()) -> logging.Logger:
    """Configure the package logger with secret redaction and return it."""
    logger = logging.getLogger("quant_agent_os")
    logger.setLevel(level)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactSecretsFilter(secrets))
    logger.handlers = [handler]
    logger.propagate = False
    return logger
