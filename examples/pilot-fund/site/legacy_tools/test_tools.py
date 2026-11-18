"""Contract test for the drafted tools, written by `qaos legacy wrap`.

Run it, fix what fails, and run it again:

    uv run pytest test_tools.py

The sample data is made up; replace it with a small sample of the firm's own.
"""

from __future__ import annotations

import datetime
import math
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from quant_agent_os.data import Dataset, UnknownDatasetError, parse_dataset
from quant_agent_os.testing import check_app

import server

HERE = Path(__file__).parent
SCRATCH = Path(tempfile.mkdtemp(prefix="legacy-tools-"))  # where writing tools write
DRAFTED = ["facades.py", "server.py", "stubs.py", "service/app.py"]
ROWS = 260


def _dates(count: int) -> list[str]:
    day, dates = datetime.date(2024, 1, 2), []
    while len(dates) < count:
        if day.weekday() < 5:
            dates.append(day.isoformat())
        day += datetime.timedelta(days=1)
    return dates


def _sample(dataset_id: str, columns: dict[str, str]) -> Dataset:
    """Made-up business-day data: "price" columns wander near 100, "return" columns near 0."""
    lines = ["date," + ",".join(columns)]
    for row, date in enumerate(_dates(ROWS)):
        cells = []
        for number, kind in enumerate(columns.values(), start=1):
            wave = math.sin(row * 0.37 * number) + math.cos(row * 0.11 + number)
            value = 100 + 5 * wave + 0.05 * row if kind == "price" else 0.01 * wave
            cells.append(f"{value:.6f}")
        lines.append(f"{date}," + ",".join(cells))
    return parse_dataset(dataset_id, "\n".join(lines).encode())


class SampleStore:
    """Datasets held in memory, as a firm's own DatasetStore would serve them."""

    def __init__(self, *datasets: Dataset) -> None:
        self.datasets = {dataset.id: dataset for dataset in datasets}

    def get(self, dataset_id: str) -> Dataset:
        if dataset_id not in self.datasets:
            raise UnknownDatasetError(f"No dataset '{dataset_id}'.")
        return self.datasets[dataset_id]

    def ids(self) -> list[str]:
        return sorted(self.datasets)


STORE = SampleStore(
    _sample("sample", {"returns": "return"}),
    _sample("sample_weights", {"asset_a": "return", "asset_b": "return", "asset_c": "return"}),
    _sample("sample_returns", {"asset_a": "return", "asset_b": "return", "asset_c": "return"}),
)
EXAMPLES: dict[str, dict[str, Any]] = {
    "kpc_backtest": {
        "weights": {"dataset": "sample_weights"},
        "returns": {"dataset": "sample_returns"},
    },
    "kpc_drawdown_table": {
        "returns": {"dataset": "sample", "column": "returns"},
    },
    "kpc_desk_pnl": {
        "desk": 1.0,  # TODO: a realistic value
    },
    "kpc_summary": {
        "returns": {"dataset": "sample", "column": "returns"},
    },
    "risk_desks": {},
    "risk_exposure": {
        "desk": 1.0,  # TODO: a realistic value
    },
    "risk_desk_var": {
        "desk": 1.0,  # TODO: a realistic value
    },
    "risk_stress": {
        "desk": 1.0,  # TODO: a realistic value
        "scenario": 1.0,  # TODO: a realistic value
    },
    "risk_stress_all": {
        "desk": 1.0,  # TODO: a realistic value
    },
    "risk_factor_decomposition": {
        "desk": 1.0,  # TODO: a realistic value
    },
    "kpc_momentum_signal": {},
    "kpc_carry_signal": {},
    "kpc_value_zscore": {},
}


@pytest.fixture(scope="module")
def service_url() -> Any:
    """Serve service/app.py on a free loopback port for the test, then stop it."""
    from service.app import app

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    running = uvicorn.Server(config)
    thread = threading.Thread(target=running.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not running.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("service/app.py did not start; run it with uvicorn to see why.")
        time.sleep(0.05)
    port = running.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    running.should_exit = True
    thread.join(timeout=10)


def test_every_tool_meets_the_contract(service_url: str) -> None:
    """check_app calls each tool with its example, directly and over MCP, and checks the output."""
    check_app(server.build_app(service_url), EXAMPLES, datasets=STORE)


def test_no_decision_is_left_open() -> None:
    """The draft marks what it could not decide; decide each with the developer, then delete it."""
    marker = "TO" + "DO"
    open_items = [
        f"{name}:{number}: {line.strip()}"
        for name in DRAFTED
        if (HERE / name).exists()
        for number, line in enumerate((HERE / name).read_text("utf-8").splitlines(), 1)
        if marker in line
    ]
    assert not open_items, f"{len(open_items)} open decisions:\n" + "\n".join(open_items)
