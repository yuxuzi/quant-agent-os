from config import CCC_LIMIT, RF, SENIOR
from db import q

last_result = {}


def load_book(deal, date):
    h = q(
        "select h.loan_id, h.par, h.market_price, r.rating, l.spread_bps, l.maturity_date "
        "from holdings h join loan l on l.loan_id = h.loan_id "
        "join obligor o on o.obligor_id = l.obligor_id "
        "join rating_history r on r.obligor_id = o.obligor_id "
        "where h.deal_id = ? and h.report_date = ? and r.effective_date = "
        "(select max(effective_date) from rating_history x where x.obligor_id = o.obligor_id "
        "and x.effective_date <= ?)",
        (deal, date, date),
    )
    h["rf"] = h.rating.map(RF)
    return h


def calc_warf(deal, date):
    book = load_book(deal, date)
    perf = book[book.rf < 10000]
    warf = (perf.par * perf.rf).sum() / perf.par.sum()
    print(deal, date, "WARF", round(warf, 1))
    last_result["warf"] = warf
    return warf


def calc_oc(deal, date, klass="oc_e"):
    book = load_book(deal, date)
    bal = (
        q(
            "select tranche, balance from tranche_balance where deal_id = ? and report_date = ?",
            (deal, date),
        )
        .set_index("tranche")
        .balance
    )
    cash = q(
        "select principal_cash from cash_balance where deal_id = ? and report_date = ?",
        (deal, date),
    ).principal_cash[0]
    perf = book[book.rf < 10000]
    ccc = perf[perf.rf >= 4770].sort_values("market_price")
    excess = max(ccc.par.sum() - CCC_LIMIT * perf.par.sum(), 0)
    hc = 0
    for _, r in ccc.iterrows():
        take = min(r.par, excess)
        hc += take * (1 - r.market_price / 100)
        excess -= take
    dflt = book[book.rf >= 10000]
    adj = perf.par.sum() - hc + (dflt.par * dflt.market_price / 100).sum() + cash
    oc = adj / bal[SENIOR[klass]].sum()
    print("OC", klass, round(oc, 4))
    last_result["oc"] = oc
    return oc


def all_dates(deal):
    return q(
        "select distinct report_date from holdings where deal_id = ? order by 1", (deal,)
    ).report_date.tolist()
