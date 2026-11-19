import glob
import os

import numpy as np
import pandas as pd

from wrenfield_quant import CONFIG, db

LATEST = {}


def _cds(tenor):
    files = sorted(
        glob.glob(
            os.path.join(
                CONFIG["estate"], "s3/wrenfield-data-lake/curated/cds_daily/dt=*/part-0000.parquet"
            )
        )
    )
    df = pd.read_parquet(files[-1])
    df = df[(df.kind == "single") & (df.tenor == tenor)]
    return df.set_index("name_id").spread_bp


def basis_monitor(threshold=-30):
    """Bonds whose CDS basis is more negative than threshold (bp); prints and remembers them."""
    oas = db.load_px("oas_bp").iloc[-1]
    ttm = db.load_px("years_to_maturity").iloc[-1]
    c3, c5, c10 = _cds("3Y"), _cds("5Y"), _cds("10Y")
    rows = []
    for bond in oas.index:
        issuer = bond.split("_")[0]
        if issuer not in c5.index or np.isnan(oas[bond]):
            continue
        cds = np.interp(ttm[bond], [3, 5, 10], [c3[issuer], c5[issuer], c10[issuer]])
        rows.append((bond, cds - oas[bond]))
    df = pd.DataFrame(rows, columns=["bond", "basis"]).sort_values("basis")
    hits = df[df.basis < threshold]
    for _, r in hits.iterrows():
        print("NEG BASIS", r.bond, round(r.basis, 1))
        LATEST[r.bond] = r.basis
    return hits
