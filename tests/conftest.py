"""Every test gets its own empty home, so none reads or writes the real ~/.quant-agent-os.

The analytics stack is loaded once before the tests, as a server loads it before it serves, so a
test's first metric call is not timed against the import on a busy machine.
"""

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


def pytest_sessionstart(session: pytest.Session) -> None:
    """Load the metrics backend once, as `quant_platform.serve.warm_up` does for a server."""
    try:
        from quant_analytics.computed import warm_up
    except ImportError:  # an install without the analytics extra
        return
    warm_up()
