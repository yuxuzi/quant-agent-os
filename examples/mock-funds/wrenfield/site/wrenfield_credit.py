"""Wrenfield Credit Opportunities' own credit analytics: plain functions that know nothing of the
platform, registered as tools by `wrenfield_server.py` (decision 0024).

Conventions common to every function here:
- Exposures are USD market values (dirty), signed: shorts are negative. Sensitivities are
  profit and loss in USD for the move named, so a long bond has a negative CS01.
- ``static`` is the bond reference table (index: bond id): ``sector_code``, ``seniority_code``,
  ``currency_code`` (1 USD, 2 EUR), ``coupon_pct``, ``recovery_assumption``, ``book_code``.
  Ratings are scores, 1 (AAA) to 22 (D). An issuer is the part of a bond id before ``_``.
- A bond with no spread or duration (a defaulted one) is left out of spread measures, and the
  result says how many.
- CDS risky annuity: (1 - exp(-(r + h) T)) / (r + h), r = 4%, h = spread / (1 - 40%), T = the
  tenor in years. Index and single-name CDS use the same formula.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from quant_toolbox.data import carried_forward

SECTORS = {
    1: "Energy", 2: "Consumer", 3: "Healthcare", 4: "Technology", 5: "Industrials",
    6: "Financials", 7: "Media", 8: "Utilities", 9: "Materials", 10: "RealEstate",
}  # fmt: skip
SENIORITY = {1: "senior secured", 2: "senior unsecured", 3: "subordinated"}
CURRENCY = {1: "USD", 2: "EUR"}
BOOK = {1: "hy_ls", 2: "ig_rv", 3: "distressed", 4: "overlay", 0: "none"}
BUCKETS = (
    (4, "AA"), (7, "A"), (10, "BBB"), (13, "BB"), (16, "B"), (21, "CCC"), (22, "D"),
)  # fmt: skip
BUCKET_NAMES = tuple(name for _, name in BUCKETS)
TENOR_BUCKETS = ((0.0, 2.0, "0-2y"), (2.0, 5.0, "2-5y"), (5.0, 10.0, "5-10y"), (10.0, 1e9, "10y+"))
CDS_RATE, CDS_RECOVERY = 0.04, 0.40


def rating_bucket(score: float) -> str:
    """Return the bucket (AA or better, A, BBB, BB, B, CCC or below, D) of a rating score."""
    for top, name in BUCKETS:
        if score <= top:
            return name
    return "D"


def risky_annuity(spread_bp: float, years: float = 5.0) -> float:
    """Return the CDS risky annuity for a spread in bp and a tenor in years."""
    rate = CDS_RATE + spread_bp / 1e4 / (1.0 - CDS_RECOVERY)
    return (1.0 - math.exp(-rate * years)) / rate


def issuer_of(bond_id: str) -> str:
    """Return the issuer ticker of a bond id such as ``HALD_31A``."""
    return bond_id.split("_")[0]


def _at(frame: pd.DataFrame, as_of: str | None, label: str) -> tuple[pd.Series, str]:
    """Return the row of a dated frame at ``as_of`` (default: the last) and its date."""
    if as_of is None:
        stamp = frame.index[-1]
    else:
        stamp = pd.Timestamp(as_of)
        if stamp not in frame.index:
            raise ValueError(
                f"{label} has no row for {as_of}; it runs from {frame.index[0].date()} to "
                f"{frame.index[-1].date()} on business days."
            )
    return frame.loc[stamp], stamp.date().isoformat()


def _nav(nav: pd.Series, day: str) -> float:
    value = float(nav.loc[pd.Timestamp(day)])
    if not value > 0:
        raise ValueError(f"The capital on {day} is {value}; it must be positive.")
    return value


@dataclass
class DtsGroup:
    """One group's exposure."""

    group: str
    bonds: int
    market_value_usd: float
    nav_weight: float
    spread_duration_years: float = field(metadata={"unit": "years of the book's capital"})
    dts_bp_years: float


@dataclass
class DtsResult:
    """Spread duration and DTS (duration times spread) of a book or the fund."""

    as_of: str
    capital_usd: float
    groups: list[DtsGroup]
    total_spread_duration_years: float
    total_dts_bp_years: float
    excluded_bonds: int
    warnings: list[str] = field(default_factory=list)


