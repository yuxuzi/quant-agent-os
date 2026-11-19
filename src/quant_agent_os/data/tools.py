"""The read-only dataset tool, so a client can find a dataset and its columns before using it."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from quant_agent_os.data.datasets import DatasetInfo, DatasetStore
from quant_agent_os.evidence import Evidence
from quant_agent_os.tools import Permission, ResourceSpec, ToolSpec, tool

LISTED_COLUMNS = 20
"""A listing names this many columns of each dataset; one dataset by id names them all."""


class DatasetQuery(BaseModel):
    """Which dataset to describe; every dataset when omitted."""

    dataset_id: str | None = Field(default=None, description="One dataset's id, with all columns")
    prefix: str | None = Field(default=None, description="Only the datasets whose id starts so")


class DatasetList(BaseModel):
    """Datasets with their columns, size, index range, and fingerprint, but not their values."""

    datasets: list[DatasetInfo]
    warnings: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    data_versions: dict[str, str] = Field(default_factory=dict)


def dataset_tools(store: DatasetStore) -> list[ToolSpec[Any, Any]]:
    """Return `list_datasets` bound to ``store``."""

    @tool(
        name="list_datasets",
        description=(
            "List the datasets analytics tools can read by id, with their rows, dates, and first "
            f"{LISTED_COLUMNS} columns; pass dataset_id for one dataset's every column. Pass a "
            "dataset id and column to a tool instead of copying values."
        ),
        version="2.0.0",
        permissions=[Permission.READ_LOCAL_DATA],
    )
    def list_datasets(params: DatasetQuery) -> DatasetList:
        ids = [params.dataset_id] if params.dataset_id else store.ids()
        if params.prefix:
            ids = [dataset_id for dataset_id in ids if dataset_id.startswith(params.prefix)]
        infos = [store.get(dataset_id).info() for dataset_id in ids]
        if not params.dataset_id:
            infos = [
                info.model_copy(update={"columns": info.columns[:LISTED_COLUMNS]}) for info in infos
            ]
        warnings = [] if infos else ["No datasets have been added; see `qaos data add`."]
        if params.prefix and not infos:
            warnings = [f"No dataset id starts with '{params.prefix}'."]
        shortened = [info.id for info in infos if len(info.columns) < info.column_count]
        if shortened:
            warnings.append(
                f"{len(shortened)} datasets have more than {LISTED_COLUMNS} columns, of which the "
                "first are listed; pass dataset_id for all of one dataset's columns."
            )
        return DatasetList(
            datasets=infos,
            warnings=warnings,
            data_versions={f"dataset:{info.id}": info.fingerprint for info in infos},
        )

    return [list_datasets]


def dataset_resources() -> list[ResourceSpec]:
    """Return the dataset resource, read through `list_datasets`."""
    return [
        ResourceSpec(
            uri_template="qaos://dataset/{dataset_id}",
            name="dataset",
            description="A dataset's columns, rows, index range, and fingerprint.",
            tool="list_datasets",
        )
    ]
