"""Reports for the morning meeting."""

import os

import numpy as np
import pandas as pd

from kestrel_quant import db
from kestrel_quant.config import CONFIG, log


def drawdown_table(returns, top=5):
    """The worst drawdowns of a daily return series: start, trough, recovery, depth, length."""
    wealth = (1 + returns.fillna(0)).cumprod()
    peak = wealth.cummax()
    dd = wealth / peak - 1
    rows = []
    in_dd = False
    start = None
    for date, value in dd.items():
        if value < 0 and not in_dd:
            in_dd, start = True, date
        elif value == 0 and in_dd:
            window = dd.loc[start:date]
            rows.append([start, window.idxmin(), date, window.min(), len(window)])
            in_dd = False
    if in_dd:
        window = dd.loc[start:]
        rows.append([start, window.idxmin(), None, window.min(), len(window)])
    table = pd.DataFrame(rows, columns=["start", "trough", "recovery", "depth", "days"])
    return table.sort_values("depth").head(top).reset_index(drop=True)


def desk_pnl_report(desk, start=None, end=None, save=True):
    """Daily P&L of a desk from end-of-day positions, net of commissions. Writes a CSV."""
    pos = db.query("SELECT date, instrument_id, quantity FROM pos_eod WHERE desk = ?", desk).pivot(
        index="date", columns="instrument_id", values="quantity"
    )
    px = db.load_prices(pos.columns)
    px.index = px.index.strftime("%Y-%m-%d")
    pnl = (pos.shift(1) * px.diff()).sum(axis=1)
    fees = db.query(
        "SELECT trade_date AS date, SUM(commission_usd) AS fees FROM trades "
        "WHERE desk = ? GROUP BY trade_date",
        desk,
    ).set_index("date")["fees"]
    report = pd.DataFrame({"gross_pnl": pnl})
    report["commissions"] = fees.reindex(report.index).fillna(0.0)
    report["net_pnl"] = report.gross_pnl - report.commissions
    report["cum_net_pnl"] = report.net_pnl.cumsum()
    report = report.loc[start:end]
    print(f"{desk}: net P&L {report.net_pnl.sum():,.0f} over {len(report)} days")
    if save:
        out = os.path.join(CONFIG["output_dir"], f"pnl_{desk}.csv")
        report.to_csv(out)
        log("wrote", out)
    return report


def summary(returns):
    ann = CONFIG["annualization"]
    mu = returns.mean() * ann
    vol = returns.std() * np.sqrt(ann)
    return {"ann_return": mu, "ann_vol": vol, "sharpe": mu / vol if vol else np.nan}
