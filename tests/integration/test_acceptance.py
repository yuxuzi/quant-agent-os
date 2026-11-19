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
            "state tolerance, relative_tolerance, or both",
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


# Callers and per-caller access (P12)

ACCESS = DEMO / "access.toml"
GROWTH = '[caller]\nprincipal = "gwen"\ngroups = ["growth-desk"]\n'
VALUE_CALLER = 'caller = { principal = "vic", groups = ["value-desk"] }\n'


def _with_callers(tmp_path: Path, header: str, value_case_caller: str = "") -> Path:
    text = GOLD.read_text("utf-8")
    old = 'arguments = { returns = { dataset = "value-book", column = "book" } }\n'
    assert old in text
    cases = tmp_path / "cases.toml"
    cases.write_text(header + text.replace(old, old + value_case_caller), "utf-8")
    return cases


def test_with_an_access_file_a_case_without_a_caller_fails_unrun(demo: list[str]) -> None:
    code, report = _evaluate(GOLD, demo, "--access", str(ACCESS))

    assert code == 1
    assert "0 of 4 cases passed" in report
    assert report.count("not run: this catalog has an access policy") == 4


def test_each_case_runs_as_its_caller_with_their_entitlements(
    demo: list[str], tmp_path: Path
) -> None:
    cases = _with_callers(tmp_path, GROWTH, VALUE_CALLER)

    code, report = _evaluate(cases, demo, "--access", str(ACCESS))

    assert code == 0, report
    assert "| Caller |" in report
    assert "| gwen (growth-desk) | pass |" in report
    assert "| vic (value-desk) | pass |" in report
    assert "Warning:" not in report


def test_a_case_beyond_its_callers_entitlements_fails_as_if_missing(
    demo: list[str], tmp_path: Path
) -> None:
    cases = _with_callers(tmp_path, GROWTH)  # the value book's case runs as the growth desk

    code, output = _evaluate(cases, demo, "--access", str(ACCESS), "--format", "json")
    report = json.loads(output)

    assert code == 1
    failed = [result for result in report["results"] if not result["passed"]]
    assert [result["name"] for result in failed] == ["value book volatility"]
    assert "No dataset 'value-book'" in failed[0]["actual"]
    assert failed[0]["caller"] == "gwen (growth-desk)"


