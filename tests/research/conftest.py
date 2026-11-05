"""Shared fixtures: the synthetic corpus (text plus generated PDFs) and a store built from it."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from pdf_fixtures import TEXT_CORPUS, build_corpus

from quant_research import ResearchApp, open_research

CORPUS = TEXT_CORPUS


@pytest.fixture(scope="session")
def corpus_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The text corpus plus generated PDFs, built once per test session."""
    return build_corpus(tmp_path_factory.mktemp("corpus"))


@pytest.fixture
def research(corpus_dir: Path) -> Iterator[ResearchApp]:
    """An in-memory store with the full fixture corpus ingested."""
    app = open_research(":memory:")
    app.ingest.ingest(corpus_dir)
    yield app
    app.close()
