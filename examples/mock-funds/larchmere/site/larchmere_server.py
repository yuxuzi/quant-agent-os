"""Larchmere Loan Partners' own server: the CLO tools beside the platform's toolsets.

Decisions 0020 and 0024.

Registration is explicit: this file names every tool. Keys, sign-in, the run store, permissions, and
the access file come from the usual settings (see `deploy/larchmere.env`). The data is read through
`LarchmereDatasetStore`: Azure SQL for the deal model and the lake for prices and cash flows.

Usage (the paths are the Container App's mounts; locally, see tests/mock_funds):
  LARCHMERE_SQL_CONNECTION=sql/clo.db LARCHMERE_LAKE_CATALOG=lake/catalog.json \\
    python larchmere_server.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
from larchmere_clo import (
    LIMIT_TESTS,
    ComplianceReport,
    EquityReturns,
    TestChange,
    WhatIfReport,
    attach_terms,
    book_at,
    compute_tests,
    equity_returns,
    judge,
    pick_date,
    sofr_on,
    years_to_epoch,
)
from larchmere_store import LAKE_ENV, SQL_ENV, LarchmereDatasetStore

from quant_agent_os.api.server import DEFAULT_HOST
from quant_agent_os.data import DatasetStore
from quant_agent_os.registration import FirmApp, Frame
from quant_agent_os.tools import AccessPolicy, FileAccessPolicy, PermissionPolicy
from quant_platform import Catalog, compose, serve_apps

CONVENTION = (
    "Month-end trustee dates; OC = adjusted par / class and senior balance, with CCC excess over "
    "7.5% at market and defaulted loans at market; IC at SOFR + spread, fees excluded; "
    "WARF by Moody's factors; WAS in bps with floors; WAL in years (actual/365.25); "
    "diversity simplified"
)
EQUITY_CONVENTION = (
    "Actual/365 XIRR of invested (first NAV), distributions on payment dates, and terminal NAV; "
    "cash-on-cash is distributions / invested / years"
)

clo = FirmApp(
    "clo",
    instructions="clo_* tools run Larchmere's own CLO compliance arithmetic. Prefer them to the "
    "platform's generic analytics for OC, IC, WARF, WAS, and diversity. as_of is a trustee report "
    "date (a month-end) from list_datasets' par_<deal> index. Report numbers as computed.",
)


def _structure(structure: pd.DataFrame, stamp: pd.Timestamp) -> tuple[pd.Series, float]:
    row = structure.loc[stamp]
    return row, float(row["principal_cash"])


def _prepared(
    par: pd.DataFrame, price: pd.DataFrame, rating_factor: pd.DataFrame, static: pd.DataFrame,
    structure: pd.DataFrame, sofr: pd.DataFrame, as_of: str,
) -> tuple[pd.Timestamp, pd.DataFrame, pd.Series, float, float]:  # fmt: skip
    stamp = pick_date(par.index, as_of, "the par dataset")
    for name, frame in (
        ("price", price),
        ("rating_factor", rating_factor),
        ("structure", structure),
    ):
        pick_date(frame.index, as_of, f"the {name} dataset")
    book = book_at(par, price, rating_factor, static, stamp)
    row, cash = _structure(structure, stamp)
    return stamp, book, row, cash, sofr_on(sofr, stamp)


def clo_compliance_tests(
    par: pd.DataFrame, price: pd.DataFrame, rating_factor: pd.DataFrame, static: pd.DataFrame,
    structure: pd.DataFrame, sofr: pd.DataFrame, as_of: str,
) -> ComplianceReport:  # fmt: skip
    """Run every indenture test (OC, IC, CCC, WARF, WAS, WAL, diversity, limits) for one deal.

    Args:
        par: The deal's par by loan and report date (par_<deal>).
        price: The deal's trustee prices (price_<deal>).
        rating_factor: Moody's rating factors for every loan (rf_all).
        static: Loan terms (loan_static).
        structure: The deal's tranche balances, triggers, limits, and cash (structure_<deal>).
        sofr: SOFR in percent (sofr).
        as_of: A trustee report date, YYYY-MM-DD.
    """
    stamp, book, row, cash, rate = _prepared(
        par, price, rating_factor, static, structure, sofr, as_of
    )
    values = compute_tests(book, row, rate, cash)
    results = judge(values, row)
    warnings = []
    if book["price"].isna().any():
        warnings.append("Some held loans have no price; they are carried at NaN.")
    return ComplianceReport(
        as_of=stamp.date().isoformat(), tests=results,
        failed=[r.name for r in results if not r.passed], adjusted_par=values["adjusted_par"],
        ccc_haircut=values["ccc_haircut"], performing_par=values["performing_par"],
        sofr_pct=rate, values={r.name: r.value for r in results}, warnings=warnings,
    )  # fmt: skip


def clo_what_if(
    par: pd.DataFrame, price: pd.DataFrame, rating_factor: pd.DataFrame, static: pd.DataFrame,
    structure: pd.DataFrame, sofr: pd.DataFrame, as_of: str, loan_id: str, par_change: float,
    trade_price: float,
) -> WhatIfReport:  # fmt: skip
    """Pre-trade check: each test before and after buying (positive) or selling (negative) a loan.

    Args:
        par: The deal's par by loan and report date (par_<deal>).
        price: The deal's trustee prices (price_<deal>).
        rating_factor: Moody's rating factors for every loan (rf_all).
        static: Loan terms (loan_static).
        structure: The deal's tranche balances, triggers, limits, and cash (structure_<deal>).
        sofr: SOFR in percent (sofr).
        as_of: A trustee report date, YYYY-MM-DD.
        loan_id: The loan, held or not, such as LN0042.
        par_change: Par to buy (positive) or sell (negative), in USD.
        trade_price: The trade price in points of par.
    """
    stamp, book, row, cash, rate = _prepared(
        par, price, rating_factor, static, structure, sofr, as_of
    )
    if loan_id not in static.index:
        raise ValueError(f"No loan {loan_id}; loan ids look like LN0042.")
    held = float(book["par"].get(loan_id, 0.0))
    if held + par_change < 0:
        raise ValueError(
            f"Cannot sell {-par_change:,.0f} of {loan_id}: the deal holds {held:,.0f}."
        )
    after = book.copy()
    if loan_id in after.index:
        after.loc[loan_id, "par"] = held + par_change
        after.loc[loan_id, "price"] = trade_price
    else:
        added = pd.DataFrame(
            {
                "par": [par_change],
                "price": [trade_price],
                "rf": [float(rating_factor.loc[stamp, loan_id])],
            },
            index=[loan_id],
        )
        after = pd.concat([after, attach_terms(added, static, stamp)])
    after = after[after["par"] > 0]
    cash_after = cash - par_change * trade_price / 100
    warnings = (
        [f"The trade needs {-cash_after:,.0f} more principal cash than the deal has."]
        if cash_after < 0
        else []
    )
    before_values = compute_tests(book, row, rate, cash)
    after_values = compute_tests(after, row, rate, cash_after)
    before_results = {r.name: r for r in judge(before_values, row)}
    after_results = {r.name: r for r in judge(after_values, row)}
    changes = [
        TestChange(
            name, before_values[name], after_values[name], after_values[name] - before_values[name],
            before_results[name].passed, after_results[name].passed,
        )
        for name in before_results
    ]  # fmt: skip
    return WhatIfReport(
        as_of=stamp.date().isoformat(), loan_id=loan_id, par_change=par_change, price=trade_price,
        cash_after=cash_after,
        changes=changes,
        newly_failing=[c.name for c in changes if c.passes_before and not c.passes_after],
        newly_passing=[c.name for c in changes if not c.passes_before and c.passes_after],
        warnings=warnings,
    )  # fmt: skip


def clo_equity_returns(cashflows: pd.DataFrame) -> EquityReturns:
    """Cash-on-cash and IRR of a deal's equity from its dated invested, distribution, and NAV flows.

    Args:
        cashflows: The equity's flows (equity_cf_<deal>): invested, distribution, terminal_nav.
    """
    return equity_returns(cashflows)


clo.expose(
    clo_compliance_tests,
    convention=CONVENTION,
    par=Frame,
    price=Frame,
    rating_factor=Frame,
    static=Frame,
    structure=Frame,
    sofr=Frame,
)
clo.expose(
    clo_what_if,
    convention=CONVENTION + "; a buy spends par x price / 100 of principal cash, a sell adds it",
    par=Frame,
    price=Frame,
    rating_factor=Frame,
    static=Frame,
    structure=Frame,
    sofr=Frame,
)
clo.expose(clo_equity_returns, cashflows=Frame, convention=EQUITY_CONVENTION)


def build_catalog(
    store: DatasetStore, policy: PermissionPolicy, access: AccessPolicy | None = None
) -> Catalog:
    """Return what Larchmere serves: the platform's analytics and the CLO tools, over the store."""
    return compose(policy=policy, datasets=store, access=access, apps=[clo])


def serve() -> None:
    """Serve until interrupted, on ``LARCHMERE_HOST``:``LARCHMERE_PORT`` (loopback, 8000)."""
    from quant_agent_os.config import get_settings

    settings = get_settings()
    access = FileAccessPolicy(settings.access_file) if settings.access_file else None
    store = LarchmereDatasetStore(Path(os.environ[SQL_ENV]), Path(os.environ[LAKE_ENV]))
    serve_apps(
        [clo], datasets=store, access=access,
        host=os.environ.get("LARCHMERE_HOST", DEFAULT_HOST),
        port=int(os.environ.get("LARCHMERE_PORT", "8000")),
    )  # fmt: skip


def main(argv: list[str]) -> int:
    """Serve; there are no arguments."""
    if argv:
        print(__doc__, file=sys.stderr)
        return 2
    serve()
    return 0


__all__ = ["LIMIT_TESTS", "build_catalog", "clo", "years_to_epoch"]

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
