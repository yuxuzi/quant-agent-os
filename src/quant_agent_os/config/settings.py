"""Runtime settings from `QUANT_AGENT_OS_*` environment variables and an optional .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from quant_agent_os.tools.permissions import READ_ONLY, Permission, PermissionPolicy


class Settings(BaseSettings):
    """Bring-your-own configuration. Any secret here must be a `SecretStr`, kept out of logs."""

    model_config = SettingsConfigDict(
        env_prefix="QUANT_AGENT_OS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    granted_permissions: frozenset[Permission] = READ_ONLY
    preapproved_permissions: frozenset[Permission] = frozenset()
    log_level: str = "INFO"
    api_keys_file: Path = Path.home() / ".quant-agent-os" / "api-keys.toml"
    datasets_dir: Path = Path.home() / ".quant-agent-os" / "datasets"

    def policy(self) -> PermissionPolicy:
        """Return the permission policy these settings configure."""
        return PermissionPolicy(
            granted=self.granted_permissions, preapproved=self.preapproved_permissions
        )

    def public_summary(self) -> dict[str, str]:
        """Return settings that are safe to display; secrets are reported only as set/unset."""
        return {
            "granted_permissions": ", ".join(sorted(p.value for p in self.granted_permissions)),
            "preapproved_permissions": ", ".join(
                sorted(p.value for p in self.preapproved_permissions)
            )
            or "(none)",
            "log_level": self.log_level,
            "api_keys_file": str(self.api_keys_file),
            "datasets_dir": str(self.datasets_dir),
        }


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings."""
    return Settings()
