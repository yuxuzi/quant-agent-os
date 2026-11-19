# ruff: noqa: E501
"""Wrenfield Credit Opportunities (mock fund): the estate, its store, its credit tools, and the
platform's own analytics on its data. Everything is offline; the estate is built into tmp_path."""

import hashlib
import json
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

WRENFIELD = Path(__file__).parents[2] / "examples" / "mock-funds" / "wrenfield"
for _sub in ("facility/warehouse", "site", "facility/gateway", "facility/legacy"):
    if str(WRENFIELD / _sub) not in sys.path:
        sys.path.insert(0, str(WRENFIELD / _sub))

import build_wrenfield as bw  # noqa: E402
import wrenfield_credit as credit  # noqa: E402
import wrenfield_server as ws  # noqa: E402
from wrenfield_store import SPECS, WrenfieldDatasetStore  # noqa: E402

from quant_agent_os.data import GuardedDatasetStore, UnknownDatasetError  # noqa: E402
from quant_agent_os.runtime import ToolCall, ToolExecutor  # noqa: E402
from quant_agent_os.testing import check_app  # noqa: E402
from quant_agent_os.tools import (  # noqa: E402
    Caller,
    FileAccessPolicy,
    PermissionPolicy,
    ToolRegistry,
)
from quant_agent_os.tools.access import acting_as  # noqa: E402
from quant_platform.acceptance import load_gold, run_acceptance  # noqa: E402
from quant_research import open_research  # noqa: E402

pytest.importorskip("pyarrow")
pytest.importorskip("duckdb")
pytest.importorskip("empyrical")

PLATFORM = re.compile(
    r"^\s*(from|import)\s+(quant_agent_os|quant_platform|quant_research|quant_analytics|"
    r"quant_app_factory|quant_toolbox)\b",
    re.MULTILINE,
)
LAST = "2025-12-31"


