"""Shared fixtures: the synthetic corpus and a store ingested from it."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from quant_research import ResearchApp, open_research

CORPUS = Path(__file__).parent / "fixtures" / "corpus"


@pytest.fixture
def research() -> Iterator[ResearchApp]:
    """An in-memory store with the fixture corpus ingested."""
    app = open_research(":memory:")
    app.ingest.ingest(CORPUS)
    yield app
    app.close()