def spread_duration_dts(
    mv: pd.DataFrame,
    sprdur: pd.DataFrame,
    oas: pd.DataFrame,
    static: pd.DataFrame,
    rating: pd.DataFrame,
    capital: pd.Series,
    group_by: str = "sector",
    as_of: str | None = None,
    returns: pd.DataFrame | None = None,
) -> DtsResult:
    """Spread duration and DTS of the bonds in ``mv``, by sector, rating, seniority, and more.

    Args:
        mv: Market value in USD, dates by bonds; the bonds are the book (or the fund) measured.
        sprdur: Spread duration in years, dates by bonds.
        oas: Option-adjusted spread in bp, dates by bonds.
        static: Bond reference table, one row per bond.
        rating: Issuer rating score, dates by bonds.
        capital: The book's capital in USD, by date; the weights divide by it.
        group_by: sector, rating, seniority, currency, book, or none for one total.
        as_of: A business day, YYYY-MM-DD. Default: the last.
        returns: Optional bond total returns, dates by bonds. When given, the window up to
            ``as_of`` is checked for carried-forward prices, and the warnings name them.
    """
    if group_by not in ("sector", "rating", "seniority", "currency", "book", "none"):
        raise ValueError(
            "group_by must be sector, rating, seniority, currency, book, or none; "
            f"not '{group_by}'."
        )
    values, day = _at(mv, as_of, "mv")
    bonds = [b for b in mv.columns if values[b] != 0.0]
    sd, spread = _at(sprdur, day, "sprdur")[0][bonds], _at(oas, day, "oas")[0][bonds]
    capital_usd = _nav(capital, day)
    scores = _at(rating, day, "rating")[0][bonds]
    live = sd.notna() & spread.notna()
    labels: Callable[[str], str] = {
        "sector": lambda b: SECTORS[int(static.loc[b, "sector_code"])],
        "seniority": lambda b: SENIORITY[int(static.loc[b, "seniority_code"])],
        "currency": lambda b: CURRENCY[int(static.loc[b, "currency_code"])],
        "book": lambda b: BOOK[int(static.loc[b, "book_code"])],
        "rating": lambda b: rating_bucket(float(scores[b])),
        "none": lambda b: "all",
    }[group_by]
    rows: dict[str, list[str]] = {}
    for bond in bonds:
        if live[bond]:
            rows.setdefault(labels(bond), []).append(bond)
    groups = []
    for name, members in sorted(rows.items()):
        weights = values[members] / capital_usd
        groups.append(
            DtsGroup(
                group=name,
                bonds=len(members),
                market_value_usd=float(values[members].sum()),
                nav_weight=float(weights.sum()),
                spread_duration_years=float((weights * sd[members]).sum()),
                dts_bp_years=float((weights * sd[members] * spread[members]).sum()),
            )
        )
    skipped = int((~live).sum())
    warnings = [f"{skipped} held bonds have no spread or duration (defaulted) and are left out."]
    if not skipped:
        warnings = []
    if returns is not None:
        warnings.extend(carried_warnings(returns, day))
    return DtsResult(
        as_of=day,
        capital_usd=capital_usd,
        groups=groups,
        total_spread_duration_years=sum(g.spread_duration_years for g in groups),
        total_dts_bp_years=sum(g.dts_bp_years for g in groups),
        excluded_bonds=skipped,
        warnings=warnings,
    )


def carried_warnings(returns: pd.DataFrame, day: str, window: int = 60) -> list[str]:
    """Warnings for carried-forward prices in the ``window`` rows of bond returns up to ``day``.

    A weekday on which every bond's return is exactly zero is a closed day with carried prices;
    a bond with a run of zero returns, or several zeros in the window, is named. Uses the
    platform's `carried_forward` (Quant Toolbox), so the rules match the other tools.
    """
    frame = returns.loc[:day].tail(window)
    dates = [pd.Timestamp(d).date() for d in frame.index]
    assets = {str(bond): [float(value) for value in frame[bond]] for bond in frame.columns}
    found = carried_forward(assets, dates)
    out: list[str] = []
    if found.closed_days:
        days = ", ".join(d.isoformat() for d in found.closed_days)
        out.append(f"Closed days with every bond's return exactly zero (carried prices): {days}.")
    if found.assets:
        bonds = ", ".join(z.asset for z in found.assets)
        out.append(f"Bonds with carried-forward returns in the window: {bonds}.")
    return out


@dataclass
class TenorBucket:
    """Sensitivities of one maturity bucket, in USD per 1 bp."""

    bucket: str
    bond_cs01_usd: float
    cds_cs01_usd: float
    net_cs01_usd: float
    dv01_usd: float


