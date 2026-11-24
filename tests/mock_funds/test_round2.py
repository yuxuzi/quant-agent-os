"""Round 2 measurements that need no server: readiness with a source gone, and the deal list.

Each test pins what round 2 measured, so a fix shows up as a failing test to update on purpose.
"""

from __future__ import annotations

import sys
from pathlib import Path

from quant_agent_os.data.datasets import readiness_probe

ROOT = Path(__file__).resolve().parents[2]
SITES = (
    "examples/pilot-fund/site",
    "examples/mock-funds/larchmere/site",
    "examples/mock-funds/corvane/site",
)
for site in SITES:
    if str(ROOT / site) not in sys.path:
        sys.path.insert(0, str(ROOT / site))

from corvane_store import CorvaneDatasetStore  # noqa: E402
from larchmere_store import DATASETS, LarchmereDatasetStore  # noqa: E402
from warehouse_store import WarehouseDatasetStore  # noqa: E402


class _DeadConnection:
    def fetch(self, sql: str, params: object = ()) -> list[tuple[object, ...]]:
        raise OSError("warehouse unreachable")


def test_r2_kestrel_readiness_fails_when_its_warehouse_file_is_gone(tmp_path: Path) -> None:
    """R2-K2 (measured): the warehouse's `ids()` reads the file, so `/readyz` fails."""
    store = WarehouseDatasetStore(tmp_path / "missing.db")
    try:
        readiness_probe(store)()
    except Exception:
        return
    raise AssertionError("readiness passed with the warehouse file missing")


def test_r2_larchmere_readiness_still_passes_with_the_sql_source_gone(tmp_path: Path) -> None:
    """R2-L1 (measured): round 1's R1-L12 is open. `ids()` is static, so readiness passes."""
    store = LarchmereDatasetStore(tmp_path / "missing.db", tmp_path / "catalog")
    readiness_probe(store)()


def test_r2_corvane_readiness_fails_when_its_warehouse_is_unreachable() -> None:
    """R2-C1 (measured): the TTL'd `ids()` reads the warehouse, so `/readyz` fails when it
    cannot."""
    store = CorvaneDatasetStore(_DeadConnection(), ttl_seconds=0)
    try:
        readiness_probe(store)()
    except OSError:
        return
    raise AssertionError("readiness passed with the warehouse unreachable")


def test_r2_larchmere_deal_list_is_fixed_at_import() -> None:
    """R2-L2 (inferred from code, pinned here): `DATASETS = _entries()` runs at import, so a new
    deal needs an edit and a restart; no reload reads a file."""
    assert isinstance(DATASETS, dict)
    assert not any(name.startswith("new_deal") for name in DATASETS)
