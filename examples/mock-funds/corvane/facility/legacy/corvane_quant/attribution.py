import config
import pandas as pd

LAST_RESULT = {}  # the report generator reads this after run() is called


def run(port_w, bench_w, ret, start, end):
    """Daily Brinson by sector, effects summed over the days."""
    global LAST_RESULT
    total = {}
    for day in ret.loc[start:end].index:
        for sec in set(config.SECTOR_MAP.values()):
            names = [n for n in ret.columns if config.SECTOR_MAP.get(n) == sec]
            wp = port_w.loc[port_w.index.isin(names)].sum()
            wb = bench_w.loc[bench_w.index.isin(names)].sum()
            rb = ret.loc[day, names].fillna(0).mean()  # equal weight, close enough
            total[sec] = total.get(sec, 0) + (wp - wb) * rb
    LAST_RESULT = total
    for k, v in sorted(total.items()):
        print("%-28s %8.4f" % (k, v))  # noqa: UP031
    return pd.Series(total)
