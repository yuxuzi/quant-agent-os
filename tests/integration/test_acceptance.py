"""`qaos eval`: agreed cases against the served tools, on the demonstration and sample (S5.1)."""

import csv
import json
import math
import re
import tomllib
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from quant_agent_os.data import DirectoryDatasetStore
from quant_platform.acceptance import GoldFileError, load_gold
from quant_platform.cli import app
from quant_research import open_research

pytest.importorskip("empyrical")

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "examples" / "portfolio-intelligence"
GOLD = DEMO / "acceptance.toml"
SAMPLE = ROOT / "src" / "quant_platform" / "sample"
RUNNER = CliRunner()


def _prepare(home: Path, corpus: Path, data: dict[str, Path]) -> list[str]:
    store = DirectoryDatasetStore(home / "datasets")
    for name, path in data.items():
        store.put(name, path)
    with open_research(home / "kb.db") as research:
        research.ingest.ingest(corpus)
    return ["--store", str(home / "kb.db"), "--datasets", str(home / "datasets")]


@pytest.fixture
def demo(tmp_path: Path) -> list[str]:
    data = {path.stem: path for path in (DEMO / "data").glob("*.csv")}
    return _prepare(tmp_path, DEMO / "notes", data)


def _evaluate(gold: Path, where: list[str], *extra: str) -> tuple[int, str]:
    result = RUNNER.invoke(app, ["eval", "--gold", str(gold), *where, *extra])
    return result.exit_code, result.stdout


def _edited(tmp_path: Path, old: str, new: str) -> Path:
    text = GOLD.read_text("utf-8")
    assert old in text
    edited = tmp_path / "edited.toml"
    edited.write_text(text.replace(old, new), "utf-8")
    return edited


def test_the_demonstrations_cases_pass(demo: list[str]) -> None:
    code, report = _evaluate(GOLD, demo)

    assert code == 0, report
    assert report.startswith("# Acceptance: PASS")
    assert "4 of 4 cases passed" in report


def test_the_expected_numbers_come_from_a_separate_calculation() -> None:
    for case in tomllib.loads(GOLD.read_text("utf-8"))["numeric"]:
        reference = case["arguments"]["returns"]
        with (DEMO / "data" / f"{reference['dataset']}.csv").open() as file:
            returns = [float(row[reference["column"]]) for row in csv.DictReader(file)]
        assert case["expected"] == pytest.approx(np.std(returns, ddof=1) * math.sqrt(12), abs=1e-12)


def test_a_wrong_expected_value_fails(demo: list[str], tmp_path: Path) -> None:
    code, report = _evaluate(_edited(tmp_path, "0.17029039110268307", "0.18"), demo)

    assert code == 1
    assert report.startswith("# Acceptance: FAIL")
    assert re.search(r"growth book volatility \| numeric \| \*\*FAIL\*\* \| 0\.18 ", report)
    assert "3 of 4 cases passed" in report


def test_an_answerable_question_marked_abstain_fails(demo: list[str], tmp_path: Path) -> None:
    edited = _edited(tmp_path, 'source = "momentum-crash-risk.md"', "abstain = true")

    code, report = _evaluate(edited, demo)

    assert code == 1
    assert re.search(r"momentum crash risk .*\*\*FAIL\*\* \| abstain \| returned ", report)


def test_an_unanswerable_question_given_a_source_fails(demo: list[str], tmp_path: Path) -> None:
    edited = _edited(tmp_path, "abstain = true", 'source = "momentum-crash-risk.md"')

    code, report = _evaluate(edited, demo)

    assert code == 1
    assert re.search(r"municipal bonds .*\*\*FAIL\*\* .* \| abstained \|", report)


