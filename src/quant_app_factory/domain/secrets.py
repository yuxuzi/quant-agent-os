"""Secrets never enter specs or settings files: only environment-variable references do."""

from __future__ import annotations

import re

ENV_REFERENCE = re.compile(r"^env:[A-Za-z_][A-Za-z0-9_]*$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_NAME = re.compile(
    r"(?:password|passwd|secret|token|api[_-]?keys?|credential|private[_-]?key)", re.I
)
_URL_CREDENTIALS = re.compile(r"://[^/@\s:]+:[^/@\s]+@")


def is_reference(value: str) -> bool:
    """Return whether ``value`` is an environment-variable reference such as ``env:DB_URL``."""
    return bool(ENV_REFERENCE.match(value))


def secret_problem(name: str, value: str, *, declared_secret: bool = False) -> str | None:
    """Return why ``value`` may not be stored under ``name``, or None if it may.

    A value is refused when its name or declaration marks it secret and it is not a reference,
    or when it embeds credentials in a URL (``scheme://user:password@host``).
    """
    if is_reference(value):
        return None
    if declared_secret or _SECRET_NAME.search(name):
        return (
            f"{name} is secret: give an environment-variable reference such as "
            f"env:{re.sub(r'[^A-Za-z0-9_]', '_', name).upper()}, never the value"
        )
    if _URL_CREDENTIALS.search(value):
        return (
            f"{name} contains credentials in a URL: put the URL in an environment variable "
            "and give env:NAME"
        )
    return None
