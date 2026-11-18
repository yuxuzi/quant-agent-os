import math

import compliance
import pandas as pd

_STATE = {"deal": None, "book": None, "loaded": False}
_CACHE = {}


def load_deal(deal, date):
    print("loading", deal, date, "- this takes a while")
    _STATE["book"] = compliance.load_book(deal, date)
    _STATE["deal"] = deal
    _STATE["loaded"] = True


def project_equity_cashflows(cpr=0.15, cdr=0.02, recovery=0.6, months=60, sofr=0.04):
    """Deterministic run-off of the loaded book; slow because it loops loan by loan."""
    if not _STATE["loaded"]:
        raise RuntimeError("call load_deal first")
    key = (_STATE["deal"], cpr, cdr, recovery, months, sofr)
    if key in _CACHE:
        return _CACHE[key]
    book = _STATE["book"]
    out = []
    par = book.par.values.copy()
    for m in range(months):
        interest = 0.0
        for i in range(len(par)):
            interest += par[i] * (sofr + book.spread_bps.values[i] / 1e4) / 12
            prepay = par[i] * (1 - (1 - cpr) ** (1 / 12))
            default = par[i] * (1 - (1 - cdr) ** (1 / 12))
            par[i] = par[i] - prepay - default
        out.append((m + 1, round(interest, 2), round(par.sum(), 2), math.nan))
    result = pd.DataFrame(out, columns=["month", "interest", "par", "equity"])
    _CACHE[key] = result
    return result
