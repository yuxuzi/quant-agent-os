"""Runtime settings, with one home for every file the platform keeps.

``QUANT_AGENT_OS_HOME`` (default ``~/.quant-agent-os``) holds ``config.toml``, ``.env``,
``knowledge.db``, ``datasets/``, ``runs.db``, and ``api-keys.toml``. A setting comes from a
``QUANT_AGENT_OS_*`` environment variable, then ``.env`` in the home, then ``config.toml``. The
working directory is never read, because a client that launches the server chooses it.
"""

from __future__ import annotations

import os
import re
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    InitSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from quant_agent_os.tools.permissions import READ_ONLY, Permission, PermissionPolicy

# What config.toml may set. Permissions are left out on purpose: they come only from the
# environment or the home's .env, so one more file cannot widen what tools may do.
CONFIG_FILE_KEYS = ("log_level", "store", "datasets_dir", "runs_file", "api_keys_file")


class ConfigFileError(ValueError):
    """Raised when config.toml cannot be read or sets something it may not."""


def home_dir() -> Path:
    """Return the platform's home: ``$QUANT_AGENT_OS_HOME``, or ``~/.quant-agent-os``."""
    configured = os.environ.get("QUANT_AGENT_OS_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".quant-agent-os"


def home_env_file() -> Path:
    """Return the one ``.env`` the platform reads, in its home."""
    return home_dir() / ".env"


def read_config_file() -> dict[str, Any]:
    """Return config.toml's settings, or nothing without a file; refuse anything it may not set."""
    path = home_dir() / "config.toml"
    try:
        values = tomllib.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigFileError(f"Cannot read {path}: {error}") from error
    unknown = sorted(set(values) - set(CONFIG_FILE_KEYS))
    if unknown:
        raise ConfigFileError(
            f"{path} sets {', '.join(unknown)}; it may set only {', '.join(CONFIG_FILE_KEYS)}. "
            "Permissions come from QUANT_AGENT_OS_GRANTED_PERMISSIONS."
        )
    return values


class Settings(BaseSettings):
    """Bring-your-own configuration. Any secret here must be a `SecretStr`, kept out of logs."""

    model_config = SettingsConfigDict(env_prefix="QUANT_AGENT_OS_", extra="ignore")

    granted_permissions: frozenset[Permission] = READ_ONLY
    preapproved_permissions: frozenset[Permission] = frozenset()
    log_level: str = "INFO"
    store: str = Field(default_factory=lambda: str(home_dir() / "knowledge.db"))
    datasets_dir: Path = Field(default_factory=lambda: home_dir() / "datasets")
    runs_file: Path = Field(default_factory=lambda: home_dir() / "runs.db")
    api_keys_file: Path = Field(default_factory=lambda: home_dir() / "api-keys.toml")

    @field_validator("datasets_dir", "runs_file", "api_keys_file")
    @classmethod
    def _expand_user(cls, value: Path) -> Path:
        return value.expanduser()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            DotEnvSettingsSource(settings_cls, env_file=home_env_file(), env_file_encoding="utf-8"),
            InitSettingsSource(settings_cls, init_kwargs=read_config_file()),
        )

    def policy(self) -> PermissionPolicy:
        """Return the permission policy these settings configure."""
        return PermissionPolicy(
            granted=self.granted_permissions, preapproved=self.preapproved_permissions
        )

    def public_summary(self) -> dict[str, str]:
        """Return settings that are safe to display; secrets are reported only as set/unset."""
        return {
            "home": str(home_dir()),
            "granted_permissions": ", ".join(sorted(p.value for p in self.granted_permissions)),
            "preapproved_permissions": ", ".join(
                sorted(p.value for p in self.preapproved_permissions)
            )
            or "(none)",
            "log_level": self.log_level,
            "store": re.sub(r"://[^/@]*@", "://***@", self.store),
            "datasets_dir": str(self.datasets_dir),
            "runs_file": str(self.runs_file),
            "api_keys_file": str(self.api_keys_file),
        }


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings."""
    return Settings()
