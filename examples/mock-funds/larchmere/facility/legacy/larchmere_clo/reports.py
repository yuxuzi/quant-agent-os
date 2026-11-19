import os

import compliance
import pandas as pd
from config import OUTPUT_DIR, TRUSTEE_TOLERANCE
from db import q


def reconcile(deal, date):
    mine = compliance.calc_oc(deal, date, "oc_e")
    print("mine", mine)
    path = os.path.join(OUTPUT_DIR, f"recon_{deal}_{date}.csv")
    pd.DataFrame({"deal": [deal], "date": [date], "oc_e": [mine]}).to_csv(path)
    print("wrote", path)
    return path


def failing_dates(deal):
    out = []
    for d in compliance.all_dates(deal):
        v = compliance.calc_oc(deal, d, "oc_e")
        trig = q(
            "select oc_trigger from tranche where deal_id = ? and tranche = 'BB'", (deal,)
        ).oc_trigger[0]
        if v < trig - TRUSTEE_TOLERANCE:
            out.append(d)
    return out
