"""examples/portfolio-intelligence: the demonstration, step by step, over real HTTP and MCP.

Its figures are checked against an independent numpy calculation from the fixture's files.
"""

import csv
import re
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import demo as demo_module
import httpx
import numpy as np
import pytest
from book_tools import BookVarInput, firm_tools, nearest_rank_var
from demo import _wait_until_healthy, connect_instructions, prepare, run_demo

from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.testing import check_tool

pytest.importorskip("empyrical")

DATA = Path(demo_module.__file__).parent / "data"
FACTORS = ["market", "size", "value", "momentum"]


def table(name: str) -> dict[str, np.ndarray]:
    """Return a fixture file's numeric columns, read without any platform code."""
    with (DATA / f"{name}.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {column: np.array([float(row[column]) for row in rows]) for column in list(rows[0])[1:]}


def weights(book: str) -> dict[str, float]:
    """Return a book's weights, read without any platform code."""
    with (DATA / f"{book}-weights.csv").open(newline="") as handle:
        return {row["asset"]: float(row["weight"]) for row in csv.DictReader(handle)}


@pytest.fixture(scope="module")
def walkthrough() -> tuple[dict[str, Any], str]:
    """Run the whole demonstration once, and return what it saw and what it printed."""
    lines: list[str] = []
    seen = run_demo(lines.append)
    return seen, "\n".join(lines)


@pytest.mark.parametrize("book", ["growth", "value"])
def test_each_books_returns_are_its_weights_times_its_assets(book: str) -> None:
    assets = table(f"{book}-assets")
    position = weights(book)

    assert sum(position.values()) == pytest.approx(1.0)
    expected = sum(position[name] * assets[name] for name in position)
    assert table(f"{book}-book")["book"] == pytest.approx(expected, abs=1e-6)


def test_the_var_is_the_nearest_rank_loss() -> None:
    returns = [-0.10, -0.05, *[0.01] * 18]  # 5% of 20 periods is rank 1

    assert nearest_rank_var(returns, 0.95) == pytest.approx(0.10)
    assert nearest_rank_var(returns, 0.90) == pytest.approx(0.05)
    assert nearest_rank_var([0.01, 0.02], 0.95) == 0.0  # no loss is no VaR, not a negative one


def test_the_firms_tool_honours_the_platform_contract(tmp_path: Path) -> None:
    store = DirectoryDatasetStore(tmp_path)
    for path in DATA.glob("*.csv"):
        store.put(path.stem, path)
    book_var = firm_tools(store)[0]

    assert check_tool(book_var, {"book": "growth"}).status == "ok"
    assert book_var.function(BookVarInput(book="growth")).periods == 36


def test_step_1_a_request_without_a_key_is_refused(walkthrough: tuple[dict[str, Any], str]) -> None:
    assert walkthrough[0]["no_key"] == 401


def test_step_2_each_caller_discovers_only_its_own(walkthrough: tuple[dict[str, Any], str]) -> None:
    seen, _ = walkthrough

    assert {"book_var", "analyze_risk", "research_report"} <= set(seen["growth_tools"])
    assert seen["value_tools"] == [
        "analyze_performance",
        "find_related_signals",
        "get_source_passage",
        "list_datasets",
    ]
    assert seen["growth_datasets"] == [
        "factors",
        "growth-assets",
        "growth-book",
        "growth-weights",
    ]


def test_step_3_the_figures_match_an_independent_calculation(
    walkthrough: tuple[dict[str, Any], str],
) -> None:
    seen, _ = walkthrough
    position = weights("growth")
    names = list(position)
    assets = table("growth-assets")
    returns = np.column_stack([assets[name] for name in names])
    w = np.array([position[name] for name in names])
    book = returns @ w

    # Rank ceil(5% of 36) = 2: the second-worst month.
    assert seen["var"]["var"] == pytest.approx(-np.sort(book)[1])
    assert seen["var"]["weights"] == position
    volatility = np.sqrt(w @ np.cov(returns, rowvar=False, ddof=1) @ w * 12)
    assert seen["risk"]["volatility"] == pytest.approx(volatility)
    assert seen["risk"]["volatility"] == pytest.approx(np.std(book, ddof=1) * np.sqrt(12))
    factors = table("factors")
    design = np.column_stack([np.ones(len(book)), *[factors[name] for name in FACTORS]])
    betas = np.linalg.lstsq(design, book, rcond=None)[0][1:]
    exposures = seen["risk"]["factors"]["exposures"]
    assert [exposures[name] for name in FACTORS] == pytest.approx(list(betas))
    assert seen["risk"]["data_versions"].keys() == {"dataset:growth-assets", "dataset:factors"}


def test_step_4_research_is_cited_and_marked_untrusted(
    walkthrough: tuple[dict[str, Any], str],
) -> None:
    found = walkthrough[0]["research"]

    assert found["passages"][0]["source"]["source_id"] == "momentum-crash-risk.md"
    assert "lines" in found["passages"][0]["source"]["location"]
    assert found["trust"] == "untrusted"


def test_step_5_the_report_links_computed_and_retrieved_evidence(
    walkthrough: tuple[dict[str, Any], str],
) -> None:
    report = walkthrough[0]["report"]
    kinds = {item["kind"] for item in report["evidence"]}

    assert {"computed", "retrieved"} <= kinds
    assert [step["status"] for step in report["steps"]] == ["ok", "ok"]
    assert "dataset:growth-book" in report["data_versions"]
    book = table("growth-book")["book"]
    wealth = np.cumprod(1 + book)
    drawdown = float(np.min(wealth / np.maximum.accumulate(np.r_[1.0, wealth])[1:] - 1))
    assert report["metrics"]["max_drawdown"] == pytest.approx(drawdown)


def test_step_6_another_desks_data_looks_missing_and_its_tool_is_denied(
    walkthrough: tuple[dict[str, Any], str],
) -> None:
    seen, _ = walkthrough

    assert seen["refused_dataset"]["is_error"]
    assert seen["refused_dataset"]["status"] == "error"
    message = seen["refused_dataset"]["error"]
    assert "No dataset 'value-book'" in message
    assert "value-assets" not in message and "value-weights" not in message
    assert "refused" not in seen["refused_dataset"]
    through_firm_tool = seen["refused_by_firm_tool"]
    assert through_firm_tool["is_error"]
    assert "No dataset 'value-weights'" in through_firm_tool["error"]
    assert seen["denied_tool"]["status"] == "denied"


def test_step_7_a_question_outside_the_research_abstains(
    walkthrough: tuple[dict[str, Any], str],
) -> None:
    abstained = walkthrough[0]["abstained"]

    assert not abstained["is_error"]
    assert abstained["passages"] == []
    assert "Nothing in the corpus matched" in abstained["warnings"][-1]


def test_step_8_every_call_is_on_record_under_its_caller(
    walkthrough: tuple[dict[str, Any], str],
) -> None:
    audit = walkthrough[0]["audit"]

    assert [call["tool"] for call in audit] == [
        "list_datasets",
        "book_var",
        "analyze_risk",
        "find_related_signals",
        "research_report",
        "analyze_performance",
        "book_var",
        "book_var",
        "find_related_signals",
    ]
    assert all(re.fullmatch("[0-9a-f]{64}", call["input"]) for call in audit)
    assert all(call["version"] for call in audit)
    read, through_firm_tool, called = audit[5:8]
    assert (read["principal"], read["refused"]) == ("growth-pm", ["dataset.read value-book"])
    assert through_firm_tool["principal"] == "growth-pm"
    assert through_firm_tool["refused"] == ["dataset.read value-weights"]
    assert (called["principal"], called["refused"]) == ("value-analyst", ["tool.call book_var"])
    assert called["status"] == "denied"
    assert audit[1]["data_versions"].keys() == {"dataset:growth-weights", "dataset:growth-assets"}


def test_it_prints_every_step(walkthrough: tuple[dict[str, Any], str]) -> None:
    printed = walkthrough[1]

    for number in range(1, 9):
        assert f"[{number}/8]" in printed
    assert printed.isascii()  # a Windows console may not encode anything else


def test_the_guide_quotes_the_figures_the_tools_return(
    walkthrough: tuple[dict[str, Any], str],
) -> None:
    seen, _ = walkthrough
    guide = (Path(demo_module.__file__).parents[2] / "docs" / "demo.md").read_text("utf-8")

    assert f"VaR {seen['var']['var']:.2%}" in guide
    assert f"volatility {seen['risk']['volatility']:.2%}" in guide
    assert f"momentum exposure {seen['risk']['factors']['exposures']['momentum']:+.2f}" in guide
    assert f"Sharpe ratio of {seen['report']['metrics']['sharpe_ratio']:.2f}" in guide


def test_each_caller_gets_its_own_key_and_the_instructions_name_it(tmp_path: Path) -> None:
    keys = prepare(tmp_path)

    assert len({keys.growth, keys.value, keys.auditor}) == 3
    instructions = connect_instructions(keys, 8000)
    assert f"Bearer {keys.growth}" in instructions
    assert "claude mcp add --transport http" in instructions


def test_serve_mode_prints_how_to_connect_and_serves_that_key(tmp_path: Path) -> None:
    home = tmp_path / "home"
    log = tmp_path / "server.log"
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    command = [sys.executable, str(demo_module.__file__), "--serve"]
    command += ["--home", str(home), "--port", str(port)]
    with log.open("wb") as output:
        server = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT)
        try:
            _wait_until_healthy(url, server, log)
            match = re.search(r"Bearer (qaos_\S+?)\"", log.read_text("utf-8"))
            assert match is not None
            tools = httpx.get(
                f"{url}/v1/tools", headers={"Authorization": f"Bearer {match[1]}"}, timeout=10
            )
        finally:
            server.terminate()
            server.wait(timeout=30)

    assert "book_var" in tools.text


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
