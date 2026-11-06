"""The firm's own tool in the demonstration: a book's value at risk by the firm's convention."""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from quant_agent_os.data import DatasetStore
from quant_agent_os.evidence import Evidence, SourceRef
from quant_agent_os.tools import Permission, ToolSpec, tool


class BookVarInput(BaseModel):
    """A book by name, so its positions and returns never pass through a model."""

    book: str = Field(min_length=1, description="A book's name: 'growth' for 'growth-weights'")
    confidence: float = Field(default=0.95, gt=0.5, lt=1, description="For example 0.95 for 95%")


class BookVarOutput(BaseModel):
    """The figure, the positions it used, its convention, and where it came from."""

    book: str
    var: float = Field(description="Loss as a positive fraction of capital, over one period")
    weights: dict[str, float] = Field(description="The book's positions, to pass to analyze_risk")
    convention: str
    periods: int
    data_versions: dict[str, str] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)


def nearest_rank_var(returns: list[float], confidence: float) -> float:
    """Return the loss at ``confidence``: the nearest-rank return, floored at zero."""
    ordered = sorted(returns)
    # Rounded first: (1 - 0.95) * 20 is a little over 1 in floating point, and must be rank 1.
    rank = max(math.ceil(round((1 - confidence) * len(ordered), 9)), 1)
    return max(-ordered[rank - 1], 0.0)


def firm_tools(store: DatasetStore) -> list[ToolSpec[Any, Any]]:
    """Return the firm's tools over ``store``, which must be guarded if access rules apply."""

    @tool(
        name="book_var",
        description="One-period historical value at risk of a book at its current weights, by "
        "the firm's convention: the nearest-rank loss, with no scaling or smoothing. Also returns "
        "the book's weights, for analyze_risk.",
        permissions=[Permission.READ_LOCAL_DATA],
    )
    def book_var(params: BookVarInput) -> BookVarOutput:
        """Read the book's weights and asset returns by id, and return their weighted VaR."""
        positions = store.get(f"{params.book}-weights")
        assets = store.get(f"{params.book}-assets")
        weights = dict(zip(positions.index or [], positions.column("weight"), strict=True))
        columns = [assets.column(name) for name in weights]
        returns = [
            math.fsum(weight * value for weight, value in zip(weights.values(), row, strict=True))
            for row in zip(*columns, strict=True)
        ]
        if not returns or any(math.isnan(value) for value in returns):
            raise ValueError(f"{assets.id} must have a value for every asset in every period.")
        var = nearest_rank_var(returns, params.confidence)
        source = SourceRef(source_id=assets.id, location=f"{len(returns)} periods")
        statement = (
            f"The {params.confidence:.0%} historical VaR of the {params.book} book is {var:.2%} "
            "of capital over one period."
        )
        return BookVarOutput(
            book=params.book,
            var=var,
            weights=weights,
            convention="Historical, nearest rank, one period, at current weights, no scaling.",
            periods=len(returns),
            data_versions={
                f"dataset:{positions.id}": positions.fingerprint,
                f"dataset:{assets.id}": assets.fingerprint,
            },
            evidence=[Evidence(kind="computed", statement=statement, sources=[source])],
        )

    return [book_var]
