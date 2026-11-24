"""Readiness (/readyz) for a firm's server: ready only when the stores it reads answer."""

import shutil
import sqlite3
from contextlib import AbstractContextManager, closing
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from quant_agent_os.api import add_key
from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.registration import FirmApp
from quant_agent_os.runs import MemoryRunStore
from quant_platform import SqlDatasetStore, build_server_app
from quant_research import open_research
from quant_toolbox.data import SqlDataset, sql


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


class DeclaredStore:
    """A store with only ``get`` and ``ids``, as the docs have always asked: no ``check``."""

    def get(self, dataset_id: str) -> object:
        raise KeyError(dataset_id)

    def ids(self) -> list[str]:
        return ["returns"]


class ProbeFails(DeclaredStore):
    """A store whose ``ids`` still answers from a declared list, but whose ``check`` cannot read."""

    def check(self) -> None:
        raise OSError("the source is gone")


def _app(store: object, tmp_path: Path) -> AbstractContextManager[FastAPI]:
    return build_server_app(
        [FirmApp("risk", instructions="Desk risk.")],
        datasets=store,  # type: ignore[arg-type]
        keys=_keys(tmp_path),
        runs=MemoryRunStore(),
    )


def test_a_datasets_folder_deleted_after_start_is_not_ready(
    datasets: DirectoryDatasetStore, tmp_path: Path
) -> None:
    with _app(datasets, tmp_path) as app, TestClient(app) as client:
        assert client.get("/readyz").status_code == 200
        shutil.rmtree(datasets.root)
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "not ready", "failed": ["datasets"]}
    assert str(datasets.root) not in response.text


def test_a_sql_source_whose_file_is_deleted_after_start_is_not_ready(tmp_path: Path) -> None:
    pytest.importorskip("sqlalchemy")
    path = tmp_path / "oms.db"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE returns (day TEXT, ret REAL)")
        db.execute("INSERT INTO returns VALUES ('2024-01-31', 0.01)")
        db.commit()
    engine = sql.connect(f"sqlite:///{path}")
    store = SqlDatasetStore(
        {"oms": engine},
        [SqlDataset("returns", "SELECT day, ret FROM returns", index_name="day")],
    )
    try:
        with _app(store, tmp_path) as app, TestClient(app) as client:
            assert client.get("/readyz").status_code == 200
            engine.dispose()  # Windows deletes no file a pooled connection holds open
            path.unlink()
            response = client.get("/readyz")
    finally:
        engine.dispose()

    assert response.status_code == 503
    assert response.json() == {"status": "not ready", "failed": ["datasets"]}


def test_a_firm_store_without_check_is_still_probed_by_ids(tmp_path: Path) -> None:
    with _app(DeclaredStore(), tmp_path) as app, TestClient(app) as client:
        response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_a_store_whose_check_fails_is_named_even_when_ids_answers(tmp_path: Path) -> None:
    with _app(ProbeFails(), tmp_path) as app, TestClient(app) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "not ready", "failed": ["datasets"]}
    assert "gone" not in response.text