def test_the_configured_access_file_applies(
    demo: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quant_agent_os.config import get_settings

    monkeypatch.setenv("QUANT_AGENT_OS_ACCESS_FILE", str(ACCESS))
    get_settings.cache_clear()
    try:
        code, report = _evaluate(GOLD, demo)
    finally:
        get_settings.cache_clear()

    assert code == 1
    assert "not run: this catalog has an access policy" in report


def test_callers_without_an_access_policy_are_warned_about(demo: list[str], tmp_path: Path) -> None:
    code, report = _evaluate(_with_callers(tmp_path, GROWTH), demo)

    assert code == 0, report
    assert "Warning: The cases name callers, but this catalog has no access policy" in report


def test_an_invalid_access_file_is_a_usage_error(demo: list[str], tmp_path: Path) -> None:
    broken = tmp_path / "access.toml"
    broken.write_text("[groups.x]\ntools = 1\n", "utf-8")

    assert _evaluate(GOLD, demo, "--access", str(broken))[0] == 2


def test_the_runs_caller_is_named_in_one_place(tmp_path: Path) -> None:
    from quant_agent_os.tools import Caller, FileAccessPolicy, PermissionPolicy
    from quant_platform import compose
    from quant_platform.acceptance import run_acceptance

    cases, digest = load_gold(_with_callers(tmp_path, GROWTH))
    catalog = compose(policy=PermissionPolicy(), access=FileAccessPolicy(ACCESS))

    with pytest.raises(ValueError, match="name it in one place"):
        run_acceptance(catalog, cases, digest, caller=Caller("gwen"))


BOOKS = (("growth", 0.17029039110268307), ("value", 0.12447905949730392))


def test_run_acceptance_takes_the_runs_caller_from_code(demo: list[str]) -> None:
    from quant_agent_os.data import DirectoryDatasetStore
    from quant_agent_os.tools import Caller, FileAccessPolicy, PermissionPolicy
    from quant_platform import compose
    from quant_platform.acceptance import GoldFile, run_acceptance

    datasets = DirectoryDatasetStore(Path(demo[3]))
    gold = GoldFile.model_validate(
        {
            "numeric": [
                {
                    "name": name,
                    "tool": "analyze_performance",
                    "arguments": {"returns": {"dataset": f"{name}-book", "column": "book"}},
                    "value": "metrics.annualized_volatility",
                    "expected": expected,
                    "tolerance": 1e-9,
                }
                for name, expected in BOOKS
            ]
        }
    )
    catalog = compose(
        policy=PermissionPolicy(),
        datasets=datasets,
        access=FileAccessPolicy(ACCESS),
    )

    report = run_acceptance(
        catalog, gold, "sha", caller=Caller("gwen", groups=frozenset({"growth-desk"}))
    )

    assert [result.passed for result in report.results] == [True, False]
    assert "No dataset 'value-book'" in report.results[1].actual


def test_a_value_path_indexes_lists_and_picks_items_by_a_field() -> None:
    from quant_platform.acceptance import MISSING, _at

    output = {
        "rows": [{"t_stat": 5.9}, {"t_stat": 1.2}],
        "sectors": [{"sector": "Energy", "dts": 3.0}, {"sector": "Industrials", "dts": 4.5}],
        "flags": [{"held": True, "id": "a"}],
    }

    assert _at(output, "rows.1.t_stat") == 1.2
    assert _at(output, "sectors[sector=Industrials].dts") == 4.5
    assert _at(output, "flags[held=true].id") == "a"
    assert _at(output, "rows.2.t_stat") is MISSING
    assert _at(output, "sectors[sector=Utilities].dts") is MISSING
    assert _at({"rows": [{"k": 1}, {"k": 1}]}, "rows[k=1]") is MISSING  # two match: ambiguous


AGREED = """
[[numeric]]
name = "growth volatility within a relative tolerance"
tool = "analyze_performance"
arguments = { returns = { dataset = "growth-book", column = "book" } }
value = "metrics.annualized_volatility"
expected = 0.1703
relative_tolerance = 0.001

[[exact]]
name = "the growth book is monthly"
tool = "analyze_performance"
arguments = { returns = { dataset = "growth-book", column = "book" } }
value = "periods_per_year"
equals = 12

[[exact]]
name = "the first evidence is computed"
tool = "analyze_performance"
arguments = { returns = { dataset = "growth-book", column = "book" } }
value = "evidence.0.kind"
equals = "computed"

[[refusal]]
name = "the growth desk cannot read the value book"
tool = "analyze_performance"
arguments = { returns = { dataset = "value-book", column = "book" } }
message = "No dataset 'value-book'"
"""


def test_exact_refusal_and_relative_cases_pass_as_agreed(demo: list[str], tmp_path: Path) -> None:
    cases = tmp_path / "cases.toml"
    cases.write_text(GROWTH + AGREED, "utf-8")

    code, output = _evaluate(cases, demo, "--access", str(ACCESS), "--format", "json")
    report = json.loads(output)

    assert code == 0, output
    assert [result["kind"] for result in report["results"]] == [
        "numeric",
        "exact",
        "exact",
        "refusal",
    ]
    assert report["results"][0]["expected"] == "0.1703 ± 0.001 relative"


@pytest.mark.parametrize(
    ("old", "new", "actual"),
    [
        ("expected = 0.1703\n", "expected = 0.1710\n", "0.1702"),
        ("equals = 12", "equals = 252", "12"),
        ("equals = 12", "equals = true", "12"),
        ('dataset = "value-book"', 'dataset = "growth-book"', "ok: the call was allowed"),
        (
            'tool = "analyze_performance"\narguments = { returns = { dataset = "value',
            'tool = "analyse_performance"\narguments = { returns = { dataset = "value',
            "unknown_tool, not a refusal",
        ),
        ("message = \"No dataset 'value-book'\"", 'message = "permission"', "No dataset"),
    ],
)
def test_each_new_kind_of_case_fails_when_it_should(
    demo: list[str], tmp_path: Path, old: str, new: str, actual: str
) -> None:
    assert old in AGREED
    cases = tmp_path / "cases.toml"
    cases.write_text(GROWTH + AGREED.replace(old, new, 1), "utf-8")

    code, output = _evaluate(cases, demo, "--access", str(ACCESS), "--format", "json")
    report = json.loads(output)

    assert code == 1
    (failed,) = [result for result in report["results"] if not result["passed"]]
    assert actual in failed["actual"]