@dataclass
class TenorLadder:
    """CS01 (spread) and DV01 (rates) by years to maturity."""

    as_of: str
    buckets: list[TenorBucket]
    total_cs01_usd: float
    total_dv01_usd: float
    excluded_bonds: int
    warnings: list[str] = field(default_factory=list)


def cs01_dv01_by_tenor(
    mv: pd.DataFrame,
    sprdur: pd.DataFrame,
    moddur: pd.DataFrame,
    ttm: pd.DataFrame,
    cds_protection: pd.DataFrame,
    cds_5y: pd.DataFrame,
    as_of: str | None = None,
) -> TenorLadder:
    """CS01 and DV01 of the bonds in ``mv`` and the 5-year CDS hedges, by years to maturity.

    CS01 is the profit in USD if every spread widens 1 bp: bonds are -mv x spread duration /
    10,000, and protection bought is notional x risky annuity / 10,000 in the 5-10y bucket. DV01
    is the profit if yields rise 1 bp: -mv x modified duration / 10,000, in local-currency
    terms translated at the book's market value.

    Args:
        mv: Market value in USD, dates by bonds.
        sprdur: Spread duration in years, dates by bonds.
        moddur: Modified duration in years, dates by bonds.
        ttm: Years to maturity, dates by bonds.
        cds_protection: Protection bought in USD notional (negative: sold), dates by issuers.
        cds_5y: 5-year CDS spread in bp, dates by issuers.
        as_of: A business day, YYYY-MM-DD. Default: the last.
    """
    values, day = _at(mv, as_of, "mv")
    bonds = [b for b in mv.columns if values[b] != 0.0]
    sd, md = _at(sprdur, day, "sprdur")[0][bonds], _at(moddur, day, "moddur")[0][bonds]
    years = _at(ttm, day, "ttm")[0][bonds]
    live = sd.notna() & md.notna()
    prot, spreads = _at(cds_protection, day, "cds_protection")[0], _at(cds_5y, day, "cds_5y")[0]
    ladder = []
    for low, high, name in TENOR_BUCKETS:
        members = [b for b in bonds if live[b] and low <= years[b] < high]
        bond_cs01 = float((-values[members] * sd[members] / 1e4).sum())
        dv01 = float((-values[members] * md[members] / 1e4).sum())
        cds_cs01 = 0.0
        if name == "5-10y":
            for issuer, notional in prot.items():
                spread = spreads.get(issuer)
                if notional != 0.0 and spread is not None and not math.isnan(spread):
                    cds_cs01 += float(notional) * risky_annuity(float(spread)) / 1e4
        ladder.append(TenorBucket(name, bond_cs01, cds_cs01, bond_cs01 + cds_cs01, dv01))
    skipped = int((~live).sum())
    return TenorLadder(
        as_of=day,
        buckets=ladder,
        total_cs01_usd=sum(b.net_cs01_usd for b in ladder),
        total_dv01_usd=sum(b.dv01_usd for b in ladder),
        excluded_bonds=skipped,
        warnings=[f"{skipped} held bonds have no duration (defaulted) and are left out."]
        if skipped
        else [],
    )


@dataclass
class IssuerJtd:
    """One issuer's jump-to-default result."""

    issuer: str
    bond_pnl_usd: float
    cds_pnl_usd: float
    total_pnl_usd: float
    pct_of_capital: float


@dataclass
class DefaultStress:
    """Jump-to-default by issuer, and a scenario of several defaults."""

    as_of: str
    issuers: list[IssuerJtd]
    scenario_issuers: list[str]
    scenario_pnl_usd: float
    scenario_pct_of_capital: float
    warnings: list[str] = field(default_factory=list)


