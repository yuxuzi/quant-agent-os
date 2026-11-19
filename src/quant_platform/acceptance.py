"""`qaos eval`: agreed test cases against the served tools, with a report to attach to acceptance.

This is an operator command, not an agent tool. Each case runs through the executor with the same
tools and permissions the server has, so a case tests what a client would get. The report names
sources and numbers and never quotes retrieved text, so it can travel with the acceptance papers.

A case runs as a named caller, a principal and groups, given on the case, for the whole file in
``[caller]``, or to `run_acceptance`. With an access policy, the case reaches exactly what that
caller would (decision 0021), and a case that names no caller is not run but fails, saying why:
there is no caller to check, and the policy fails closed. Without one, named callers change
nothing, and the report says so.
"""

from __future__ import annotations

import hashlib
import math
import re
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from quant_agent_os import __version__
from quant_agent_os.runtime import ToolCall, ToolExecutor
from quant_agent_os.tools import Caller, ToolRegistry
from quant_agent_os.tools.access import acting_as
from quant_platform.composition import Catalog

SEARCH_TOOL = "find_related_signals"


class GoldFileError(ValueError):
    """Raised when a cases file cannot be read or is not valid."""


class CaseCaller(BaseModel):
    """Who a case runs as: a principal and its groups, as a key or a token would bind them."""

    model_config = ConfigDict(extra="forbid")

    principal: str = Field(min_length=1)
    groups: list[str] = Field(default_factory=list)

    def caller(self) -> Caller:
        """Return the caller to bind."""
        return Caller(self.principal, groups=frozenset(self.groups))


def describe(caller: Caller) -> str:
    """Return ``caller`` as the report shows it, such as ``pm-macro (macro, pm)``."""
    groups = ", ".join(sorted(caller.groups))
    return f"{caller.principal} ({groups})" if groups else caller.principal


VALUE_PATH = (
    "Where in the output: keys joined by dots, such as metrics.volatility; a number indexes a "
    "list (rows.0.t_stat), and [field=text] picks the item whose field is text "
    "(sectors[sector=Industrials].selection)"
)


class NumericCase(BaseModel):
    """A tool, its arguments, where its output holds a number, and the number agreed."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    value: str = Field(min_length=1, description=VALUE_PATH)
    expected: float = Field(allow_inf_nan=False)
    tolerance: float | None = Field(default=None, ge=0, allow_inf_nan=False, description="Absolute")
    relative_tolerance: float | None = Field(
        default=None, ge=0, allow_inf_nan=False, description="A share of |expected|"
    )
    caller: CaseCaller | None = None

    @model_validator(mode="after")
    def _stated(self) -> NumericCase:
        if self.tolerance is None and self.relative_tolerance is None:
            raise ValueError("state tolerance, relative_tolerance, or both")
        return self

    def allowed(self) -> float:
        """Return the largest difference that passes: the larger of the two tolerances."""
        relative = (self.relative_tolerance or 0.0) * abs(self.expected)
        return max(self.tolerance or 0.0, relative)

    def stated(self) -> str:
        """Return the expectation as the report shows it."""
        parts = [f"± {self.tolerance}"] if self.tolerance is not None else []
        if self.relative_tolerance is not None:
            parts.append(f"± {self.relative_tolerance:g} relative")
        return f"{self.expected} {' or '.join(parts)}"


class ExactCase(BaseModel):
    """A tool, its arguments, where its output holds a value, and the value agreed exactly."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    value: str = Field(min_length=1, description=VALUE_PATH)
    equals: str | bool | int | list[Any] = Field(description="Text, a flag, a count, or a list")
    caller: CaseCaller | None = None


class RefusalCase(BaseModel):
    """A call its caller must not be able to make: refused, or shown no such dataset."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    message: str | None = Field(default=None, min_length=1, description="Text the error holds")
    caller: CaseCaller | None = None


class RetrievalCase(BaseModel):
    """A question, and either the source that must answer it or that the corpus must abstain."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    question: str = Field(min_length=1)
    source: str | None = Field(default=None, min_length=1)
    abstain: bool = False
    k: int = Field(default=5, ge=1, description="How many passages may hold the source")
    caller: CaseCaller | None = None

    @model_validator(mode="after")
    def _one_expectation(self) -> RetrievalCase:
        if (self.source is None) != self.abstain:
            raise ValueError("give either source or abstain = true, not both or neither")
        return self


