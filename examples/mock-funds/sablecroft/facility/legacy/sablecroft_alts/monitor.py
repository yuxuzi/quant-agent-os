"""The NAV monitor: parses every administrator statement once, then answers from memory."""

import glob
import time

import pandas as pd

from sablecroft_alts.config import CONFIG, log

_STATEMENTS = None  # module-level cache, filled by refresh()
_LOADED_AT = None


def refresh(root=None):
    """Re-read all raw statements. Slow: it parses every file under raw/admin-nav."""
    global _STATEMENTS, _LOADED_AT
    root = root or CONFIG["lake"].replace("/uc/sablecroft", "/raw")
    frames = [pd.read_csv(p) for p in glob.glob(root + "/admin-nav/*/*/nav-statement.csv")]
    time.sleep(CONFIG["refresh_delay_s"])
    _STATEMENTS = pd.concat(frames, ignore_index=True)
    _LOADED_AT = time.time()
    log("loaded", len(_STATEMENTS), "statement rows")
    return len(_STATEMENTS)


def latest_status(manager_id):
    """The latest statement for a manager: its period, whether estimate or final, and the return."""
    if _STATEMENTS is None:
        refresh()
    rows = _STATEMENTS[_STATEMENTS.manager_id == manager_id].sort_values("period_end")
    if rows.empty:
        return None
    last = rows.iloc[-1]
    return {"period_end": last.period_end, "status": last.status, "return": float(last.mtd_return)}


def stale(days=45):
    """Managers whose latest statement is an estimate."""
    if _STATEMENTS is None:
        refresh()
    last = _STATEMENTS.sort_values("period_end").groupby("manager_id").tail(1)
    return list(last[last.status == "estimate"].manager_id)
