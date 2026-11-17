"""Tables, workbooks, configuration, checks, run folders, and reports: known answers."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import re
from pathlib import Path

import numpy as np
import pytest

from quant_toolbox import audit, data, report, risk
from quant_toolbox.data import (
    ConfigError,
    Table,
    TableError,
    dump_config,
    load_config,
    load_csv,
    read_meta,
    save_table,
)
from quant_toolbox.performance import expected_shortfall, value_at_risk
from quant_toolbox.report import Column, Document, TableBlock

DATES = [dt.date(2024, 1, 31), dt.date(2024, 2, 29), dt.date(2024, 3, 31)]


def _table(**columns: list[float]) -> Table:
    return Table(DATES, list(columns), np.column_stack(list(columns.values())))


# Tables


def test_a_saved_table_reads_back_with_its_provenance(tmp_path: Path) -> None:
    table = _table(net=[0.01, np.nan, -0.02])

    path = save_table(tmp_path / "r.csv", table, made_by="test", inputs={"prices": "sha256:x"})

    assert load_csv(path).fingerprint() == table.fingerprint()
    assert read_meta(path)["inputs"] == {"prices": "sha256:x"}
    assert read_meta(path)["fingerprint"] == f"sha256:{table.fingerprint()}"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("date,A\n2024-02-01,0.1\n2024-01-01,0.1\n", "ascending"),
        ("date,A,A\n2024-02-01,0.1,0.1\n", "unique"),
        ("date,A\n01/31/2024,0.1\n", "not an ISO date"),
        ("date,A\n2024-01-31,abc\n", "line 2: 'abc' is not a number"),
    ],
)
def test_bad_files_say_what_to_fix(tmp_path: Path, text: str, message: str) -> None:
    path = tmp_path / "bad.csv"
    path.write_text(text, "utf-8")

    with pytest.raises(TableError, match=message):
        load_csv(path)


def test_a_panel_refuses_non_positive_prices_and_aligns_other_files() -> None:
    with pytest.raises(TableError, match="positive"):
        data.Panel(DATES, ["A"], np.array([[1.0], [0.0], [2.0]]))
    panel = data.Panel(DATES, ["A", "B"], np.ones((3, 2)))
    signal = Table(DATES[1:], ["B", "C"], np.array([[5.0, 9.0], [6.0, 9.0]]))

    values = data.aligned(signal, panel, "signal")

    assert np.isnan(values[0]).all() and np.isnan(values[:, 0]).all()
    assert values[1:, 1].tolist() == [5.0, 6.0]


# Checks and configuration


def test_checks_warn_about_short_samples_percentages_gaps_and_outliers() -> None:
    assert data.short_sample("fund", 5) and not data.short_sample("fund", 12)
    assert data.percent_like("fund", [1.2, -0.8, 2.0]) and not data.percent_like("f", [0.01])
    notes = data.return_notes(_table(A=[0.01, np.nan, 0.9], B=[np.nan, 0.01, np.nan]))

    assert any("1 missing returns" in note for note in notes)
    assert any("moves beyond 50%" in note for note in notes)
    assert any("B starts 2024-02-29" in note for note in notes)
    assert any("B ends 2024-02-29" in note for note in notes)


@dataclasses.dataclass(frozen=True)
class _Settings:
    name: str = "x"
    lookback: int = 5
    cost_bps: float = 1.0
    lookbacks: tuple[int, ...] | None = None
    data: str | None = None


def test_a_config_file_reads_into_a_dataclass_and_writes_back(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text('lookback = 7\ncost_bps = 2\nlookbacks = [3, 4]\nname = "y"\n')

    settings = load_config(path, _Settings)

    assert settings == _Settings(name="y", lookback=7, cost_bps=2, lookbacks=(3, 4))
    path.write_text(dump_config(settings, "written"))
    assert load_config(path, _Settings) == settings


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("lookbak = 5\n", "unknown keys lookbak"),
        ('lookback = "5"\n', "lookback must be"),
        ("lookback = true\n", "lookback must be"),
        ("lookbacks = [1.5]\n", "lookbacks must be"),
        ("name = \n", "Cannot read"),
    ],
)
def test_a_bad_config_names_the_problem(tmp_path: Path, text: str, message: str) -> None:
    path = tmp_path / "c.toml"
    path.write_text(text)

    with pytest.raises(ConfigError, match=message):
        load_config(path, _Settings)


# Workbooks


def test_a_workbook_with_title_rows_and_totals_reads_correctly(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    from quant_toolbox.data.excel import read_workbook

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Returns"
    for row in (["Statement"], [], ["Date", "A", "B"], [dt.datetime(2024, 1, 31), 0.01, None]):
        sheet.append(row)
    sheet.append([dt.date(2024, 2, 29), 0.02, 0.03])
    sheet.append([None, "Total", 0.03])
    path = tmp_path / "admin.xlsx"
    workbook.save(path)

    table = read_workbook(path, sheet="Returns", header_row=3)

    assert table.dates == DATES[:2] and table.columns == ["A", "B"]
    assert np.isnan(table.values[0, 1])
    with pytest.raises(TableError, match="no sheet 'Nope'"):
        read_workbook(path, sheet="Nope")
    with pytest.raises(TableError, match="Set header_row"):
        read_workbook(path)


def test_written_workbooks_hold_numbers_and_never_formulas(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    from quant_toolbox.report.excel import write_workbook

    document = Document(
        "t",
        blocks=[
            TableBlock(
                "Returns: by/book",
                [Column("Book", "text"), Column("YTD")],
                [["=HYPERLINK(1)", 0.05], ["A", None]],
                note="@note",
            )
        ],
    )

    sheet = openpyxl.load_workbook(write_workbook(document, tmp_path / "o.xlsx")).worksheets[1]

    assert sheet["A2"].value == "'=HYPERLINK(1)" and sheet["A2"].data_type == "s"
    assert sheet["B2"].value == pytest.approx(0.05) and sheet["B2"].number_format == "0.00%"
    assert sheet["A5"].value == "'@note"


def test_tables_convert_to_and_from_pandas() -> None:
    pytest.importorskip("pandas")
    from quant_toolbox.data.frames import from_pandas, to_pandas

    table = _table(A=[0.01, np.nan, 0.02])

    assert from_pandas(to_pandas(table)).fingerprint() == table.fingerprint()


# Risk


def test_value_at_risk_shortfall_and_book_measures_have_known_answers() -> None:
    returns = np.array([-0.05, -0.03, -0.01, 0.01, 0.02])
    book = risk.exposures(np.array([0.5, -0.25, 0.25, np.nan]))

    assert value_at_risk(returns, 0.25) == pytest.approx(-0.03)
    assert expected_shortfall(returns, 0.25) == pytest.approx(-0.04)
    assert (book.gross, book.net, book.long, book.short) == (1.0, 0.5, 0.75, -0.25)
    assert book.effective_positions == pytest.approx(1 / (0.25 + 0.0625 + 0.0625))
    assert risk.stress_return(np.array([0.5, -0.5]), np.array([-0.1, -0.2])) == pytest.approx(0.05)


def test_risk_shares_sum_to_one_and_are_zero_for_an_exact_hedge() -> None:
    returns = np.random.default_rng(3).normal(0, 0.01, (200, 3))
    hedged = np.column_stack([returns[:, 0], returns[:, 0]])

    assert risk.risk_shares(np.array([0.5, 0.3, -0.2]), returns).sum() == pytest.approx(1.0)
    assert risk.risk_shares(np.array([1.0, -1.0]), hedged).tolist() == [0.0, 0.0]


# Reports


def test_html_and_markdown_escape_names_and_show_undefined_as_na() -> None:
    document = Document(
        "<script>x</script>",
        blocks=[TableBlock("T", [Column("<b>", "text"), Column("v")], [["a|z", None]])],
    )

    html = report.render_html(document)
    assert "<script>x" not in html and "<b>" not in html
    assert "\\|z" in report.render_markdown(document)
    assert "n/a" in report.render_markdown(document)


def test_static_charts_hold_no_script_and_no_link() -> None:
    pytest.importorskip("pygal")
    days = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(60)]
    returns = {"A": np.random.default_rng(1).normal(0, 0.01, 60)}
    returns["A"][10] = np.nan
    document = Document(
        "t",
        blocks=[
            report.equity_curve(days, returns),
            report.underwater_chart(days, returns),
            report.bar_chart("w", ["x", "y"], {"w": [0.2, -0.1]}, percent=True),
        ],
    )

    html = report.render_html(document)

    assert html.count("<svg") == 3
    assert "<script" not in html
    assert set(re.findall(r"(?:https?|file)://[^\s\"'<)]+", html)) == {"http://www.w3.org/2000/svg"}


def test_interactive_charts_embed_plotly_once() -> None:
    pytest.importorskip("plotly")
    days = [dt.date(2024, 1, 1), dt.date(2024, 1, 2), dt.date(2024, 1, 3)]
    chart = report.line_chart("x", days, {"a": [1.0, float("nan"), 2.0]})
    document = Document("t", blocks=[chart, chart])

    inline = report.render_html(document, charts="interactive")
    online = report.render_html(document, charts="interactive-cdn")

    assert inline.count("plotly.js v") == 1 or len(inline) > 1_000_000
    assert "cdn.plot.ly" in online and len(online) < 200_000
    with pytest.raises(ValueError, match="charts must be one of"):
        report.render_html(document, charts="animated")


def test_a_chart_needs_one_value_per_point() -> None:
    with pytest.raises(ValueError, match="2 values for 3 points"):
        report.line_chart("x", DATES, {"a": [1.0, 2.0]})


def test_charts_are_data_in_the_json_form() -> None:
    chart = report.bar_chart("w", ["x"], {"w": [float("nan")]})

    block = report.to_dict(Document("t", blocks=[chart]))["blocks"][0]

    assert block == {
        "type": "chart",
        "title": "w",
        "dates": [],
        "series": {"w": [None]},
        "kind": "bar",
        "labels": ["x"],
    }


@pytest.mark.parametrize(
    ("name", "value", "shown"),
    [
        ("annualized_return", 0.1234, "12.34%"),
        ("sharpe_ratio", 1.23456, "1.2346"),
        ("bars", 250.0, "250"),
        ("unknown", float("nan"), "n/a"),
        ("signal", "momentum", "momentum"),
    ],
)
def test_metrics_format_by_kind(name: str, value: object, shown: str) -> None:
    assert report.format_metric(name, value) == shown


def test_a_run_report_and_a_comparison_render_from_results() -> None:
    results = {
        "kind": "backtest",
        "title": "demo",
        "period": ["2024-01-01", "2024-01-03"],
        "metrics": {"sharpe_ratio": 0.5, "annualized_return": 0.1},
        "series": {"dates": ["2024-01-01", "2024-12-31"], "net_returns": [0.0, 0.1]},
    }

    text = report.run_markdown(results, "equity.svg")
    both = report.comparison_markdown([("a", results), ("b", results)])
    chart = report.equity_chart([("a", results)])

    assert "| sharpe ratio | 0.5000 |" in text and "| 2024 | 10.00% |" in text
    assert "![Equity curve](equity.svg)" in text and "Comparison of 2 runs" in both
    assert chart is not None and chart.series["a"] == pytest.approx([1.0, 1.1])


# Audit


def test_run_folders_are_never_reused_and_results_are_json_safe(tmp_path: Path) -> None:
    now = dt.datetime(2024, 5, 1, 12, 0, tzinfo=dt.UTC)
    name = audit.run_name("My Backtest!", now)

    first, second = audit.new_run_folder(tmp_path, name), audit.new_run_folder(tmp_path, name)
    audit.write_json(second / audit.RESULTS_FILE, {"x": float("nan"), "y": (1.0, float("inf"))})

    assert name == "20240501T120000Z-my-backtest" and second.name == f"{name}-2"
    assert first.is_dir()
    assert json.loads((second / "results.json").read_text()) == {"x": None, "y": [1.0, None]}
    assert audit.latest_run(tmp_path) == second
    with pytest.raises(ValueError, match="run study first"):
        audit.latest_run(tmp_path / "none", hint="run study first")


def test_provenance_names_inputs_code_and_time() -> None:
    found = audit.provenance(
        version="1.2.3",
        where=Path(__file__),
        fingerprints={"data_fingerprint": "abc"},
        now=dt.datetime(2024, 5, 1, tzinfo=dt.UTC),
        source="demo",
    )

    assert found["created"] == "2024-05-01T00:00:00+00:00"
    assert found["data_fingerprint"] == "sha256:abc" and found["source"] == "demo"
    assert found["code_version"]["package"] == "1.2.3"


def test_group_exposures_sum_net_and_gross_and_sort_by_gross() -> None:
    assets = ["A", "B", "C", "D"]
    weights = np.array([0.3, -0.2, 0.1, 0.0])

    found = risk.group_exposures(assets, weights, {"A": "Tech", "B": "Tech", "C": "Energy"})

    assert list(found) == ["Tech", "Energy"]
    assert found["Tech"] == pytest.approx((0.1, 0.5))
    assert found["Energy"] == pytest.approx((0.1, 0.1))
    assert risk.group_exposures(["X"], np.array([0.4]), {}) == {risk.UNGROUPED: (0.4, 0.4)}


def test_simulated_history_applies_todays_weights_and_treats_gaps_as_flat() -> None:
    returns = np.array([[0.01, 0.02], [np.nan, -0.01], [0.03, 0.0]])

    history = risk.simulated_returns(np.array([0.5, -0.5]), returns)

    assert history.tolist() == pytest.approx([-0.005, 0.005, 0.015])


def test_concentration_measures_of_a_book() -> None:
    book = risk.exposures(np.array([0.4, 0.3, -0.2, 0.05, 0.03, 0.02]))

    assert book.largest == pytest.approx(0.4)
    assert book.top_five == pytest.approx(0.98)
    assert book.positions == 6
    assert risk.exposures(np.zeros(3)).effective_positions == 0.0