class GoldFile(BaseModel):
    """The agreed cases: at least one, so an empty file never reads as a pass."""

    model_config = ConfigDict(extra="forbid")

    caller: CaseCaller | None = Field(default=None, description="For cases that name none")
    numeric: list[NumericCase] = Field(default_factory=list)
    exact: list[ExactCase] = Field(default_factory=list)
    refusal: list[RefusalCase] = Field(default_factory=list)
    retrieval: list[RetrievalCase] = Field(default_factory=list)

    @model_validator(mode="after")
    def _some_cases(self) -> GoldFile:
        cases: list[NumericCase | ExactCase | RefusalCase | RetrievalCase] = [
            *self.numeric,
            *self.exact,
            *self.refusal,
            *self.retrieval,
        ]
        if not cases:
            raise ValueError("there are no cases")
        names = [case.name for case in cases]
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ValueError(f"case names appear twice: {', '.join(repeated)}")
        return self


class CaseResult(BaseModel):
    """One case's verdict, with what was expected and what came back."""

    name: str
    kind: Literal["numeric", "exact", "refusal", "retrieval"]
    passed: bool
    expected: str
    actual: str
    caller: str | None = None


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
    warnings: list[str] = Field(default_factory=list)

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
        ]
        lines += [f"Warning: {warning}" for warning in self.warnings]
        lines += [""] if self.warnings else []
        callers = any(item.caller for item in self.results)
        lines += [
            "| Case | Kind |" + (" Caller |" if callers else "") + " Result | Expected | Actual |",
            "| --- | --- |" + (" --- |" if callers else "") + " --- | --- | --- |",
        ]
        lines += [
            "| "
            + " | ".join(
                _cell(cell)
                for cell in (
                    item.name,
                    item.kind,
                    *([item.caller or "none"] if callers else []),
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


NOT_REFUSALS = frozenset({"invalid_input", "unknown_tool"})
PICK = re.compile(r"^(?P<key>[^\[\]]*)\[(?P<field>[^=\]]+)=(?P<text>[^\]]*)\]$")
MISSING = object()


def _at(output: dict[str, Any], path: str) -> Any:
    """Return what ``path`` names in ``output`` (see `VALUE_PATH`), or `MISSING`."""
    found: Any = output
    for part in path.split("."):
        picked = PICK.match(part)
        key = picked["key"] if picked else part
        if key:
            if isinstance(found, dict) and key in found:
                found = found[key]
            elif isinstance(found, list) and key.isdigit() and int(key) < len(found):
                found = found[int(key)]
            else:
                return MISSING
        if picked:
            if not isinstance(found, list):
                return MISSING
            matches = [
                item
                for item in found
                if isinstance(item, dict) and _text(item.get(picked["field"])) == picked["text"]
            ]
            if len(matches) != 1:
                return MISSING
            found = matches[0]
    return found


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, dict | list):
        return None
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


NO_CALLER = (
    "not run: this catalog has an access policy, which refuses a call with no caller; give the "
    "case caller = {principal, groups}, or the file a [caller] table"
)


class _Runner:
    """Runs each case as its caller, through one executor that applies the catalog's access."""

    def __init__(self, catalog: Catalog, default: Caller | None) -> None:
        self.executor = ToolExecutor(
            ToolRegistry(catalog.tools), catalog.policy, access=catalog.access
        )
        self.guarded = catalog.access is not None
        self.default = default
        self.versions: dict[str, str] = {}

    def caller(self, named: CaseCaller | None) -> Caller | None:
        return named.caller() if named is not None else self.default

    def call(self, caller: Caller | None, tool: str, arguments: dict[str, Any]) -> Any:
        with acting_as(caller):
            record = self.executor.execute(ToolCall(tool=tool, arguments=arguments))
        self.versions.update(record.data_versions)
        return record

    def numeric(self, case: NumericCase) -> CaseResult:
        caller = self.caller(case.caller)
        verdict: dict[str, Any] = {
            "name": case.name,
            "kind": "numeric",
            "expected": case.stated(),
            "caller": describe(caller) if caller else None,
        }
        if self.guarded and caller is None:
            return CaseResult(**verdict, passed=False, actual=NO_CALLER)
        record = self.call(caller, case.tool, case.arguments)
        if record.status != "ok":
            return CaseResult(**verdict, passed=False, actual=f"{record.status}: {record.error}")
        value = _at(record.output or {}, case.value)
        if isinstance(value, bool) or not isinstance(value, int | float):
            return CaseResult(**verdict, passed=False, actual=f"no number at {case.value}")
        passed = math.isfinite(value) and abs(value - case.expected) <= case.allowed()
        return CaseResult(**verdict, passed=passed, actual=str(value))

    def exact(self, case: ExactCase) -> CaseResult:
        caller = self.caller(case.caller)
        verdict: dict[str, Any] = {
            "name": case.name,
            "kind": "exact",
            "expected": _shown(case.equals),
            "caller": describe(caller) if caller else None,
        }
        if self.guarded and caller is None:
            return CaseResult(**verdict, passed=False, actual=NO_CALLER)
        record = self.call(caller, case.tool, case.arguments)
        if record.status != "ok":
            return CaseResult(**verdict, passed=False, actual=f"{record.status}: {record.error}")
        value = _at(record.output or {}, case.value)
        if value is MISSING:
            return CaseResult(**verdict, passed=False, actual=f"nothing at {case.value}")
        # A flag equals only a flag: True must not pass for 1.
        passed = value == case.equals and isinstance(value, bool) == isinstance(case.equals, bool)
        return CaseResult(**verdict, passed=passed, actual=_shown(value))

    def refusal(self, case: RefusalCase) -> CaseResult:
        caller = self.caller(case.caller)
        verdict: dict[str, Any] = {
            "name": case.name,
            "kind": "refusal",
            "expected": "refused" + (f", saying {case.message!r}" if case.message else ""),
            "caller": describe(caller) if caller else None,
        }
        if self.guarded and caller is None:
            return CaseResult(**verdict, passed=False, actual=NO_CALLER)
        record = self.call(caller, case.tool, case.arguments)
        if record.status == "ok":
            return CaseResult(**verdict, passed=False, actual="ok: the call was allowed")
        if record.status in NOT_REFUSALS:
            # A misspelt tool or argument fails here, so it cannot pass as a refusal.
            return CaseResult(
                **verdict, passed=False, actual=f"{record.status}, not a refusal: {record.error}"
            )
        said = case.message is None or case.message in (record.error or "")
        return CaseResult(**verdict, passed=said, actual=f"{record.status}: {record.error}")

    def retrieval(self, case: RetrievalCase) -> CaseResult:
        caller = self.caller(case.caller)
        verdict: dict[str, Any] = {
            "name": case.name,
            "kind": "retrieval",
            "expected": "abstain" if case.abstain else f"{case.source} in the top {case.k}",
            "caller": describe(caller) if caller else None,
        }
        if self.guarded and caller is None:
            return CaseResult(**verdict, passed=False, actual=NO_CALLER)
        record = self.call(caller, SEARCH_TOOL, {"query": case.question, "k": case.k})
        if record.status != "ok":
            return CaseResult(**verdict, passed=False, actual=f"{record.status}: {record.error}")
        output = record.output or {}
        sources = list(
            dict.fromkeys(passage["source"]["source_id"] for passage in output.get("passages", []))
        )
        passed = output.get("abstained") is True if case.abstain else case.source in sources
        actual = "abstained" if output.get("abstained") else f"returned {', '.join(sources)}"
        return CaseResult(**verdict, passed=passed, actual=actual)


def _shown(value: Any, limit: int = 200) -> str:
    text = repr(value) if isinstance(value, str) else str(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def run_acceptance(
    catalog: Catalog, gold: GoldFile, gold_sha256: str, *, caller: Caller | None = None
) -> AcceptanceReport:
    """Run every case against ``catalog``'s tools, under its permissions and access, and report.

    Each case runs as the caller it names, else the file's ``[caller]``, else ``caller``. With an
    access policy, a case left with no caller fails without running. Without one, named callers
    change nothing, and the report warns. Raises `ValueError` when both the file's ``[caller]``
    and ``caller`` are given, since one would be ignored.
    """
    if caller is not None and gold.caller is not None:
        raise ValueError(
            "The cases file names the run's caller in [caller], and so does "
            "run_acceptance(caller=...); name it in one place."
        )
    runner = _Runner(catalog, gold.caller.caller() if gold.caller else caller)
    results = [runner.numeric(case) for case in gold.numeric]
    results += [runner.exact(case) for case in gold.exact]
    results += [runner.refusal(case) for case in gold.refusal]
    results += [runner.retrieval(case) for case in gold.retrieval]
    warnings = []
    if catalog.access is None and any(result.caller for result in results):
        warnings.append(
            "The cases name callers, but this catalog has no access policy, so every case "
            "reached every tool and dataset and no entitlement was checked. Compose the catalog "
            "with its access policy (qaos eval --access) to check them."
        )
    versions = runner.versions
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
        warnings=warnings,
    )
