"""Readiness (/readyz) for a firm's server: ready only when the stores it reads answer."""

from pathlib import Path

from fastapi.testclient import TestClient

from quant_agent_os.api import add_key
from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.registration import FirmApp
from quant_agent_os.runs import MemoryRunStore
from quant_platform import build_server_app
from quant_research import open_research


class UnreadableStore:
    """A dataset store whose directory cannot be listed; its error names a path."""

    def get(self, dataset_id: str) -> object:
        raise PermissionError("/srv/private/datasets: Permission denied")

    def ids(self) -> list[str]:
        raise PermissionError("/srv/private/datasets: Permission denied")


def _keys(tmp_path: Path) -> Path:
    path = tmp_path / "keys.toml"
    add_key(path, "team", ["tools:list", "tools:call"])
    return path


def test_a_firm_server_is_ready_when_its_stores_answer(
    datasets: DirectoryDatasetStore, tmp_path: Path
) -> None:
    with (
        build_server_app(
            [FirmApp("risk", instructions="Desk risk.")],
            datasets=datasets,
            keys=_keys(tmp_path),
            runs=MemoryRunStore(),
        ) as app,
        TestClient(app) as client,
    ):
        response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_an_unreadable_dataset_store_is_named_without_its_path(tmp_path: Path) -> None:
    with (
        build_server_app(
            [FirmApp("risk", instructions="Desk risk.")],
            datasets=UnreadableStore(),  # type: ignore[arg-type]
            keys=_keys(tmp_path),
            runs=MemoryRunStore(),
        ) as app,
        TestClient(app) as client,
    ):
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "not ready", "failed": ["datasets"]}
    assert "/srv" not in response.text and "Permission" not in response.text


def test_a_closed_knowledge_store_is_named(tmp_path: Path) -> None:
    research = open_research(":memory:")
    research.close()
    with (
        build_server_app(
            [], research=research.research, keys=_keys(tmp_path), runs=MemoryRunStore()
        ) as app,
        TestClient(app) as client,
    ):
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "not ready", "failed": ["knowledge"]}
