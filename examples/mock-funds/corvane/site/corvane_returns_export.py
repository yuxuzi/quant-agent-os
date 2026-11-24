"""Export Corvane's composites' net daily returns, and the US benchmark they are measured against,
to the CSV the fund's performance report reads.

    CORVANE_SQL_URL=estate://<dir> python corvane_returns_export.py <out.csv>

One column per composite (LCC, GQ), net of the management fee as the books hold them, and the
benchmark column (BM_US300, the LCC composite's). The report takes one benchmark per file, so GQ's
benchmark (BM_GLOBAL200) is not in it.
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent
if str(SITE) not in sys.path:
    sys.path.insert(0, str(SITE))

from corvane_store import CorvaneDatasetStore, connect  # noqa: E402

COMPOSITES = {"LCC": "perf_lcc", "GQ": "perf_gq"}
BENCHMARK = "BM_US300"


def export(out: Path) -> int:
    """Write the composites' net returns and the benchmark; return the number of rows written."""
    store = CorvaneDatasetStore(connect(dict(os.environ)))
    net = {name: dict(zip(s.index or [], s.column("net"), strict=True)) for name, s in
           ((n, store.get(d)) for n, d in COMPOSITES.items())}  # fmt: skip
    bench = store.get("bench_ret")
    benchmark = dict(zip(bench.index or [], bench.column(BENCHMARK), strict=True))
    days = sorted(net["LCC"])
    with out.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", *COMPOSITES, BENCHMARK])
        for day in days:
            values = [net[name].get(day, float("nan")) for name in COMPOSITES] + [benchmark[day]]
            writer.writerow([day, *("" if v != v else repr(v) for v in values)])  # NaN is blank
    return len(days)


if __name__ == "__main__":
    print(f"wrote {export(Path(sys.argv[1]))} rows")
