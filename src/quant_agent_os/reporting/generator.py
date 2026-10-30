"""Assemble reports from tool records and render them for people."""

from __future__ import annotations

from datetime import datetime

from pydantic import ValidationError

from quant_agent_os import __version__
from quant_agent_os.evidence.models import Evidence
from quant_agent_os.reporting.models import Report, RunMetadata
from quant_agent_os.runtime.executor import ToolCallRecord
from quant_agent_os.runtime.plan import Plan

STANDARD_LIMITATIONS = (
    "Findings are limited to the registered tools and the data supplied for this run.",
    "This report is research support, not investment advice.",
)


def build_report(
    plan: Plan,
    records: list[ToolCallRecord],
    *,
    run_id: str,
    provider: str,
    registry_digest: str,
    started_at: datetime,
    finished_at: datetime,
) -> Report:
    """Collect evidence and warnings from tool outputs; record every failed call as a warning."""
    findings: list[Evidence] = []
    warnings: list[str] = []
    data_versions: dict[str, str] = {}
    for call in records:
        for key, value in call.data_versions.items():
            if data_versions.setdefault(key, value) != value:
                warnings.append(
                    f"{call.tool}: used {key} {value}, but an earlier call used "
                    f"{data_versions[key]}."
                )
        if call.status != "ok" or call.output is None:
            warnings.append(f"{call.tool}: {call.status} - {call.error}")
            continue
        warnings.extend(f"{call.tool}: {text}" for text in call.output.get("warnings", []))
        for item in call.output.get("evidence", []):
            try:
                findings.append(Evidence.model_validate(item))
            except ValidationError:
                warnings.append(f"{call.tool}: returned malformed evidence, which was ignored.")
    return Report(
        goal=plan.goal,
        plan=plan,
        tool_calls=records,
        findings=findings,
        warnings=warnings,
        limitations=list(STANDARD_LIMITATIONS),
        metadata=RunMetadata(
            run_id=run_id,
            platform_version=__version__,
            provider=provider,
            registry_digest=registry_digest,
            started_at=started_at,
            finished_at=finished_at,
            data_versions=data_versions,
        ),
    )


def render_markdown(report: Report) -> str:
    """Render a report as Markdown with findings, sources, warnings, and provenance."""
    lines = [f"# {report.goal}", "", "## Findings", ""]
    if not report.findings:
        lines.append("_No findings._")
    for finding in report.findings:
        sources = ", ".join(
            source.source_id + (f" ({source.location})" if source.location else "")
            for source in finding.sources
        )
        suffix = f" [sources: {sources}]" if sources else ""
        lines.append(f"- **{finding.kind}**: {finding.statement}{suffix}")
    lines += ["", "## Warnings", ""]
    lines += [f"- {warning}" for warning in report.warnings] or ["_None._"]
    lines += ["", "## Limitations", ""]
    lines += [f"- {limitation}" for limitation in report.limitations]
    lines += [
        "",
        "## Provenance",
        "",
        "| Step | Tool | Version | Status | ms |",
        "| --- | --- | --- | --- | --- |",
    ]
    for index, call in enumerate(report.tool_calls, start=1):
        lines.append(
            f"| {index} | {call.tool} | {call.tool_version or '-'} | {call.status} | "
            f"{call.duration_ms} |"
        )
    meta = report.metadata
    lines += [
        "",
        f"Run `{meta.run_id}`: platform {meta.platform_version}, provider `{meta.provider}`, "
        f"registry `{meta.registry_digest[:12]}`, "
        f"{meta.started_at.isoformat()} to {meta.finished_at.isoformat()}.",
    ]
    lines += [f"Data: {key} `{value}`." for key, value in sorted(meta.data_versions.items())]
    lines.append("")
    return "\n".join(lines)
