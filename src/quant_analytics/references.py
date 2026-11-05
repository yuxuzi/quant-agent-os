"""A reference to a dataset column, importable without the analytics extra's numeric libraries."""

from pydantic import BaseModel, ConfigDict, Field


class ColumnRef(BaseModel):
    """One column of a dataset."""

    model_config = ConfigDict(extra="forbid")

    dataset: str = Field(min_length=1)
    column: str = Field(min_length=1)
