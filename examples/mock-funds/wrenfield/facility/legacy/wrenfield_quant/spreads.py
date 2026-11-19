import pandas as pd

from wrenfield_quant import CONFIG, db

CAPITAL = {"hy_ls": 400e6, "ig_rv": 600e6, "distressed": 200e6}


def dts_by_book(date=None):
    """DTS per book as of date: sum of weight * spread duration * oas."""
    pos = db.load_positions()
    sd = db.load_px("spread_duration")
    oas = db.load_px("oas_bp")
    date = date or pos.date.max()
    out = {}
    for book, cap in CAPITAL.items():
        p = pos[(pos.date == date) & (pos.book == book)].set_index("bond_id")
        w = p.mv_usd / cap
        dts = (w * sd.loc[date, p.index] * oas.loc[date, p.index]).sum()
        out[book] = dts
        if CONFIG["verbose"]:
            print(book, "DTS", round(dts, 1))
    return pd.Series(out)


def cs01_ladder(book, date=None):
    pos = db.load_positions(book)
    date = date or pos.date.max()
    p = pos[pos.date == date].set_index("bond_id")
    sd = db.load_px("spread_duration").loc[date, p.index]
    ttm = db.load_px("years_to_maturity").loc[date, p.index]
    cs01 = -p.mv_usd * sd / 1e4
    bucket = pd.cut(ttm, [0, 2, 5, 10, 100], labels=["0-2y", "2-5y", "5-10y", "10y+"])
    ladder = cs01.groupby(bucket).sum()
    print(ladder)
    ladder.to_csv(CONFIG["output_dir"] + f"/cs01_{book}.csv")
    return ladder
