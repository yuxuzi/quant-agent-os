"""The factory settings file, `~/.quant-agent-os/factory.toml` (decision 0008).

It holds identity defaults, named database connections, and registered template packs. It never
holds a secret: connections name the environment variables that hold passwords or URLs, unknown
fields are refused, and values that look like secrets are refused.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import tomllib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from quant_agent_os.runtime.runner import CommandResult, run_command
from quant_app_factory.domain import toml_text
from quant_app_factory.domain.connections import Connection
from quant_app_factory.domain.secrets import secret_problem
from quant_app_factory.domain.spec import Identity

DEFAULT_SETTINGS = Path.home() / ".quant-agent-os" / "factory.toml"


class SettingsError(ValueError):
    """Raised when the settings file cannot be read, or a change is refused."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class RegisteredTemplate(_Strict):
    """A user template pack registered by name."""

    source: str
    ref: str | None = None
    path: str


class FactorySettings(_Strict):
    """Everything in `factory.toml`."""

    identity: Identity = Field(default_factory=Identity)
    connections: dict[str, Connection] = Field(default_factory=dict)
    templates: dict[str, RegisteredTemplate] = Field(default_factory=dict)


def settings_path() -> Path:
    """Return the settings file: ``$QUANT_FACTORY_SETTINGS``, or the default location."""
    return Path(os.environ.get("QUANT_FACTORY_SETTINGS", DEFAULT_SETTINGS))


def load_settings(path: Path | None = None) -> FactorySettings:
    """Read the settings file; a missing file reads as empty settings."""
    path = path or settings_path()
    if not path.is_file():
        return FactorySettings()
    try:
        data = tomllib.loads(path.read_text("utf-8"))
        settings = FactorySettings.model_validate(data)
    except (OSError, tomllib.TOMLDecodeError, ValidationError) as error:
        raise SettingsError(f"Cannot use the settings file {path}: {error}") from error
    _refuse_secrets(settings)
    return settings


def save_settings(settings: FactorySettings, path: Path | None = None) -> Path:
    """Write ``settings`` atomically, readable only by the owner on POSIX."""
    _refuse_secrets(settings)
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = toml_text.table("identity", settings.identity.model_dump())
    for name, connection in sorted(settings.connections.items()):
        lines += toml_text.table(
            f"connections.{toml_text.key(name)}", connection.model_dump(by_alias=True)
        )
    for name, template in sorted(settings.templates.items()):
        lines += toml_text.table(f"templates.{toml_text.key(name)}", template.model_dump())
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".factory-", suffix=".toml")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write("\n".join(lines).rstrip() + "\n" if lines else "")
    if sys.platform != "win32":
        os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return path


LOCK_TIMEOUT_SECONDS = 10.0


@contextmanager
def settings_lock(
    path: Path | None = None, timeout: float = LOCK_TIMEOUT_SECONDS
) -> Iterator[None]:
    """Hold an exclusive lock on the settings file, so concurrent updates never overwrite."""
    path = path or settings_path()
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise SettingsError(
                    f"{path} is being updated by another command (lock {lock}); try again, or "
                    "remove the lock file if no other command is running."
                ) from None
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(handle)
        lock.unlink(missing_ok=True)


def update_settings(
    change: Callable[[FactorySettings], FactorySettings], path: Path | None = None
) -> FactorySettings:
    """Load, change, and save the settings file under its lock; return the saved settings."""
    path = path or settings_path()
    with settings_lock(path):
        updated = change(load_settings(path))
        save_settings(updated, path)
    return updated


def set_value(settings: FactorySettings, dotted: str, raw: str) -> FactorySettings:
    """Return ``settings`` with ``dotted`` (such as ``connections.pg.host``) set to ``raw``."""
    return _edit(settings, dotted, raw)


def remove_value(settings: FactorySettings, dotted: str) -> FactorySettings:
    """Return ``settings`` without ``dotted`` (a field, or a whole connection or template)."""
    return _edit(settings, dotted, None)


def _edit(settings: FactorySettings, dotted: str, raw: str | None) -> FactorySettings:
    parts = dotted.split(".")
    data = settings.model_dump(by_alias=True)
    if parts[0] == "identity" and len(parts) == 2:
        section = data["identity"]
        field = parts[1]
    elif parts[0] in ("connections", "templates") and len(parts) in (2, 3):
        entries = data[parts[0]]
        if len(parts) == 2:
            if raw is not None:
                raise SettingsError(f"Set a field, such as {dotted}.host, not the whole entry.")
            entries.pop(parts[1], None)
            return _validated(data)
        section = entries.setdefault(parts[1], {})
        field = parts[2]
    else:
        raise SettingsError(
            f"Unknown setting {dotted!r}; use identity.FIELD, connections.NAME.FIELD, or "
            "templates.NAME.FIELD."
        )
    if raw is None:
        section.pop(field, None)
    else:
        if not field.endswith("_env") and (problem := secret_problem(field, raw)):
            hint = (
                " For a connection, set password_env or url_env to the variable's name."
                if parts[0] == "connections"
                else ""
            )
            raise SettingsError(problem + "." + hint)
        section[field] = _typed(raw)
    return _validated(data)


def _typed(raw: str) -> Any:
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    return int(raw) if raw.isdigit() else raw


def _validated(data: dict[str, Any]) -> FactorySettings:
    try:
        return FactorySettings.model_validate(data)
    except ValidationError as error:
        raise SettingsError(str(error)) from error


def _refuse_secrets(settings: FactorySettings) -> None:
    sections: list[tuple[str, dict[str, Any]]] = [("identity", settings.identity.model_dump())]
    sections += [(f"connections.{n}", c.model_dump()) for n, c in settings.connections.items()]
    for label, fields in sections:
        for field, item in fields.items():
            if field.endswith("_env") or not isinstance(item, str):
                continue  # *_env fields hold variable names, validated as such
            if problem := secret_problem(field, item):
                raise SettingsError(f"{label}: {problem}")


def git_identity(runner: Callable[..., CommandResult] = run_command) -> Identity:
    """Return the author and email from ``git config``, or an empty identity without git."""
    found: dict[str, str] = {}
    for field, option in (("author", "user.name"), ("email", "user.email")):
        result = runner(["git", "config", "--get", option], cwd=Path.home(), timeout=5)
        text = result.stdout_tail.strip()
        if result.ok and text and '"' not in text and "\\" not in text:
            found[field] = text
    return Identity(**found)
