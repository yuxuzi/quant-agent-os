"""Named database connections, described without secrets, and how a spec uses one."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from quant_app_factory.domain.secrets import ENV_NAME
from quant_app_factory.domain.spec import AppSpec

DATABASE_COMPONENT = "data-database"


class Connection(BaseModel):
    """A database connection. Secrets stay in the environment: only variable names are kept."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    kind: Literal["postgres", "sqlite", "duckdb", "snowflake"]
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = Field(default=None, description="Name, or file path for sqlite/duckdb")
    user: str | None = None
    db_schema: str | None = Field(default=None, alias="schema")
    read_only: bool = True
    url_env: str | None = Field(default=None, description="Variable holding the full URL")
    password_env: str | None = Field(default=None, description="Variable holding the password")

    @field_validator("url_env", "password_env")
    @classmethod
    def _variable_name(cls, name: str | None) -> str | None:
        if name is not None and not ENV_NAME.match(name):
            raise ValueError("must be an environment-variable name, such as RESEARCH_PG_PASSWORD")
        return name


def apply_connection(spec: AppSpec, connection: Connection) -> AppSpec:
    """Return ``spec`` using ``connection``: the database component, its answers, its settings.

    Values the spec already sets win, so a spec can override one detail of a saved connection.
    """
    answers = {
        "db_kind": connection.kind,
        "db_host": connection.host,
        "db_port": None if connection.port is None else str(connection.port),
        "db_name": connection.database,
        "db_user": connection.user,
        "db_schema": connection.db_schema,
        "db_read_only": "yes" if connection.read_only else "no",
    }
    settings = {
        "DATABASE_URL": None if connection.url_env is None else f"env:{connection.url_env}",
        "DATABASE_PASSWORD": (
            None if connection.password_env is None else f"env:{connection.password_env}"
        ),
    }
    components = list(spec.components)
    if DATABASE_COMPONENT not in components:
        components.append(DATABASE_COMPONENT)
    return spec.model_copy(
        update={
            "components": components,
            "answers": {
                **{name: value for name, value in answers.items() if value is not None},
                **spec.answers,
            },
            "settings": {
                **{name: value for name, value in settings.items() if value is not None},
                **spec.settings,
            },
        }
    )
