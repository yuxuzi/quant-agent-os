"""The returns section (a fund, its benchmarks, and its excess by period) and the PDF renderer."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pytest

from quant_toolbox import MissingExtraError
from quant_toolbox.data import demo_returns
from quant_toolbox.performance import horizon_return
from quant_toolbox.report import (
    RETURN_PERIODS,
    ChartBlock,
    Document,
    TableBlock,
    TextBlock,
    bar_chart,
    returns_table,
    tear_sheet,
)


def _books() -> tuple[list[dt.date], dict[str, np.ndarray]]:
    table = demo_returns(800)
    return table.dates, {name: table.column(name) for name in ("Macro", "Index", "Stat Arb")}


def test_returns_table_has_a_row_per_series_and_an_excess_row_per_benchmark() -> None:
    dates, books = _books()
    block = returns_table(dates, books, 252, excess_over="Macro")
    assert [column.name for column in block.columns] == ["Series", *RETURN_PERIODS]
    assert [row[0] for row in block.rows] == [
        "Macro",
        "Index",
        "Stat Arb",
        "Macro over Index",
        "Macro over Stat Arb",
    ]
    macro, index, excess = block.rows[0], block.rows[1], block.rows[3]
    assert macro[1] == pytest.approx(float(books["Macro"][-1]))  # 1D: the last date's return
    assert macro[4] == horizon_return(dates, books["Macro"], "YTD", 252)
    for got, mine, theirs in zip(excess[1:], macro[1:], index[1:], strict=True):
        if mine is None or theirs is None:
            assert got is None
        else:
            assert got == pytest.approx(mine - theirs)  # type: ignore[operator]
    assert macro[RETURN_PERIODS.index("5Y") + 1] is None  # about three years of data


def test_returns_table_periods_are_chosen_and_checked() -> None:
    dates, books = _books()
    block = returns_table(dates, books, 252, periods=("1D", "MTD", "QTD"))
    assert [column.name for column in block.columns] == ["Series", "1D", "MTD", "QTD"]
    with pytest.raises(ValueError, match="'1W' is not a period"):
        returns_table(dates, books, 252, periods=("1W",))
    with pytest.raises(ValueError, match="No series 'Fund'"):
        returns_table(dates, books, 252, excess_over="Fund")
    with pytest.raises(ValueError, match="has 3 returns"):
        returns_table(dates, {"Short": np.zeros(3)}, 252)


def test_the_tear_sheet_compares_the_fund_with_its_benchmark_and_more() -> None:
    dates, books = _books()
    document = tear_sheet(
        dates,
        books["Macro"],
        252,
        name="Macro",
        benchmark=books["Index"],
        benchmark_name="Index",
        compare={"Peer": books["Stat Arb"]},
        periods=("1D", "MTD", "YTD", "ITD"),
    )
    returns = next(block for block in document.blocks if block.title == "Returns by horizon")
    assert isinstance(returns, TableBlock)
    assert [row[0] for row in returns.rows] == ["Macro", "Index", "Peer", "Macro over Index",
                                               "Macro over Peer"]  # fmt: skip
    with pytest.raises(ValueError, match="names two series"):
        tear_sheet(dates, books["Macro"], 252, benchmark=books["Index"],
                   compare={"Benchmark": books["Index"]})  # fmt: skip


def test_a_fund_alone_has_no_excess_rows() -> None:
    dates, books = _books()
    document = tear_sheet(dates, books["Macro"], 252, name="Macro")
    returns = next(block for block in document.blocks if block.title == "Returns by horizon")
    assert [row[0] for row in returns.rows] == ["Macro"]  # type: ignore[union-attr]


def test_pdf_without_its_extra_says_how_to_install_it(monkeypatch: pytest.MonkeyPatch) -> None:
    import quant_toolbox._optional as optional
    from quant_toolbox.report import pdf

    real = optional.importlib.import_module

    def missing(name: str, *args: object) -> object:
        if name == "fpdf":
            raise ImportError("No module named 'fpdf'")
        return real(name, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(optional.importlib, "import_module", missing)
    with pytest.raises(MissingExtraError, match=r'uv add "quant-toolbox\[pdf\]"'):
        pdf.render_pdf(Document("Report"))


def test_a_tear_sheet_renders_as_a_pdf(tmp_path: Path) -> None:
    pytest.importorskip("fpdf")
    from quant_toolbox.report.pdf import render_pdf, write_pdf

    dates, books = _books()
    document = tear_sheet(dates, books["Macro"], 252, benchmark=books["Index"])
    data = render_pdf(document)
    assert data.startswith(b"%PDF-") and data.rstrip().endswith(b"%%EOF")
    assert b"/Type /Page" in data
    path = write_pdf(document, tmp_path / "tear.pdf")
    assert path.read_bytes()[:5] == b"%PDF-"


def test_every_kind_of_block_and_awkward_text_renders() -> None:
    pytest.importorskip("fpdf")
    from quant_toolbox.report.pdf import render_pdf

    days = [dt.date(2024, 1, day) for day in range(1, 6)]
    document = Document(
        "".join(map(chr, (70, 0x2014, 0x201C, 113, 0x201D, 32, 0x2212, 0x4E2D))),
        subtitle="Gross " + chr(0x2013) + " net",
        blocks=[
            ChartBlock("Gappy line", days, {"A": [1.0, float("nan"), 2.0, 3.0, 2.5]}),
            ChartBlock("Empty", days, {"A": [float("nan")] * 5}),
            ChartBlock("Flat", days, {"A": [1.0] * 5}, percent=True),
            bar_chart("Bars", ["Q1", "Q2"], {"A": [0.02, -0.01], "B": [0.01, 0.03]}, percent=True),
            TableBlock("Empty table", [], []),
            TextBlock("Notes", ["A note " + chr(0x2026)]),
        ],
    )
    assert render_pdf(document).startswith(b"%PDF-")
