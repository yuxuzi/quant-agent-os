"""Every test gets its own empty home, so none reads or writes the real ~/.quant-agent-os."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from quant_agent_os.config import get_settings


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("QUANT_AGENT_OS_HOME", str(tmp_path / "qaos-home"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