def default_stress(
    mv: pd.DataFrame,
    px_clean: pd.DataFrame,
    px_accrued: pd.DataFrame,
    oas: pd.DataFrame,
    static: pd.DataFrame,
    cds_protection: pd.DataFrame,
    capital: pd.Series,
    scenario_issuers: list[str] | None = None,
    worst_n: int = 3,
    recovery: float | None = None,
    as_of: str | None = None,
) -> DefaultStress:
    """Jump to default per issuer and the loss if several issuers default together.

    A bond's loss is its face x recovery - its market value (dirty); face is the market value
    over dirty price. Recovery is each bond's assumed one by seniority (60% secured, 40% senior
    unsecured, 15% subordinated) unless ``recovery`` is given. Protection bought pays notional x
    (1 - 40%); the CDS's own mark is ignored. The scenario is ``scenario_issuers`` if given, else
    the ``worst_n`` issuers with the largest losses.

    Args:
        mv: Market value in USD, dates by bonds.
        px_clean: Clean price per 100, dates by bonds.
        px_accrued: Accrued interest per 100, dates by bonds.
        oas: Spread in bp, dates by bonds; a bond with none has already defaulted and is left out.
        static: Bond reference table, one row per bond.
        cds_protection: Protection bought in USD notional, dates by issuers.
        capital: The book's capital in USD, by date.
        scenario_issuers: Issuer tickers that default together. Default: the worst few.
        worst_n: How many of the worst issuers default when no list is given.
        recovery: One recovery rate, 0 to 1, for every bond. Default: by seniority.
        as_of: A business day, YYYY-MM-DD. Default: the last.
    """
    if recovery is not None and not 0.0 <= recovery <= 1.0:
        raise ValueError(f"recovery must be between 0 and 1, not {recovery}.")
    values, day = _at(mv, as_of, "mv")
    capital_usd = _nav(capital, day)
    clean, accrued = _at(px_clean, day, "px_clean")[0], _at(px_accrued, day, "px_accrued")[0]
    spread = _at(oas, day, "oas")[0]
    loss: dict[str, list[float]] = {}
    left_out = 0
    for bond in mv.columns:
        if values[bond] == 0.0:
            continue
        if math.isnan(spread[bond]):
            left_out += 1
            continue
        face_usd = values[bond] * 100.0 / (clean[bond] + accrued[bond])
        rate = recovery if recovery is not None else float(static.loc[bond, "recovery_assumption"])
        loss.setdefault(issuer_of(bond), [0.0, 0.0])[0] += float(face_usd * rate - values[bond])
    prot = _at(cds_protection, day, "cds_protection")[0]
    for issuer, notional in prot.items():
        if notional != 0.0:
            loss.setdefault(str(issuer), [0.0, 0.0])[1] += float(notional) * (1.0 - CDS_RECOVERY)
    rows = [IssuerJtd(i, b, c, b + c, (b + c) / capital_usd) for i, (b, c) in sorted(loss.items())]
    rows.sort(key=lambda r: r.total_pnl_usd)
    chosen = (
        scenario_issuers if scenario_issuers is not None else [r.issuer for r in rows[:worst_n]]
    )
    unknown = [i for i in chosen if i not in loss]
    total = sum(r.total_pnl_usd for r in rows if r.issuer in chosen)
    warnings = (
        [f"{left_out} held bonds have already defaulted and are left out."] if left_out else []
    )
    if unknown:
        warnings.append(f"No position in {', '.join(unknown)}.")
    return DefaultStress(day, rows, list(chosen), total, total / capital_usd, warnings)


@dataclass
class RatingShock:
    """The result of a spread shock for one rating bucket."""

    bucket: str
    shock_bp: float
    pnl_usd: float


@dataclass
class SpreadStress:
    """The first-order profit and loss of a spread shock."""

    as_of: str
    total_pnl_usd: float
    pct_of_capital: float
    by_rating: list[RatingShock]
    warnings: list[str] = field(default_factory=list)


