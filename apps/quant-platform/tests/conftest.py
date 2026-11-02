"""Shared fixtures: the research fixture corpus, ingested once, and a small returns dataset."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from quant_agent_os.data import DirectoryDatasetStore
from quant_research import ResearchApp, open_research

ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "packages" / "quant-research" / "tests" / "fixtures" / "corpus"
FUND = [0.012, -0.004, 0.021, -0.015, 0.008, 0.017, -0.006, 0.011, 0.002, -0.009, 0.014, 0.005]


@pytest.fixture(scope="module")
def research() -> Iterator[ResearchApp]:
    app = open_research(":memory:")
    app.ingest.ingest(CORPUS)
    yield app
    app.close()


@pytest.fixture
def datasets(tmp_path: Path) -> DirectoryDatasetStore:
    source = tmp_path / "returns.csv"
    source.write_text(
        "month,fund\n" + "".join(f"2024-{m + 1:02d},{v}\n" for m, v in enumerate(FUND))
    )
    store = DirectoryDatasetStore(tmp_path / "datasets")
    store.put("returns", source)
    return store
