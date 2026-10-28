"""Evidence and provenance records that keep retrieved facts apart from interpretation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SourceRef(BaseModel):
    """Where a fact came from: a stable identifier plus an optional location inside it."""

    source_id: str = Field(min_length=1)
    location: str | None = None
    title: str | None = None


class Evidence(BaseModel):
    """One statement in a report and the sources that support it.

    ``retrieved`` statements quote or paraphrase a source, ``computed`` statements come from a
    deterministic tool, and ``interpretation`` statements are model-generated and must be
    labelled as such.
    """

    kind: Literal["retrieved", "computed", "interpretation"]
    statement: str = Field(min_length=1)
    sources: list[SourceRef] = Field(default_factory=list)