def spread_shock_stress(
    mv: pd.DataFrame,
    sprdur: pd.DataFrame,
    rating: pd.DataFrame,
    cds_protection: pd.DataFrame,
    capital: pd.Series,
    parallel_bp: float = 0.0,
    by_rating_bp: dict[str, float] | None = None,
    as_of: str | None = None,
) -> SpreadStress:
    """Profit and loss if spreads widen: a parallel shock plus a shock per rating bucket.

    First order only: bonds lose mv x spread duration x shock / 10,000, protection gains
    notional x 5-year risky annuity at 300 bp x shock / 10,000; no convexity, no rates, and no
    repricing of calls. A CDS takes the shock of its issuer's rating.

    Args:
        mv: Market value in USD, dates by bonds.
        sprdur: Spread duration in years, dates by bonds.
        rating: Issuer rating score, dates by bonds.
        cds_protection: Protection bought in USD notional, dates by issuers.
        capital: The book's capital in USD, by date.
        parallel_bp: Widening of every spread in bp (negative: tightening).
        by_rating_bp: Extra widening by bucket: AA, A, BBB, BB, B, CCC.
        as_of: A business day, YYYY-MM-DD. Default: the last.
    """
    extra = by_rating_bp or {}
    unknown = [k for k in extra if k not in BUCKET_NAMES]
    if unknown:
        raise ValueError(f"Unknown rating buckets {unknown}; use {', '.join(BUCKET_NAMES)}.")
    values, day = _at(mv, as_of, "mv")
    capital_usd = _nav(capital, day)
    sd, scores = _at(sprdur, day, "sprdur")[0], _at(rating, day, "rating")[0]
    per_bucket: dict[str, float] = dict.fromkeys(BUCKET_NAMES, 0.0)
    skipped = 0
    issuer_bucket: dict[str, str] = {}
    for bond in mv.columns:
        bucket = rating_bucket(float(scores[bond]))
        issuer_bucket[issuer_of(bond)] = bucket
        if values[bond] == 0.0:
            continue
        if math.isnan(sd[bond]):
            skipped += 1
            continue
        shock = parallel_bp + extra.get(bucket, 0.0)
        per_bucket[bucket] += -float(values[bond] * sd[bond]) * shock / 1e4
    for issuer, notional in _at(cds_protection, day, "cds_protection")[0].items():
        named = issuer_bucket.get(str(issuer))
        if notional != 0.0 and named is not None:
            shock = parallel_bp + extra.get(named, 0.0)
            per_bucket[named] += float(notional) * risky_annuity(300.0) * shock / 1e4
    total = sum(per_bucket.values())
    return SpreadStress(
        as_of=day,
        total_pnl_usd=total,
        pct_of_capital=total / capital_usd,
        by_rating=[
            RatingShock(b, parallel_bp + extra.get(b, 0.0), p)
            for b, p in per_bucket.items()
            if p != 0.0
        ],
        warnings=[f"{skipped} held bonds have no duration (defaulted) and are left out."]
        if skipped
        else [],
    )


@dataclass
class BasisRow:
    """One bond's basis to its issuer's CDS."""

    bond: str
    oas_bp: float
    cds_bp: float
    basis_bp: float


@dataclass
class BasisResult:
    """Bond-CDS basis, most negative first."""

    as_of: str
    rows: list[BasisRow]
    mean_basis_bp: float
    bonds_without_cds: int


def bond_cds_basis(
    oas: pd.DataFrame,
    ttm: pd.DataFrame,
    cds_3y: pd.DataFrame,
    cds_5y: pd.DataFrame,
    cds_10y: pd.DataFrame,
    top_n: int = 10,
    as_of: str | None = None,
) -> BasisResult:
    """Basis of each bond to its issuer's CDS: the CDS spread at the bond's maturity minus its OAS.

    The CDS spread is interpolated linearly in years across 3, 5, and 10 years, and flat outside.
    A negative basis means the bond is cheap to the CDS. Bonds whose issuer has no CDS quote, or
    that have no OAS, are counted and left out.

    Args:
        oas: Spread in bp, dates by bonds.
        ttm: Years to maturity, dates by bonds.
        cds_3y: 3-year CDS spread in bp, dates by issuers.
        cds_5y: 5-year CDS spread in bp, dates by issuers.
        cds_10y: 10-year CDS spread in bp, dates by issuers.
        top_n: How many bonds to list, most negative basis first.
        as_of: A business day, YYYY-MM-DD. Default: the last.
    """
    spread, day = _at(oas, as_of, "oas")
    years = _at(ttm, day, "ttm")[0]
    curves = [_at(frame, day, name)[0] for frame, name in
              ((cds_3y, "cds_3y"), (cds_5y, "cds_5y"), (cds_10y, "cds_10y"))]  # fmt: skip
    rows: list[BasisRow] = []
    missing = 0
    for bond in oas.columns:
        issuer = issuer_of(bond)
        if issuer not in cds_5y.columns or math.isnan(spread[bond]):
            missing += 1
            continue
        quotes = [float(c[issuer]) for c in curves]
        if any(math.isnan(q) for q in quotes):
            missing += 1
            continue
        cds = _interpolate((3.0, 5.0, 10.0), quotes, float(years[bond]))
        rows.append(BasisRow(bond, float(spread[bond]), cds, cds - float(spread[bond])))
    rows.sort(key=lambda r: r.basis_bp)
    mean = sum(r.basis_bp for r in rows) / len(rows) if rows else float("nan")
    return BasisResult(day, rows[:top_n], mean, missing)


def _interpolate(tenors: tuple[float, ...], values: list[float], x: float) -> float:
    if x <= tenors[0]:
        return values[0]
    for t0, t1, v0, v1 in zip(tenors, tenors[1:], values, values[1:], strict=False):
        if x <= t1:
            return v0 + (v1 - v0) * (x - t0) / (t1 - t0)
    return values[-1]


