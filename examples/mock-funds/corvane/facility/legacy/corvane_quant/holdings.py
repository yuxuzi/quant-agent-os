import glob

import pandas as pd

import config


def load_prices():
    # reads every daily partition once, then keeps it for the life of the process
    if config.PRICES is None:
        print("loading prices from", config.LAKE)
        frames = []
        for path in sorted(glob.glob(config.LAKE + "/prices_daily/dt=*/part-0000.parquet")):
            df = pd.read_parquet(path)
            df["date"] = path.split("dt=")[1][:10]
            frames.append(df)
        config.PRICES = pd.concat(frames)
    return config.PRICES


def load_master(path):
    df = pd.read_parquet(path)
    config.SECTOR_MAP = dict(zip(df.SECURITY_ID, df.SECTOR))
    return df


def weights_on(holdings, account, date):
    px = load_prices()
    h = holdings[(holdings.ACCOUNT_ID == account) & (holdings.AS_OF_DATE == date)]
    p = px[px.date == date].set_index("security_id").close_local
    h = h.assign(value=[s * p.get(i, 1.0) for s, i in zip(h.SHARES, h.SECURITY_ID)])
    h["w"] = h.value / h.value.sum()
    print(account, date, "positions:", len(h))
    return h.set_index("SECURITY_ID").w
