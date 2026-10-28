"""Runtime settings from `QUANT_AGENT_OS_*` environment variables and an optional .env file."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from quant_agent_os.tools.permissions import READ_ONLY, Permission


class Settings(BaseSettings):
    """Bring-your-own configuration. Secrets are `SecretStr` and never appear in repr or logs."""

    model_config = SettingsConfigDict(
        env_prefix="QUANT_AGENT_OS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    model_provider: Literal["fake"] = "fake"
    model_name: str | None = None
    api_key: SecretStr | None = None

    max_plan_steps: int = Field(default=8, ge=1, le=50)
    granted_permissions: frozenset[Permission] = READ_ONLY
    log_level: str = "INFO"

    def public_summary(self) -> dict[str, str]:
        """Return settings that are safe to display; secrets are reported only as set/unset."""
        return {
            "model_provider": self.model_provider,
            "model_name": self.model_name or "(none)",
            "api_key": "set" if self.api_key else "unset",
            "max_plan_steps": str(self.max_plan_steps),
            "granted_permissions": ", ".join(sorted(p.value for p in self.granted_permissions)),
            "log_level": self.log_level,
        }


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings."""
    return Settings()
