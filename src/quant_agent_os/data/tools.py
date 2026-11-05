"""The read-only dataset tool, so a client can find a dataset and its columns before using it."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from quant_agent_os.data.datasets import DatasetInfo, DatasetStore
from quant_agent_os.evidence import Evidence
from quant_agent_os.tools import Permission, ResourceSpec, ToolSpec, tool


class DatasetQuery(BaseModel):
    """Which dataset to describe; every dataset when omitted."""

    dataset_id: str | None = Field(default=None, description="One dataset's id")


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
            "List the datasets analytics tools can read by id, with their columns and rows. "
            "Pass a dataset id and column to a tool instead of copying values."
        ),
        version="1.1.0",
        permissions=[Permission.READ_LOCAL_DATA],
    )
    def list_datasets(params: DatasetQuery) -> DatasetList:
        ids = [params.dataset_id] if params.dataset_id else store.ids()
        infos = [store.get(dataset_id).info() for dataset_id in ids]
        return DatasetList(
            datasets=infos,
            warnings=[] if infos else ["No datasets have been added; see `qaos data add`."],
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
