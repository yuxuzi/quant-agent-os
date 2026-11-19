"""Contract test for the drafted tools, written by `qaos legacy wrap`.

Run it, fix what fails, and run it again:

    uv run pytest test_tools.py

conftest.py builds a throwaway copy of the fund's warehouse and points KPC_WAREHOUSE at
it, and the tools read it through the fund's own DatasetStore, as the server does.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import server
import uvicorn
from warehouse_store import WarehouseDatasetStore

from quant_agent_os.testing import check_app

HERE = Path(__file__).parent
DRAFTED = ["facades.py", "server.py", "stubs.py", "service/app.py"]
STORE = WarehouseDatasetStore(Path(os.environ["KPC_WAREHOUSE"]))
EXAMPLES: dict[str, dict[str, Any]] = {
    "kpc_backtest": {
        "weights": {"dataset": "weights_macro"},
        "returns": {"dataset": "px_returns"},
        "cost_bps": 2.0,
    },
    "kpc_drawdown_table": {
        "returns": {"dataset": "desk_returns_macro", "column": "gross_return"},
    },
    "kpc_desk_pnl": {"desk": "macro", "start": "2025-01-02"},
    "kpc_summary": {
        "returns": {"dataset": "desk_returns_eq_ls", "column": "gross_return"},
    },
    "risk_desks": {},
    "risk_exposure": {"desk": "macro"},
    "risk_desk_var": {"desk": "macro", "method": "factor"},
    "risk_stress": {"desk": "eq_ls", "scenario": "equity_crash_2008"},
    "risk_stress_all": {"desk": "commod"},
    "risk_factor_decomposition": {"desk": "macro"},
    "kpc_momentum_signal": {"prices": {"dataset": "px_close"}},
    "kpc_carry_signal": {"asset_class": "fx", "as_of": "2025-06-30"},
    "kpc_value_zscore": {"values": {"dataset": "signals_value"}},
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
    for app in server.build_apps(service_url):
        examples = {name: EXAMPLES[name] for name in app.names}
        check_app(app, examples, datasets=STORE)


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
