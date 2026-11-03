"""Unit tests for schema-change classification and the tool lock (decision 0015)."""

from typing import Literal

from pydantic import BaseModel, Field

from quant_agent_os.tools import Permission, tool
from quant_agent_os.tools.compatibility import bump, check_lock, classify, lock_entry


class Query(BaseModel):
    text: str
    limit: int = 5


class Hit(BaseModel):
    title: str
    kind: Literal["note", "paper"]


class Hits(BaseModel):
    hits: list[Hit]


@tool(name="search", description="Search.")
def search(params: Query) -> Hits:
    return Hits(hits=[])


BASE = lock_entry(search)


def entry_for(
    input_model: type[BaseModel], output_model: type[BaseModel], **options: object
) -> dict[str, object]:
    def function(params: BaseModel) -> BaseModel:
        raise NotImplementedError

    function.__annotations__ = {"params": input_model, "return": output_model}
    return lock_entry(tool(name="search", description="Search.", **options)(function))  # type: ignore[arg-type]


def test_identical_schemas_are_no_change() -> None:
    assert classify(BASE, lock_entry(search)) is None


def test_a_described_field_is_a_patch() -> None:
    class Described(BaseModel):
        text: str = Field(description="What to look for.")
        limit: int = 5

    assert classify(BASE, entry_for(Described, Hits)) == "patch"


def test_an_optional_input_or_a_new_output_field_is_minor() -> None:
    class MoreOptions(BaseModel):
        text: str
        limit: int = 5
        since: str | None = None

    class RicherHit(BaseModel):
        title: str
        kind: Literal["note", "paper"]
        score: float

    class RicherHits(BaseModel):
        hits: list[RicherHit]

    assert classify(BASE, entry_for(MoreOptions, Hits)) == "minor"
    assert classify(BASE, entry_for(Query, RicherHits)) == "minor"


def test_a_new_output_enum_value_is_minor() -> None:
    class WiderHit(BaseModel):
        title: str
        kind: Literal["note", "paper", "pdf"]

    class WiderHits(BaseModel):
        hits: list[WiderHit]

    assert classify(BASE, entry_for(Query, WiderHits)) == "minor"


def test_breaking_changes_are_major() -> None:
    class NewRequired(BaseModel):
        text: str
        limit: int = 5
        corpus: str

    class Renamed(BaseModel):
        query: str
        limit: int = 5

    class Narrowed(BaseModel):
        text: str
        limit: str = "5"

    class NoKind(BaseModel):
        title: str

    class FewerFields(BaseModel):
        hits: list[NoKind]

    for changed in (
        entry_for(NewRequired, Hits),
        entry_for(Renamed, Hits),
        entry_for(Narrowed, Hits),
    ):
        assert classify(BASE, changed) == "major"
    assert classify(BASE, entry_for(Query, FewerFields)) == "major"
    assert classify(BASE, entry_for(Query, Hits, permissions=[Permission.NETWORK])) == "major"


def test_bump_sizes() -> None:
    assert bump("1.0.0", "2.0.0") == "major"
    assert bump("1.0.0", "1.1.0") == "minor"
    assert bump("1.0.0", "1.0.1") == "patch"
    assert bump("1.2.0", "1.2.0") is None
    assert bump("1.2.0", "1.1.9") is None


def test_the_lock_check_names_every_disagreement() -> None:
    class Renamed(BaseModel):
        query: str

    @tool(name="search", description="Search.", version="1.1.0")
    def search_renamed(params: Renamed) -> Hits:
        return Hits(hits=[])

    @tool(name="search", description="Search.", version="2.0.0")
    def search_v2(params: Renamed) -> Hits:
        return Hits(hits=[])

    assert check_lock({"search": BASE}, [search]) == []
    [too_small] = check_lock({"search": BASE}, [search_renamed])
    assert "major schema change needs a major version bump" in too_small
    [stale] = check_lock({"search": BASE}, [search_v2])
    assert "update the lock" in stale
    assert "is new" in check_lock({}, [search])[0]
    assert "not served" in check_lock({"search": BASE, "gone": BASE}, [search])[0]
