"""`qaos eval`: agreed test cases against the served tools, with a report to attach to acceptance.

This is an operator command, not an agent tool. Each case runs through the executor with the same
tools and permissions the server has, so a case tests what a client would get. The report names
sources and numbers and never quotes retrieved text, so it can travel with the acceptance papers.
"""

from __future__ import annotations

import hashlib
import math
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from quant_agent_os import __version__
from quant_agent_os.runtime import ToolCall, ToolExecutor
from quant_agent_os.tools import ToolRegistry
from quant_platform.composition import Catalog

SEARCH_TOOL = "find_related_signals"


class GoldFileError(ValueError):
    """Raised when a cases file cannot be read or is not valid."""


class NumericCase(BaseModel):
    """A tool, its arguments, where its output holds a number, and the number agreed."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    value: str = Field(min_length=1, description="Where in the output, such as metrics.volatility")
    expected: float = Field(allow_inf_nan=False)
    tolerance: float = Field(ge=0, allow_inf_nan=False, description="Absolute, and always stated")


class RetrievalCase(BaseModel):
    """A question, and either the source that must answer it or that the corpus must abstain."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    question: str = Field(min_length=1)
    source: str | None = Field(default=None, min_length=1)
    abstain: bool = False
    k: int = Field(default=5, ge=1, description="How many passages may hold the source")

    @model_validator(mode="after")
    def _one_expectation(self) -> RetrievalCase:
        if (self.source is None) != self.abstain:
            raise ValueError("give either source or abstain = true, not both or neither")
        return self


class GoldFile(BaseModel):
    """The agreed cases: at least one, so an empty file never reads as a pass."""

    model_config = ConfigDict(extra="forbid")

    numeric: list[NumericCase] = Field(default_factory=list)
    retrieval: list[RetrievalCase] = Field(default_factory=list)

    @model_validator(mode="after")
    def _some_cases(self) -> GoldFile:
        if not self.numeric and not self.retrieval:
            raise ValueError("there are no cases")
        names = [case.name for case in self.numeric] + [case.name for case in self.retrieval]
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ValueError(f"case names appear twice: {', '.join(repeated)}")
        return self


class CaseResult(BaseModel):
    """One case's verdict, with what was expected and what came back."""

    name: str
    kind: Literal["numeric", "retrieval"]
    passed: bool
    expected: str
    actual: str


class AcceptanceReport(BaseModel):
    """Every case's verdict, and what they were run on, so the result can be traced later."""

    passed: bool
    total: int
    failed: int
    results: list[CaseResult]
    platform_version: str
    gold_sha256: str
    data_versions: dict[str, str]
    at: datetime

    def markdown(self) -> str:
        """Return the report as Markdown, for attaching to an acceptance record."""
        verdict = "PASS" if self.passed else "FAIL"
        lines = [
            f"# Acceptance: {verdict}",
            "",
            f"{self.total - self.failed} of {self.total} cases passed.",
            "",
            f"- Platform version: {self.platform_version}",
            f"- Cases file SHA-256: `{self.gold_sha256}`",
            f"- Run at: {self.at.isoformat()}",
            "",
            "| Case | Kind | Result | Expected | Actual |",
            "| --- | --- | --- | --- | --- |",
        ]
        lines += [
            "| "
            + " | ".join(
                _cell(cell)
                for cell in (
                    item.name,
                    item.kind,
                    "pass" if item.passed else "**FAIL**",
                    item.expected,
                    item.actual,
                )
            )
            + " |"
            for item in self.results
        ]
        if self.data_versions:
            lines += ["", "| Data | Version |", "| --- | --- |"]
            lines += [f"| {_cell(k)} | `{v}` |" for k, v in sorted(self.data_versions.items())]
        return "\n".join(lines) + "\n"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def load_gold(path: Path) -> tuple[GoldFile, str]:
    """Read a cases file; return it with its SHA-256, or raise `GoldFileError` with the problem."""
    try:
        raw = path.read_bytes()
        gold = GoldFile.model_validate(tomllib.loads(raw.decode("utf-8")))
    except FileNotFoundError as error:
        raise GoldFileError(f"No cases file at {path}.") from error
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(map(str, item['loc']))}: {item['msg']}" for item in error.errors()
        )
        raise GoldFileError(f"Invalid cases file {path}: {problems}") from error
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise GoldFileError(f"Invalid cases file {path}: {error}") from error
    return gold, hashlib.sha256(raw).hexdigest()


def _at(output: dict[str, Any], path: str) -> Any:
    found: Any = output
    for part in path.split("."):
        if not isinstance(found, dict) or part not in found:
            return None
        found = found[part]
    return found


def _numeric(executor: ToolExecutor, case: NumericCase, versions: dict[str, str]) -> CaseResult:
    wanted = f"{case.expected} ± {case.tolerance}"
    record = executor.execute(ToolCall(tool=case.tool, arguments=case.arguments))
    versions.update(record.data_versions)
    if record.status != "ok":
        actual = f"{record.status}: {record.error}"
        return CaseResult(
            name=case.name, kind="numeric", passed=False, expected=wanted, actual=actual
        )
    value = _at(record.output or {}, case.value)
    if isinstance(value, bool) or not isinstance(value, int | float):
        actual = f"no number at {case.value}"
        return CaseResult(
            name=case.name, kind="numeric", passed=False, expected=wanted, actual=actual
        )
    passed = math.isfinite(value) and abs(value - case.expected) <= case.tolerance
    return CaseResult(
        name=case.name, kind="numeric", passed=passed, expected=wanted, actual=str(value)
    )


def _retrieval(executor: ToolExecutor, case: RetrievalCase, versions: dict[str, str]) -> CaseResult:
    wanted = "abstain" if case.abstain else f"{case.source} in the top {case.k}"
    record = executor.execute(
        ToolCall(tool=SEARCH_TOOL, arguments={"query": case.question, "k": case.k})
    )
    versions.update(record.data_versions)
    if record.status != "ok":
        actual = f"{record.status}: {record.error}"
        return CaseResult(
            name=case.name, kind="retrieval", passed=False, expected=wanted, actual=actual
        )
    output = record.output or {}
    sources = list(
        dict.fromkeys(passage["source"]["source_id"] for passage in output.get("passages", []))
    )
    passed = output.get("abstained") is True if case.abstain else case.source in sources
    actual = "abstained" if output.get("abstained") else f"returned {', '.join(sources)}"
    return CaseResult(
        name=case.name, kind="retrieval", passed=passed, expected=wanted, actual=actual
    )


def run_acceptance(catalog: Catalog, gold: GoldFile, gold_sha256: str) -> AcceptanceReport:
    """Run every case against ``catalog``'s tools, under its permissions, and report them all."""
    executor = ToolExecutor(ToolRegistry(catalog.tools), catalog.policy)
    versions: dict[str, str] = {}
    results = [_numeric(executor, case, versions) for case in gold.numeric]
    results += [_retrieval(executor, case, versions) for case in gold.retrieval]
    failed = sum(not result.passed for result in results)
    return AcceptanceReport(
        passed=failed == 0,
        total=len(results),
        failed=failed,
        results=results,
        platform_version=__version__,
        gold_sha256=gold_sha256,
        data_versions=versions,
        at=datetime.now(UTC),
    )