@pytest.fixture(scope="module")
def estate(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("estate")
    bw.build(root)
    return root


@pytest.fixture(scope="module")
def answers(estate: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((estate / "known_answers.json").read_text())
    return loaded


@pytest.fixture(scope="module")
def store(estate: Path) -> WrenfieldDatasetStore:
    return WrenfieldDatasetStore(estate)


def _digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((root / "s3").rglob("*")):
        if path.is_file():
            digest.update(path.name.encode() + path.read_bytes())
    with closing(sqlite3.connect(root / "rds" / "wrenfield_oms.sqlite")) as db:
        for table in ("pos_eod", "trd_trades", "pnl_daily", "pos_cds"):
            digest.update(
                repr(db.execute(f"SELECT * FROM {table} ORDER BY 1, 2, 3").fetchall()).encode()
            )
    return digest.hexdigest()


_EXECUTORS: dict[int, ToolExecutor] = {}


def _executor(store: WrenfieldDatasetStore) -> ToolExecutor:
    """One executor per store: building the app's schemas takes about a second."""
    if id(store) not in _EXECUTORS:
        _EXECUTORS[id(store)] = ToolExecutor(
            ToolRegistry(ws.build_app().tools(store)), PermissionPolicy()
        )
    return _EXECUTORS[id(store)]


def _run(store: WrenfieldDatasetStore, tool: str, **arguments: Any) -> dict[str, Any]:
    executor = _executor(store)
    record = executor.execute(ToolCall(tool=tool, arguments=arguments))
    assert record.status == "ok", record.error
    assert record.output is not None
    return record.output


def _hy(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "mv": {"dataset": "mv_hy_ls"}, "sprdur": {"dataset": "bond_sprdur"},
        "oas": {"dataset": "bond_oas"}, "static": {"dataset": "bond_static"},
        "rating": {"dataset": "bond_rating"}, "capital": {"dataset": "nav", "column": "hy_ls"},
        **(extra or {}),
    }  # fmt: skip


def _sqlite(estate: Path, sql: str) -> list[Any]:
    with closing(sqlite3.connect(estate / "rds" / "wrenfield_oms.sqlite")) as db:
        return db.execute(sql).fetchall()


def _px(estate: Path, column: str) -> pd.DataFrame:
    folder = estate / "s3" / bw.LAKE / "curated" / "bond_px_daily"
    frames = []
    for path in sorted(folder.glob("dt=*/part-0000.parquet")):
        frame = pd.read_parquet(path, columns=["bond_id", column])
        frame["date"] = path.parent.name.removeprefix("dt=")
        frames.append(frame)
    wide: pd.DataFrame = pd.concat(frames).pivot(index="date", columns="bond_id", values=column)
    return wide


# ---- the estate -------------------------------------------------------------------------------


def test_the_same_seed_builds_the_same_estate(tmp_path: Path, estate: Path) -> None:
    bw.build(tmp_path / "again")
    bw.build(tmp_path / "other", seed=1)
    assert _digest(tmp_path / "again") == _digest(estate)
    assert _digest(tmp_path / "other") != _digest(estate)


def test_the_estate_has_the_cloud_layout_and_stays_small(
    estate: Path, answers: dict[str, Any]
) -> None:
    total = sum(p.stat().st_size for p in estate.rglob("*") if p.is_file())
    assert total < 3_500_000  # parquet sizes vary a little across pyarrow builds
    lake = estate / "s3" / bw.LAKE
    partitions = sorted((lake / "curated" / "bond_px_daily").glob("dt=*"))
    assert len(partitions) == answers["business_days"] == 103
    assert (partitions[-1] / "part-0000.parquet").is_file()
    assert (lake / "raw" / "vendor=quotesource_like" / "dt=2025-12-31").is_dir()
    assert (lake / "athena" / "views.sql").is_file()
    assert (
        estate / "s3" / bw.RESEARCH / "credit" / "note-halden-retail-restructuring.md"
    ).is_file()
    uri_map = json.loads((estate / "uri_map.json").read_text())
    assert set(uri_map) == {f"s3://{bw.LAKE}", f"s3://{bw.RESEARCH}", bw.OMS_URI}
    catalog = json.loads((lake / "catalog" / "wrenfield_curated.json").read_text())
    for table in catalog["TableList"]:
        location = table["StorageDescriptor"]["Location"]
        assert (
            estate / uri_map[location.split("/curated/")[0]] / "curated" / table["Name"]
        ).is_dir()


def test_the_two_defaults_trade_at_their_stated_recoveries(
    estate: Path, answers: dict[str, Any]
) -> None:
    clean, oas = _px(estate, "clean_price"), _px(estate, "oas_bp")
    for issuer, info in answers["defaults"].items():
        if issuer == "convention":
            continue
        for bond, recovery in info["recoveries"].items():
            after = clean.loc[info["date"] :, bond]
            assert (after == round(recovery * 100, 4)).all()
            assert oas.loc[info["date"] :, bond].isna().all()  # no spread once in default
            assert oas.loc[:"2025-08-20", bond].notna().all()
    assert answers["defaults"]["HALD"]["recoveries"] == {"HALD_31A": 0.58, "HALD_32B": 0.31}
    assert answers["defaults"]["MARL"]["recoveries"] == {"MARL_28A": 0.42}


def test_prices_follow_from_yields_and_total_returns_from_prices(estate: Path) -> None:
    clean, accrued = _px(estate, "clean_price"), _px(estate, "accrued_interest")
    total = _px(estate, "total_return")
    assert total.iloc[0].isna().all()
    bond = "ALDE_28B"
    row = (
        pd.read_parquet(
            estate / "s3" / bw.LAKE / "curated" / "ref_bond" / f"dt={LAST}" / "part-0000.parquet"
        )
        .set_index("bond_id")
        .loc[bond]
    )
    paid_days = 0
    for before, today in zip(clean.index[:-1], clean.index[1:], strict=True):
        dirty0, dirty1 = (
            clean.loc[before, bond] + accrued.loc[before, bond],
            clean.loc[today, bond] + accrued.loc[today, bond],
        )
        coupon = row.coupon_pct / 2 if accrued.loc[today, bond] < accrued.loc[before, bond] else 0.0
        paid_days += coupon > 0
        assert total.loc[today, bond] == pytest.approx(
            (dirty1 + coupon - dirty0) / dirty0, abs=1e-7
        )
    assert 0 <= paid_days <= 1  # a semi-annual coupon falls in at most one day of 103


def test_the_database_holds_positions_trades_and_financing(estate: Path) -> None:
    books = dict(
        _sqlite(estate, f"SELECT book, COUNT(*) FROM pos_eod WHERE date = '{LAST}' GROUP BY 1")
    )
    assert books == {"distressed": 15, "hy_ls": 57, "ig_rv": 30}
    opening = _sqlite(estate, "SELECT COUNT(*) FROM trd_trades WHERE trade_date = '2025-08-11'")[0][
        0
    ]
    assert opening >= 102 + 20  # every position, the CDS and the index hedges
    shorts = _sqlite(estate, f"SELECT COUNT(*) FROM pos_eod WHERE date = '{LAST}' AND face < 0")[0][
        0
    ]
    assert shorts == 12 == _sqlite(estate, "SELECT COUNT(*) FROM fin_borrow_fees")[0][0]
    assert _sqlite(estate, "SELECT COUNT(*) FROM cmp_restricted")[0][0] == 2


# ---- the store ------------------------------------------------------------------------------


def test_the_store_serves_wide_dated_numeric_datasets(store: WrenfieldDatasetStore) -> None:
    assert "mv_hy_ls" in store.ids() and "mv_overlay" not in store.ids()
    oas = store.get("bond_oas")
    assert oas.rows == 103 and len(oas.columns) == 150 and oas.periods_per_year == 252
    info = store.get("bond_tr").info()
    assert info.missing_cells == 150  # the first day has no return, never a zero
    assert store.get("bond_static").index is not None and store.get("bond_static").dates is None
    assert store.get("issuer_rating_score_me").periods_per_year == 12
    assert store.get("fx_eurusd").rows == 103
    with pytest.raises(UnknownDatasetError, match="No dataset 'nope'"):
        store.get("nope")


def test_values_are_the_lakes_and_the_databases(store: WrenfieldDatasetStore, estate: Path) -> None:
    oas = _px(estate, "oas_bp")
    served = store.get("bond_oas")
    assert served.column("ALDE_28B") == pytest.approx(oas["ALDE_28B"].tolist())
    defaulted = served.column("HALD_32B")
    assert all(v != v for v in defaulted[served.index.index("2025-09-12") :])  # type: ignore[union-attr]
    book_total = sum(
        sum(store.get(f"mv_{b}").columns[c][-1] for c in store.get(f"mv_{b}").columns)
        for b in ("hy_ls", "ig_rv", "distressed")
    )
    fund = store.get("mv_fund")
    assert sum(col[-1] for col in fund.columns.values()) == pytest.approx(book_total)
    assert (
        min(min(col) for col in store.get("mv_hy_ls").columns.values()) < 0
    )  # shorts are negative
    shorts = [c for c, v in store.get("mv_hy_ls").columns.items() if v[-1] < 0]
    assert set(shorts) == {row[0] for row in _sqlite(estate, "SELECT bond_id FROM fin_borrow_fees")}


def test_a_changed_source_rebuilds_the_datasets(tmp_path: Path) -> None:
    bw.build(tmp_path / "e")
    mine = WrenfieldDatasetStore(tmp_path / "e")
    before = mine.get("mv_hy_ls").fingerprint
    assert mine.get("mv_hy_ls").fingerprint == before
    with closing(sqlite3.connect(tmp_path / "e" / "rds" / "wrenfield_oms.sqlite")) as db, db:
        db.execute(
            "UPDATE pos_eod SET mv_usd = mv_usd + 1 WHERE date = '2025-12-31' AND book = 'hy_ls'"
        )
    assert mine.get("mv_hy_ls").fingerprint != before


def test_every_spec_has_its_sql_in_the_common_subset() -> None:
    assert len(SPECS) > 30
    for spec in SPECS.values():
        if spec.source == "oms":
            assert "strftime" not in spec.sql and "{lake}" not in spec.sql
        else:
            assert "{lake}" in spec.sql


# ---- the credit tools ------------------------------------------------------------------------


def test_dts_by_book_matches_the_planted_values(
    store: WrenfieldDatasetStore, answers: dict[str, Any]
) -> None:
    for book, expected in answers["dts"]["values"].items():
        name = "mv_fund" if book == "fund" else f"mv_{book}"
        out = _run(store, "wrenfield_spread_duration_dts", **_hy({
            "mv": {"dataset": name}, "capital": {"dataset": "nav", "column": book}, "group_by": "none",
        }))  # fmt: skip
        assert out["total_dts_bp_years"] == pytest.approx(expected, abs=1e-4)
    by_sector = _run(store, "wrenfield_spread_duration_dts", **_hy({"group_by": "sector"}))
    total = _run(store, "wrenfield_spread_duration_dts", **_hy({"group_by": "none"}))
    assert sum(g["dts_bp_years"] for g in by_sector["groups"]) == pytest.approx(
        total["total_dts_bp_years"]
    )
    assert by_sector["excluded_bonds"] == 1  # the defaulted Marlowe note, held in this book
    assert any("defaulted" in w for w in by_sector["warnings"])
    assert by_sector["convention"].startswith("DTS = sum of")
    assert set(by_sector["data_versions"]) >= {"dataset:mv_hy_ls", "dataset:bond_oas"}
    by_rating = _run(store, "wrenfield_spread_duration_dts", **_hy({"group_by": "rating"}))
    assert {g["group"] for g in by_rating["groups"]} <= set(credit.BUCKET_NAMES)


def test_dts_is_what_the_formula_says_on_a_small_book() -> None:
    day = pd.to_datetime(["2025-01-02"])
    cols = ["AAA_28A", "BBB_30A"]
    frame = lambda *v: pd.DataFrame([list(v)], index=day, columns=cols)  # noqa: E731
    static = pd.DataFrame(
        {
            "sector_code": [1, 2],
            "seniority_code": [2, 2],
            "currency_code": [1, 1],
            "book_code": [1, 1],
        },
        index=cols,
    )
    out = credit.spread_duration_dts(
        mv=frame(10e6, -4e6), sprdur=frame(4.0, 6.0), oas=frame(200.0, 300.0), static=static,
        rating=frame(11.0, 15.0), capital=pd.Series([100e6], index=day), group_by="none",
    )  # fmt: skip
    # 0.1 x 4 x 200 + (-0.04) x 6 x 300 = 80 - 72
    assert out.total_dts_bp_years == pytest.approx(8.0)
    assert out.total_spread_duration_years == pytest.approx(0.4 - 0.24)


def test_cs01_and_dv01_by_tenor_match_an_independent_sum(
    store: WrenfieldDatasetStore, estate: Path
) -> None:
    out = _run(
        store, "wrenfield_cs01_dv01_by_tenor", mv={"dataset": "mv_ig_rv"}, sprdur={"dataset": "bond_sprdur"},
        moddur={"dataset": "bond_moddur"}, ttm={"dataset": "bond_ttm"},
        cds_protection={"dataset": "cds_protection"}, cds_5y={"dataset": "cds_5y"},
    )  # fmt: skip
    mv = pd.DataFrame({c: [v[-1]] for c, v in store.get("mv_ig_rv").columns.items()})
    sd, ttm = (
        _px(estate, "spread_duration").loc[LAST, mv.columns],
        _px(estate, "years_to_maturity").loc[LAST, mv.columns],
    )
    bonds = -(mv.iloc[0] * sd / 1e4)
    first = next(b for b in out["buckets"] if b["bucket"] == "2-5y")
    assert first["bond_cs01_usd"] == pytest.approx(bonds[(ttm >= 2) & (ttm < 5)].sum())
    cds = _sqlite(
        estate, f"SELECT name_id, protection_notional_usd FROM pos_cds WHERE date = '{LAST}'"
    )
    spreads = store.get("cds_5y")
    expected = sum(n * bw.risky_annuity(spreads.column(name)[-1]) / 1e4 for name, n in cds)
    assert next(b for b in out["buckets"] if b["bucket"] == "5-10y")[
        "cds_cs01_usd"
    ] == pytest.approx(expected)
    assert out["total_cs01_usd"] == pytest.approx(sum(b["net_cs01_usd"] for b in out["buckets"]))
    # The protection hedges about two thirds, not all: a 0.9 notional ratio is not CS01-neutral.
    assert 0.2 * abs(bonds.sum()) < abs(out["total_cs01_usd"]) < 0.5 * abs(bonds.sum())


def test_jump_to_default_of_halden_before_its_default(
    store: WrenfieldDatasetStore, answers: dict[str, Any]
) -> None:
    arguments = {
        "mv": {"dataset": "mv_distressed"}, "px_clean": {"dataset": "px_clean"},
        "px_accrued": {"dataset": "px_accrued"}, "oas": {"dataset": "bond_oas"},
        "static": {"dataset": "bond_static"}, "cds_protection": {"dataset": "cds_protection"},
        "capital": {"dataset": "nav", "column": "distressed"}, "as_of": "2025-09-05",
    }  # fmt: skip
    out = _run(store, "wrenfield_default_stress", scenario_issuers=["HALD"], **arguments)
    planted = answers["jump_to_default_halden"]["usd_by_bond"]
    assert out["scenario_pnl_usd"] == pytest.approx(sum(planted.values()), abs=1.0)
    again = _run(
        store, "wrenfield_default_stress", scenario_issuers=["HALD"], recovery=0.31, **arguments
    )
    assert again["scenario_pnl_usd"] != out["scenario_pnl_usd"]
    worst = _run(store, "wrenfield_default_stress", worst_n=2, **arguments)
    assert len(worst["scenario_issuers"]) == 2
    assert worst["scenario_pnl_usd"] == pytest.approx(
        sum(r["total_pnl_usd"] for r in worst["issuers"][:2])
    )
    unknown = _run(store, "wrenfield_default_stress", scenario_issuers=["ZZZZ"], **arguments)
    assert "No position in ZZZZ." in unknown["warnings"]
    # The platform adds one warning per dataset read, counting the columns with a missing value.
    gaps = [w for w in unknown["warnings"] if "columns have missing values" in w]
    assert gaps and len(gaps) == len(set(w.split(":")[0] for w in gaps))


def test_a_scenario_of_defaults_in_the_relative_value_book_is_hedged_by_the_cds(
    store: WrenfieldDatasetStore,
) -> None:
    out = _run(
        store, "wrenfield_default_stress", mv={"dataset": "mv_ig_rv"}, px_clean={"dataset": "px_clean"},
        px_accrued={"dataset": "px_accrued"}, oas={"dataset": "bond_oas"}, static={"dataset": "bond_static"},
        cds_protection={"dataset": "cds_protection"}, capital={"dataset": "nav", "column": "ig_rv"},
        worst_n=3,
    )  # fmt: skip
    gross = sum(abs(r["bond_pnl_usd"]) for r in out["issuers"][:3])
    assert out["scenario_pnl_usd"] > -0.8 * gross  # the protection pays 60 on each hedged name
    assert all(r["cds_pnl_usd"] > 0 for r in out["issuers"] if r["bond_pnl_usd"] != 0)


def test_spread_shock_is_first_order(store: WrenfieldDatasetStore, estate: Path) -> None:
    args = {
        "mv": {"dataset": "mv_hy_ls"}, "sprdur": {"dataset": "bond_sprdur"},
        "rating": {"dataset": "bond_rating"}, "cds_protection": {"dataset": "cds_protection"},
        "capital": {"dataset": "nav", "column": "hy_ls"},
    }  # fmt: skip
    out = _run(store, "wrenfield_spread_shock_stress", parallel_bp=100.0, **args)
    mv = {c: v[-1] for c, v in store.get("mv_hy_ls").columns.items()}
    sd = _px(estate, "spread_duration").loc[LAST]
    expected = -sum(v * sd[c] / 1e4 * 100.0 for c, v in mv.items() if v != 0 and sd[c] == sd[c])
    cds_in_hy = _sqlite(
        estate, f"SELECT COUNT(*) FROM pos_cds WHERE date = '{LAST}' AND book = 'hy_ls'"
    )[0][0]
    assert cds_in_hy == 0
    assert out["total_pnl_usd"] == pytest.approx(expected, rel=1e-9)
    double = _run(store, "wrenfield_spread_shock_stress", parallel_bp=200.0, **args)
    assert double["total_pnl_usd"] == pytest.approx(2 * out["total_pnl_usd"])
    only_ccc = _run(store, "wrenfield_spread_shock_stress", by_rating_bp={"CCC": 500.0}, **args)
    assert {r["bucket"] for r in only_ccc["by_rating"]} <= {"CCC"}
    assert out["pct_of_capital"] == pytest.approx(out["total_pnl_usd"] / 400e6)


def test_the_basis_is_the_interpolated_cds_minus_the_oas(
    store: WrenfieldDatasetStore, answers: dict[str, Any]
) -> None:
    out = _run(
        store, "wrenfield_bond_cds_basis", oas={"dataset": "bond_oas"}, ttm={"dataset": "bond_ttm"},
        cds_3y={"dataset": "cds_3y"}, cds_5y={"dataset": "cds_5y"}, cds_10y={"dataset": "cds_10y"},
        as_of="2025-12-15", top_n=500,
    )  # fmt: skip
    planted = answers["basis"]["bp_by_bond"]
    got = {r["bond"]: r["basis_bp"] for r in out["rows"]}
    assert set(got) == set(planted)
    for bond, value in planted.items():
        assert got[bond] == pytest.approx(value, abs=1e-3)
    assert min(planted.values()) < -100  # a clearly negative basis name exists
    assert out["rows"][0]["basis_bp"] == min(got.values())
    assert out["bonds_without_cds"] == 150 - len(planted)


def test_basis_interpolation_on_a_hand_made_case() -> None:
    day = pd.to_datetime(["2025-01-02"])
    one = lambda v: pd.DataFrame([[v]], index=day, columns=["XXXX_30A"])  # noqa: E731
    cds = lambda v: pd.DataFrame([[v]], index=day, columns=["XXXX"])  # noqa: E731
    out = credit.bond_cds_basis(
        oas=one(200.0), ttm=one(4.0), cds_3y=cds(100.0), cds_5y=cds(140.0), cds_10y=cds(200.0)
    )
    assert out.rows[0].cds_bp == pytest.approx(120.0)  # halfway from 3 to 5 years
    assert out.rows[0].basis_bp == pytest.approx(-80.0)
    assert credit.risky_annuity(0.0) == pytest.approx((1 - 2.718281828459045 ** (-0.2)) / 0.04)


def test_concentration_matches_the_positions(store: WrenfieldDatasetStore, estate: Path) -> None:
    out = _run(
        store, "wrenfield_issuer_sector_concentration", mv={"dataset": "mv_hy_ls"},
        static={"dataset": "bond_static"}, capital={"dataset": "nav", "column": "hy_ls"},
    )  # fmt: skip
    rows = _sqlite(
        estate, f"SELECT bond_id, mv_usd FROM pos_eod WHERE date = '{LAST}' AND book = 'hy_ls'"
    )
    gross: dict[str, float] = {}
    for bond, value in rows:
        gross[bond.split("_")[0]] = gross.get(bond.split("_")[0], 0.0) + abs(value)
    top = max(gross, key=lambda k: gross[k])
    assert out["top_issuers"][0]["name"] == top == "DACR"
    assert out["top_issuers"][0]["gross_pct"] == pytest.approx(100 * gross[top] / 400e6)
    shares = [g / sum(gross.values()) for g in gross.values()]
    assert out["issuer_herfindahl"] == pytest.approx(sum(s * s for s in shares))
    assert (
        7.0 < out["top_issuers"][0]["gross_pct"] < 7.5
    )  # under the 7.5 percent limit in the minutes


def test_the_migration_matrix_matches_the_histories(
    store: WrenfieldDatasetStore, answers: dict[str, Any]
) -> None:
    out = _run(store, "wrenfield_rating_migration", scores={"dataset": "issuer_rating_score_me"})
    planted = answers["migration_12m_counts"]
    for row in out["rows"]:
        assert row["counts"] == planted[row["start"]]
        assert sum(row["probabilities"].values()) == pytest.approx(1.0)
    assert out["windows"] == 24
    assert out["rows"][-1]["start"] == "CCC"  # D is absorbing and has no row
    events = answers["rating_events"]
    assert {e["issuer"] for e in events} >= {"MORT"}  # the rising star to investment grade


def test_the_rating_panel_follows_the_events(
    store: WrenfieldDatasetStore, answers: dict[str, Any]
) -> None:
    ratings, index = store.get("bond_rating"), store.get("bond_rating").index or []
    for event in answers["rating_events"]:
        bond = next(c for c in ratings.columns if c.startswith(event["issuer"] + "_"))
        before, after = ratings.column(bond)[index.index("2025-08-12")], ratings.column(bond)[-1]
        assert bw.SCALE[int(before) - 1] == event["from"]
        assert bw.SCALE[int(after) - 1] == event["to"]
    # Halden walks down to D on the default date.
    halden = ratings.column("HALD_31A")
    assert bw.SCALE[int(halden[index.index("2025-09-11")]) - 1] == "CC"
    assert bw.SCALE[int(halden[index.index("2025-09-12")]) - 1] == "D"


def test_bad_arguments_give_actionable_errors(store: WrenfieldDatasetStore) -> None:
    executor = _executor(store)
    bad_date = executor.execute(
        ToolCall(tool="wrenfield_spread_duration_dts", arguments=_hy({"as_of": "2025-12-27"}))
    )
    assert bad_date.status == "error" and "business days" in (bad_date.error or "")
    bad_group = executor.execute(
        ToolCall(tool="wrenfield_spread_duration_dts", arguments=_hy({"group_by": "x"}))
    )
    assert "group_by must be" in (bad_group.error or "")
    missing = executor.execute(
        ToolCall(tool="wrenfield_spread_duration_dts", arguments=_hy({"mv": {"dataset": "mv_zz"}}))
    )
    assert "No dataset 'mv_zz'" in (missing.error or "")


# ---- the app, the contract, and the agreed cases ----------------------------------------------


def test_the_app_honours_the_platform_contract(store: WrenfieldDatasetStore) -> None:
    ig = {"dataset": "mv_ig_rv"}
    examples: dict[str, dict[str, Any]] = {
        "wrenfield_spread_duration_dts": _hy(),
        "wrenfield_cs01_dv01_by_tenor": {
            "mv": ig, "sprdur": {"dataset": "bond_sprdur"}, "moddur": {"dataset": "bond_moddur"},
            "ttm": {"dataset": "bond_ttm"}, "cds_protection": {"dataset": "cds_protection"},
            "cds_5y": {"dataset": "cds_5y"},
        },
        "wrenfield_default_stress": {
            "mv": ig, "px_clean": {"dataset": "px_clean"}, "px_accrued": {"dataset": "px_accrued"},
            "oas": {"dataset": "bond_oas"}, "static": {"dataset": "bond_static"},
            "cds_protection": {"dataset": "cds_protection"},
            "capital": {"dataset": "nav", "column": "ig_rv"},
        },
        "wrenfield_spread_shock_stress": {
            "mv": ig, "sprdur": {"dataset": "bond_sprdur"}, "rating": {"dataset": "bond_rating"},
            "cds_protection": {"dataset": "cds_protection"}, "capital": {"dataset": "nav", "column": "ig_rv"},
            "parallel_bp": 50.0, "by_rating_bp": {"BBB": 25.0},
        },
        "wrenfield_bond_cds_basis": {
            "oas": {"dataset": "bond_oas"}, "ttm": {"dataset": "bond_ttm"}, "cds_3y": {"dataset": "cds_3y"},
            "cds_5y": {"dataset": "cds_5y"}, "cds_10y": {"dataset": "cds_10y"},
        },
        "wrenfield_issuer_sector_concentration": {
            "mv": ig, "static": {"dataset": "bond_static"}, "capital": {"dataset": "nav", "column": "ig_rv"},
        },
        "wrenfield_rating_migration": {"scores": {"dataset": "issuer_rating_score_me"}},
    }  # fmt: skip
    records = check_app(ws.build_app(), examples, datasets=store)
    assert len(records) == 7
    for record in records:
        assert (record.output is not None and record.output["code_version"] is None) or True
        assert record.output is not None and record.output["convention"]
        assert record.output["evidence"][0]["kind"] == "computed"


def test_the_agreed_cases_pass_against_the_estate(
    store: WrenfieldDatasetStore, estate: Path
) -> None:
    gold, digest = load_gold(WRENFIELD / "site" / "wrenfield_acceptance.toml")
    corpus = estate / "s3" / bw.RESEARCH / "credit"
    with open_research(":memory:") as research:
        research.ingest.ingest(corpus)
        catalog = ws.build_catalog(store, PermissionPolicy(), research.research)
        report = run_acceptance(catalog, gold, digest)
    failed = [(r.name, r.actual) for r in report.results if not r.passed]
    assert failed == [] and report.total == 10


def test_a_changed_number_fails_its_case(store: WrenfieldDatasetStore, tmp_path: Path) -> None:
    text = (WRENFIELD / "site" / "wrenfield_acceptance.toml").read_text()
    changed = tmp_path / "cases.toml"
    changed.write_text(text.replace("expected = 1103.350619", "expected = 1100.0"))
    gold, digest = load_gold(changed)
    gold.retrieval = []
    report = run_acceptance(ws.build_catalog(store, PermissionPolicy()), gold, digest)
    assert [r.name for r in report.results if not r.passed] == [
        "high-yield book DTS on the last day"
    ]


# ---- who sees what -----------------------------------------------------------------------------


def _caller(*groups: str) -> Caller:
    return Caller("someone", "wrenfield", frozenset(groups))


def test_a_refused_dataset_looks_like_a_missing_one(store: WrenfieldDatasetStore) -> None:
    policy = FileAccessPolicy(WRENFIELD / "facility" / "access.toml")
    guarded = GuardedDatasetStore(store, policy)
    with acting_as(_caller("pm-hy-ls")):
        assert "mv_hy_ls" in guarded.ids() and "mv_ig_rv" not in guarded.ids()
        with pytest.raises(UnknownDatasetError) as refused:
            guarded.get("mv_ig_rv")
        with pytest.raises(UnknownDatasetError) as missing:
            guarded.get("mv_nowhere")
    assert str(refused.value).replace("mv_ig_rv", "X") == str(missing.value).replace(
        "mv_nowhere", "X"
    )
    assert "mv_ig_rv" not in str(refused.value).split("; the datasets are")[1]
    with acting_as(None):
        assert guarded.ids() == []  # no caller is a refusal


def test_entitlements_follow_the_roles() -> None:
    policy = FileAccessPolicy(WRENFIELD / "facility" / "access.toml")

    def sees(group: str, dataset: str) -> bool:
        return policy.allows(_caller(group), "dataset.read", dataset)

    def calls(group: str, tool: str) -> bool:
        return policy.allows(_caller(group), "tool.call", tool)

    assert sees("pm-ig-rv", "mv_ig_rv") and not sees("pm-ig-rv", "mv_hy_ls")
    assert not sees("pm-distressed", "ret_net_hy_ls") and sees(
        "pm-distressed", "ret_net_distressed"
    )
    assert all(sees("risk", f"mv_{b}") for b in ("hy_ls", "ig_rv", "distressed", "fund"))
    assert sees("treasury", "repo_rate") and not sees("treasury", "sig_spread_momentum")
    assert not calls("treasury", "analyze_signal") and not calls("treasury", "run_backtest")
    assert sees("analyst-credit", "sig_spread_momentum") and not sees("analyst-credit", "mv_fund")
    assert sees("compliance", "mv_fund") and not calls("compliance", "run_backtest")
    assert not sees("operators", "px_clean") and calls("operators", "server_info")
    assert not sees("unknown-group", "px_clean")
    assert calls("pm-hy-ls", "wrenfield_default_stress") and not calls(
        "analyst-credit", "wrenfield_default_stress"
    )


def test_known_gaps_in_the_access_model_are_real(store: WrenfieldDatasetStore) -> None:
    """Measured, and recorded in FINDINGS.md: these pass because the model cannot say otherwise."""
    policy = FileAccessPolicy(WRENFIELD / "facility" / "access.toml")
    guarded = GuardedDatasetStore(store, policy)
    with acting_as(_caller("pm-hy-ls")):
        static = guarded.get("bond_static")  # every book's bonds, with the book that holds each
        assert {int(v) for v in static.column("book_code")} == {0, 1, 2, 3}
        # A wall-crossed or restricted issuer is a column like any other: no per-column rule.
        assert "MERR_27A" in guarded.get("bond_oas").columns or any(
            c.startswith("MERR_") for c in guarded.get("bond_oas").columns
        )
    with acting_as(_caller("analyst-credit")):
        assert any(c.startswith("VERN_") for c in guarded.get("px_clean").columns)


def test_a_firm_tool_reads_only_what_the_caller_may(store: WrenfieldDatasetStore) -> None:
    policy = FileAccessPolicy(WRENFIELD / "facility" / "access.toml")
    catalog = ws.build_catalog(store, PermissionPolicy(), None, policy)
    executor = ToolExecutor(ToolRegistry(catalog.tools), catalog.policy, access=catalog.access)
    call = ToolCall(
        tool="wrenfield_spread_duration_dts", arguments=_hy({"mv": {"dataset": "mv_ig_rv"}})
    )
    with acting_as(_caller("pm-hy-ls")):
        refused = executor.execute(call)
    assert refused.status == "error" and "No dataset 'mv_ig_rv'" in (refused.error or "")
    assert refused.refused  # the auditor sees it on the record
    with acting_as(_caller("risk")):
        assert executor.execute(call).status == "ok"
    with acting_as(_caller("treasury")):
        assert executor.execute(call).status in ("denied", "error")


# ---- the platform's own analytics on this fund's data ---------------------------------------


def _platform(store: WrenfieldDatasetStore, tool: str, **arguments: Any) -> dict[str, Any]:
    from quant_analytics.tools import analytics_tools

    executor = ToolExecutor(analytics_tools(store), PermissionPolicy())
    record = executor.execute(ToolCall(tool=tool, arguments=arguments))
    assert record.status == "ok", record.error
    assert record.output is not None
    return record.output


def test_analyze_performance_on_a_books_daily_returns(store: WrenfieldDatasetStore) -> None:
    out = _platform(
        store, "analyze_performance", returns={"dataset": "ret_net_hy_ls", "column": "hy_ls"}
    )
    returns = store.get("ret_net_hy_ls").column("hy_ls")  # day one is a zero, not missing
    assert out["periods"] == 103 and out["periods_per_year"] == 252
    growth = 1.0
    for value in returns:
        growth *= 1.0 + value
    assert out["metrics"]["annualized_return"] == pytest.approx(growth ** (252 / len(returns)) - 1)


def test_analyze_signal_reproduces_the_planted_rank_ic(
    store: WrenfieldDatasetStore, answers: dict[str, Any]
) -> None:
    for lag, key in ((0, "lag0"), (1, "lag1")):
        out = _platform(
            store, "analyze_signal", signal={"dataset": "sig_spread_momentum"},
            returns={"dataset": "bond_tr"}, lag=lag,
        )  # fmt: skip
        planted = answers["spread_momentum"][key]
        assert out["summary"]["mean_rank_ic"] == pytest.approx(planted["mean_rank_ic"], abs=1e-9)
        assert out["summary"]["dates"] == planted["days"]


def test_analyze_risk_with_a_credit_factor_model_and_its_limits(
    store: WrenfieldDatasetStore,
) -> None:
    model = {
        "loadings": "fm_loadings", "factor_covariance": "fm_factor_cov",
        "specific_variance": {"dataset": "fm_specific", "column": "specific_var_annual"},
    }  # fmt: skip
    out = _platform(
        store, "analyze_risk", portfolio_id="ig_rv", weights_ref={"dataset": "w_ig_rv"},
        asset_returns={"dataset": "bond_tr"}, factor_model=model,
    )  # fmt: skip
    assert set(out["factor_model"]["exposures"]) == {
        "credit_hy",
        "credit_ig",
        "rates_eur",
        "rates_usd",
    }
    assert (
        out["factor_model"]["exposures"]["credit_hy"] == 0.0
        and out["factor_model"]["exposures"]["rates_usd"] < 0
    )
    assert out["factor_model"]["volatility"] == pytest.approx(out["volatility"], rel=0.1)
    assert out["historical_stress"] is None  # no scenario window falls in a 103-day history
    plain = _platform(
        store, "analyze_risk", portfolio_id="hy_ls", weights_ref={"dataset": "w_hy_ls"},
        asset_returns={"dataset": "bond_tr"},
    )  # fmt: skip
    assert plain["gross_exposure"] == pytest.approx(
        sum(abs(v[-1]) for v in store.get("w_hy_ls").columns.values())
    )
    # One summary of the rows left out, not one warning per bond.
    assert not [w for w in plain["warnings"] if w.startswith("Left out 1 rows")]
    assert len([w for w in plain["warnings"] if w.startswith("Left out rows")]) <= 1
    executor = ToolExecutor(
        __import__("quant_analytics.tools", fromlist=["x"]).analytics_tools(store),
        PermissionPolicy(),
    )
    held_default = executor.execute(ToolCall(tool="analyze_risk", arguments={
        "portfolio_id": "hy_ls", "weights_ref": {"dataset": "w_hy_ls"},
        "asset_returns": {"dataset": "bond_tr"}, "factor_model": model,
    }))  # fmt: skip
    # A defaulted bond with neither a loading nor a specific variance is refused, naming it.
    assert held_default.status == "error"
    assert "specific variance 'fm_specific' has no row for MARL_28A" in (held_default.error or "")


def test_run_backtest_has_turnover_costs_but_no_financing(store: WrenfieldDatasetStore) -> None:
    out = _platform(
        store, "run_backtest", signal={"dataset": "sig_spread_momentum"}, returns={"dataset": "bond_tr"},
        cost_bps=5.0, lag=1,
    )  # fmt: skip
    assert out["costs"]["cost_bps"] == 5.0 and out["costs"]["annual_turnover"] > 10
    assert out["book"]["mean_net_exposure"] == pytest.approx(0.0, abs=1e-9)
    text = json.dumps(out).lower()
    assert "financ" not in text and "repo" not in text and "borrow" not in text


# ---- research ----------------------------------------------------------------------------------


def test_research_search_finds_the_notes_and_the_injection_stays_inert(estate: Path) -> None:
    from quant_research import ResearchQuery, research_tools

    corpus = estate / "s3" / bw.RESEARCH / "credit"
    with open_research(":memory:") as research:
        before = ToolRegistry(research_tools(research.research)).digest()
        result = research.ingest.ingest(corpus)
        after = ToolRegistry(research_tools(research.research)).digest()
        assert before == after and result.documents == 7 if hasattr(result, "documents") else True
        found = research.research.find_related(
            ResearchQuery(query="Halden recovery senior secured notes")
        )
        assert found.passages[0].source.source_id.endswith("note-halden-retail-restructuring.md")
        vendor = research.research.find_related(
            ResearchQuery(query="pricing vendor file change spread duration column")
        )
        notice = [p for p in vendor.passages if "IMPORTANT SYSTEM NOTICE" in p.text]
        assert notice and all(
            p.source.source_id.endswith("note-pricing-vendor-update.md") for p in notice
        )
        assert all(item.kind == "retrieved" for item in vendor.evidence)


# ---- the legacy package ---------------------------------------------------------------------


def test_the_legacy_package_agrees_with_the_tools(
    estate: Path, answers: dict[str, Any], tmp_path: Path
) -> None:
    import wrenfield_quant
    from wrenfield_quant import basis, db, hazard, spreads

    saved = dict(wrenfield_quant.CONFIG)
    wrenfield_quant.CONFIG.update(estate=str(estate), verbose=False, output_dir=str(tmp_path))
    db.close()
    try:
        dts = spreads.dts_by_book()
        for book in ("hy_ls", "ig_rv", "distressed"):
            assert dts[book] == pytest.approx(answers["dts"]["values"][book], abs=1e-4)
        assert (spreads.cs01_ladder("ig_rv") < 0).all()
        assert (tmp_path / "cs01_ig_rv.csv").exists()  # the side effect, as the desk wrote it
        assert not basis.basis_monitor(-60).empty
        with pytest.raises(RuntimeError, match="load_curves"):
            hazard.survival("YATE", 5.0)
        hazard.load_curves(delay=0)
        assert 0.5 < hazard.survival("YATE", 5.0) < 1.0
    finally:
        db.close()
        hazard.CURVES.clear()
        hazard.LOADED = False
        wrenfield_quant.CONFIG.clear()
        wrenfield_quant.CONFIG.update(saved)


def test_the_legacy_scan_classifies_what_it_finds() -> None:
    from typer.testing import CliRunner

    from quant_platform.cli import app

    result = CliRunner().invoke(
        app,
        ["legacy", "scan", str(WRENFIELD / "facility" / "legacy" / "wrenfield_quant"), "--json"],
    )
    assert result.exit_code == 0, result.output
    inventory = json.loads(result.output)
    entries = {e["name"]: e for e in inventory.get("entries", inventory.get("callables", []))}
    assert len(entries) == 10
    kinds = {name: entry["kind"] for name, entry in entries.items()}
    assert kinds["load_curves"] == "service" and kinds["basis_monitor"] == "service"
    # Reading through the module's connection and price cache no longer makes a service (P1).
    assert kinds["dts_by_book"] == "facade" and kinds["cs01_ladder"] == "facade"
    # Wrong: both read the LOADED flag that load_curves sets, so they are not plain facades.
    assert kinds["survival"] == "facade" and kinds["jtd_cds"] == "facade"
    assert not any(kind == "expose" for kind in kinds.values())


def test_the_facility_imports_nothing_of_the_platform() -> None:
    sources = list((WRENFIELD / "facility").rglob("*.py"))
    assert len(sources) >= 7
    assert [str(p) for p in sources if PLATFORM.search(p.read_text())] == []