@dataclass
class Weight:
    """A name's weight in the book's capital."""

    name: str
    gross_pct: float
    net_pct: float


@dataclass
class Concentration:
    """Issuer and sector concentration."""

    as_of: str
    top_issuers: list[Weight]
    sectors: list[Weight]
    issuer_herfindahl: float
    top_issuers_gross_share: float


def issuer_sector_concentration(
    mv: pd.DataFrame,
    static: pd.DataFrame,
    capital: pd.Series,
    top_n: int = 10,
    as_of: str | None = None,
) -> Concentration:
    """Gross and net exposure by issuer and sector, as percent of capital.

    The Herfindahl index is the sum of squared shares of gross issuer exposure (1 is one issuer);
    ``top_issuers_gross_share`` is the share of gross exposure in the ``top_n`` issuers.

    Args:
        mv: Market value in USD, dates by bonds.
        static: Bond reference table, one row per bond.
        capital: The book's capital in USD, by date.
        top_n: How many issuers to list, largest gross first.
        as_of: A business day, YYYY-MM-DD. Default: the last.
    """
    values, day = _at(mv, as_of, "mv")
    capital_usd = _nav(capital, day)
    issuers: dict[str, list[float]] = {}
    sectors: dict[str, list[float]] = {}
    for bond in mv.columns:
        value = float(values[bond])
        if value == 0.0:
            continue
        for table, key in (
            (issuers, issuer_of(bond)),
            (sectors, SECTORS[int(static.loc[bond, "sector_code"])]),
        ):
            entry = table.setdefault(key, [0.0, 0.0])
            entry[0] += abs(value)
            entry[1] += value

    def weights(table: dict[str, list[float]]) -> list[Weight]:
        return sorted(
            (
                Weight(k, 100 * g / capital_usd, 100 * n / capital_usd)
                for k, (g, n) in table.items()
            ),
            key=lambda w: -w.gross_pct,
        )

    gross = sum(g for g, _ in issuers.values())
    shares = [g / gross for g, _ in issuers.values()] if gross else []
    ordered = weights(issuers)
    return Concentration(
        as_of=day,
        top_issuers=ordered[:top_n],
        sectors=weights(sectors),
        issuer_herfindahl=sum(s * s for s in shares),
        top_issuers_gross_share=sum(w.gross_pct for w in ordered[:top_n])
        / (100 * gross / capital_usd)
        if gross
        else 0.0,
    )


@dataclass
class MigrationRow:
    """Where issuers starting in one bucket ended up."""

    start: str
    issuers: int
    counts: dict[str, int]
    probabilities: dict[str, float]


@dataclass
class MigrationMatrix:
    """A rating transition matrix from month-end scores."""

    months: int
    windows: int
    rows: list[MigrationRow]
    warnings: list[str] = field(default_factory=list)


def rating_migration(scores: pd.DataFrame, months: int = 12) -> MigrationMatrix:
    """Rating transition matrix over ``months`` months, from every window in the month-end history.

    Each issuer and each start month with ``months`` more months of history is one observation:
    its bucket then and its bucket ``months`` later (AA or better, A, BBB, BB, B, CCC or below,
    D). D is absorbing and has no row. Probabilities are row shares of the counts.

    Args:
        scores: Issuer rating scores, month-ends by issuers.
        months: The horizon in months (1 to 24).
    """
    if not 1 <= months <= 24 or months >= len(scores):
        raise ValueError(f"months must be from 1 to 24 and below the {len(scores)} months given.")
    counts = {a: dict.fromkeys(BUCKET_NAMES, 0) for a in BUCKET_NAMES}
    buckets = scores.map(lambda score: rating_bucket(float(score)))
    for start in range(len(scores) - months):
        for issuer in scores.columns:
            counts[buckets.iloc[start][issuer]][buckets.iloc[start + months][issuer]] += 1
    rows = []
    for start_bucket in BUCKET_NAMES[:-1]:
        row = counts[start_bucket]
        total = sum(row.values())
        rows.append(
            MigrationRow(
                start_bucket,
                total,
                row,
                {b: (c / total if total else 0.0) for b, c in row.items()},
            )
        )
    thin = [r.start for r in rows if 0 < r.issuers < 100]
    return MigrationMatrix(
        months,
        len(scores) - months,
        rows,
        [f"Few observations in {', '.join(thin)}: read those rows with care."] if thin else [],
    )
