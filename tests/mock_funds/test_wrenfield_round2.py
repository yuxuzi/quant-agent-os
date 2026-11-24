"""Round 2: Wrenfield's carried-forward warning on a closure frame (R2-W2, fixed)."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "examples" / "mock-funds" / "wrenfield" / "site"
if str(SITE) not in sys.path:
    sys.path.insert(0, str(SITE))

from wrenfield_credit import carried_warnings  # noqa: E402


def _closure_frame() -> pd.DataFrame:
    """Five weekdays of bond returns. 2025-10-13 is a closure: every bond carried, so zero.

    BOND_A has three zero returns in a row on the closure's run; BOND_B moves every day.
    """
    days = pd.bdate_range("2025-10-08", periods=5)
    return pd.DataFrame(
        {
            "BOND_A": [0.002, 0.0, 0.0, 0.0, 0.001],
            "BOND_B": [0.003, 0.0, -0.001, 0.002, 0.004],
            "BOND_C": [0.001, 0.0, 0.0, 0.001, 0.002],
        },
        index=days.strftime("%Y-%m-%d"),
    )


def test_a_closure_with_carried_prices_names_the_day_and_the_bonds() -> None:
    frame = _closure_frame()
    # A closure needs every bond to be zero on that weekday; make the closure day explicit.
    frame.loc["2025-10-09"] = 0.0
    warnings = carried_warnings(frame, "2025-10-10")
    joined = " ".join(warnings)
    assert "2025-10-09" in joined and "carried prices" in joined
    assert "BOND_A" in joined


def test_no_closure_and_no_runs_gives_no_warning() -> None:
    frame = _closure_frame().iloc[[0, 2, 4]]
    assert carried_warnings(frame.drop(columns=["BOND_A"]), "2025-10-12") == []
