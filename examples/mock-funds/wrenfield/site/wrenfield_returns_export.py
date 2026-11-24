"""Export Wrenfield's books' net daily returns to the CSV the fund's performance report reads.

    WRENFIELD_ESTATE=<dir> python wrenfield_returns_export.py <out.csv>

One column per book with capital (hy_ls, ig_rv, distressed), each its net P&L over its capital.
The overlay book has no capital, so it has no return series here.
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent
if str(SITE) not in sys.path:
    sys.path.insert(0, str(SITE))

from wrenfield_store import WrenfieldDatasetStore  # noqa: E402

BOOKS = ("hy_ls", "ig_rv", "distressed")


def export(out: Path) -> int:
    """Write the books' net returns; return the number of rows written."""
    store = WrenfieldDatasetStore(Path(os.environ["WRENFIELD_ESTATE"]))
    series = store.get("book_returns_net")
    index = series.index or []
    with out.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", *BOOKS])
        for row, day in enumerate(index):
            values = [series.column(book)[row] for book in BOOKS]
            writer.writerow([day, *("" if v != v else repr(v) for v in values)])  # NaN is blank
    return len(index)


if __name__ == "__main__":
    print(f"wrote {export(Path(sys.argv[1]))} rows")
