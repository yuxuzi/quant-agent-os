"""A firm's own tool: plain Python with typed input and output, which knows nothing about MCP."""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from quant_agent_os.data import DatasetStore
from quant_agent_os.evidence import Evidence, SourceRef
from quant_agent_os.tools import ToolSpec, tool


class DeskVarInput(BaseModel):
    """A series by reference, so its values never pass through a model."""

    dataset: str = Field(min_length=1, description="A dataset id from list_datasets")
    column: str = Field(min_length=1, description="The series in that dataset")
    confidence: float = Field(default=0.95, gt=0.5, lt=1, description="For example 0.95 for 95%")


class DeskVarOutput(BaseModel):
    """The figure, with the convention it uses and where it came from."""

    var: float = Field(description="Loss as a positive fraction of capital, over one period")
    convention: str
    periods: int
    data_versions: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


def firm_tools(store: DatasetStore) -> list[ToolSpec[Any, Any]]:
    """Return the firm's tools over ``store``. This list is what the firm's server registers."""

    @tool(
        name="desk_var",
        description="One-period historical value at risk of one desk's returns, by the firm's "
        "convention: the nearest-rank loss, with no scaling or smoothing.",
    )
    def desk_var(params: DeskVarInput) -> DeskVarOutput:
        """Return the nearest-rank historical loss at ``confidence``."""
        dataset = store.get(params.dataset)
        returns = sorted(v for v in dataset.column(params.column) if not math.isnan(v))
        if not returns:
            raise ValueError(f"{params.dataset}.{params.column} has no values.")
        rank = max(math.ceil((1 - params.confidence) * len(returns)), 1)
        var = max(-returns[rank - 1], 0.0)
        source = SourceRef(
            source_id=f"{params.dataset}.{params.column}", location=f"{len(returns)} periods"
        )
        statement = (
            f"The {params.confidence:.0%} historical VaR of {params.dataset}.{params.column} "
            f"is {var:.2%} of capital over one period."
        )
        return DeskVarOutput(
            var=var,
            convention="Historical, nearest rank, one period, no scaling.",
            periods=len(returns),
            data_versions={f"dataset:{dataset.id}": dataset.fingerprint},
            warnings=["Fewer than 20 periods; read with care."] if len(returns) < 20 else [],
            evidence=[Evidence(kind="computed", statement=statement, sources=[source])],
        )

    return [desk_var]
