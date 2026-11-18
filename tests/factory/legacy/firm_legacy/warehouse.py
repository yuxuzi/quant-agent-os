"""Needs the fund's database to import, so a scan must never import it."""

import os
import sqlite3

raise RuntimeError("firm_legacy.warehouse needs the fund's database; a scan must not import it")

DB = sqlite3.connect(os.environ["FUND_DB"])


def daily_prices(ticker: str) -> list[float]:
    """Daily closing prices of one ticker."""
    rows = DB.execute("SELECT close FROM prices WHERE ticker = ?", (ticker,)).fetchall()
    return [row[0] for row in rows]
