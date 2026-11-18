import glob
import os
import sqlite3

import pandas as pd

from wrenfield_quant import CONFIG

_conn = None
_px_cache = {}


def connect():
    global _conn
    if _conn is None:
        path = os.path.join(CONFIG["estate"], "rds", "wrenfield_oms.sqlite")
        _conn = sqlite3.connect(path)
        if CONFIG["verbose"]:
            print("connected to", path)
    return _conn


def close():
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None
    _px_cache.clear()


def load_px(column="oas_bp"):
    # the lake, one parquet file per day; we concat them every time a new column is asked for
    if column in _px_cache:
        return _px_cache[column]
    files = sorted(
        glob.glob(
            os.path.join(
                CONFIG["estate"],
                "s3/wrenfield-data-lake/curated/bond_px_daily/dt=*/part-0000.parquet",
            )
        )
    )
    frames = []
    for f in files:
        df = pd.read_parquet(f, columns=["bond_id", column])
        df["date"] = f.split("dt=")[1].split("/")[0]
        frames.append(df)
    wide = pd.concat(frames).pivot(index="date", columns="bond_id", values=column)
    _px_cache[column] = wide
    return wide


def load_positions(book=None):
    q = "select date, book, bond_id, face, mv_usd from pos_eod"
    if book:
        q += f" where book = '{book}'"
    return pd.read_sql_query(q, connect())