@pytest.mark.parametrize(
    ("old", "new", "shown"),
    [
        ('tool = "analyze_performance"', 'tool = "no_such_tool"', "unknown_tool"),
        ("metrics.annualized_volatility", "metrics.no_such_metric", "no number at"),
        ('dataset = "growth-book"', 'dataset = "no-such-data"', "error"),
    ],
)
def test_a_case_that_cannot_run_fails_and_the_rest_still_run(
    demo: list[str], tmp_path: Path, old: str, new: str, shown: str
) -> None:
    code, report = _evaluate(_edited(tmp_path, old, new), demo)

    assert code == 1
    assert shown in report
    assert "of 4 cases passed" in report


def test_the_json_report_can_be_traced_to_its_cases_and_data(demo: list[str]) -> None:
    code, output = _evaluate(GOLD, demo, "--format", "json")
    report = json.loads(output)

    assert code == 0
    assert report["passed"] is True
    assert report["total"] == 4
    assert report["gold_sha256"] == load_gold(GOLD)[1]
    assert {"dataset:growth-book", "dataset:value-book", "corpus_snapshot"} <= set(
        report["data_versions"]
    )
    assert report["platform_version"]


def test_the_report_never_quotes_retrieved_text(demo: list[str]) -> None:
    corpus = (DEMO / "notes" / "momentum-crash-risk.md").read_text("utf-8")
    sentence = next(line for line in corpus.splitlines() if len(line) > 40)

    _, report = _evaluate(GOLD, demo)

    assert sentence not in report


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        ("", "no cases"),
        ("not toml =", "Invalid"),
        ('[[retrieval]]\nname = "a"\nquestion = "q"\n', "not both or neither"),
        ('[[retrieval]]\nname = "a"\nquestion = "q"\nsource = "s"\nabstain = true\n', "not both"),
        (
            '[[numeric]]\nname = "a"\ntool = "t"\nvalue = "v"\nexpected = 1.0\n',
            "numeric.0.tolerance",
        ),
        (
            '[[numeric]]\nname = "a"\ntool = "t"\nvalue = "v"\nexpected = nan\ntolerance = 0.1\n',
            "numeric.0.expected",
        ),
        (
            '[[numeric]]\nname = "a"\ntool = "t"\nvalue = "v"\nexpected = 1.0\ntolerance = -1.0\n',
            "numeric.0.tolerance",
        ),
        ('[[retrieval]]\nname = "a"\nquestion = "q"\nabstain = true\nextra = 1\n', "extra"),
        (
            '[[retrieval]]\nname = "a"\nquestion = "q"\nabstain = true\n'
            '[[retrieval]]\nname = "a"\nquestion = "r"\nabstain = true\n',
            "appear twice",
        ),
    ],
)
def test_a_bad_cases_file_is_refused_and_nothing_runs(
    tmp_path: Path, content: str, problem: str
) -> None:
    gold = tmp_path / "cases.toml"
    gold.write_text(content, "utf-8")

    with pytest.raises(GoldFileError, match=problem):
        load_gold(gold)
    assert RUNNER.invoke(app, ["eval", "--gold", str(gold)]).exit_code == 2


def test_a_missing_cases_file_and_a_bad_format_are_usage_errors(tmp_path: Path) -> None:
    assert RUNNER.invoke(app, ["eval", "--gold", str(tmp_path / "none.toml")]).exit_code == 2
    assert RUNNER.invoke(app, ["eval", "--gold", str(GOLD), "--format", "pdf"]).exit_code == 2


def test_the_cases_the_smoke_test_runs_pass_on_the_sample(tmp_path: Path) -> None:
    data = {"monthly": SAMPLE / "datasets" / "monthly-returns.csv"}
    where = _prepare(tmp_path, SAMPLE / "corpus", data)

    code, report = _evaluate(ROOT / "deploy" / "acceptance-sample.toml", where)

    assert code == 0, report
    assert "3 of 3 cases passed" in report


def test_eval_is_a_command_and_not_a_tool() -> None:
    assert not list((ROOT / "scripts").glob("*acceptance*"))
    lock = json.loads((ROOT / "scripts" / "tool-versions.lock").read_text("utf-8"))
    assert not {"eval", "run_acceptance", "qaos_eval"} & set(lock.get("tools", lock))
