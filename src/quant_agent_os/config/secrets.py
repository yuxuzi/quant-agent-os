"""Secrets from the environment, or from a file named by ``NAME_FILE``.

For a secret ``NAME`` the platform reads, set either ``NAME`` to its value, or ``NAME_FILE`` to
the path of a file holding it. Container platforms mount secrets as files (Docker and Kubernetes
secrets, Key Vault and Secrets Manager mounts, Azure Container Apps secret volumes), so the file
form is the one to use there. Setting both is refused, so the platform never guesses which one
holds the secret. A variable set to nothing counts as unset.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from quant_agent_os.config.settings import home_env_file


class SecretConfigError(ValueError):
    """A secret is given twice, or its file cannot be read.

    The message names the variables and the path, never a value.
    """


def read_secret(name: str, environ: Mapping[str, str | None] | None = None) -> str | None:
    """Return the secret ``name``: its value, or the contents of the file ``NAME_FILE`` names.

    ``environ`` defaults to the process environment; pass `secret_sources` to read the home's
    ``.env`` as well. A file's content is read as UTF-8 and loses one trailing line break (LF or
    CRLF), as an editor or ``echo`` adds one. Returns None when neither variable is set. Raises
    `SecretConfigError` when both are set, when the file cannot be read, or when it is empty or not
    UTF-8 text.
    """
    values = os.environ if environ is None else environ
    file_name = f"{name}_FILE"
    value = values.get(name) or None
    path = values.get(file_name) or None
    if value is not None and path is not None:
        raise SecretConfigError(
            f"Set {name} or {file_name}, not both, so it cannot tell which holds the secret."
        )
    if value is not None:
        return value
    if path is None:
        return None
    return _read_file(file_name, path)


def _read_file(file_name: str, path: str) -> str:
    try:
        raw = Path(path).read_bytes()
    except (OSError, ValueError) as error:
        reason = error.strerror if isinstance(error, OSError) and error.strerror else "not a path"
        raise SecretConfigError(
            f"{file_name} names {path}, which cannot be read: {reason}."
        ) from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SecretConfigError(f"{file_name} names {path}, which is not UTF-8 text.") from None
    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]
    if not text:
        raise SecretConfigError(f"{file_name} names {path}, which holds no secret.")
    return text


def secret_sources() -> dict[str, str | None]:
    """Return the home's ``.env`` overlaid by the environment: where a secret may be read from."""
    from dotenv import dotenv_values

    path = home_env_file()
    values: dict[str, str | None] = dict(dotenv_values(path)) if path.is_file() else {}
    return values | dict(os.environ)
