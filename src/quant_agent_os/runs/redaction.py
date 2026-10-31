"""Keep secrets out of persisted runs."""

from __future__ import annotations

import re
from typing import Any

from pydantic import SecretBytes, SecretStr

from quant_agent_os.observability.logging import REDACTED

_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|token|secret|password|passwd|authorization|credential)", re.I
)


def redact(value: Any) -> Any:
    """Return ``value`` with secrets replaced by `REDACTED`, recursively.

    A secret is a `SecretStr` or `SecretBytes`, or any value stored under a key that names
    one ("api_key", "token", "password", ...). Tools take no secrets, so this is a safety net
    for arguments a client sends anyway.
    """
    if isinstance(value, SecretStr | SecretBytes):
        return REDACTED
    if isinstance(value, dict):
        return {
            key: REDACTED if _SECRET_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    return value
