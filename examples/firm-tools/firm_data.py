"""A firm's own data source, as a `DatasetStore`: a read-only SQLite table of returns.

Any source works, because the platform asks only for `get` and `ids`. Point these at your own
database or service; the analytics tools and `list_datasets` then read it by reference.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from contextlib import closing
from pathlib import Path

from quant_agent_os.data import Dataset, UnknownDatasetError, parse_dataset

# Fictional monthly returns for one fictional desk.
SAMPLE_RETURNS = [
    0.012, -0.008, 0.021, 0.004, -0.015, 0.009, 0.017, -0.022, 0.006, 0.011, -0.004, 0.014,
    -0.031, 0.019, 0.003, -0.011, 0.008, 0.016, -0.006, 0.024, -0.018, 0.005, 0.010, -0.002,
]  # fmt: skip


class SqliteDatasetStore:
    """Datasets from a table of (dataset, date, series, value) rows, opened read-only."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        # A connection per call, because tools run on a pool of threads.
        return sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)

    def ids(self) -> list[str]:
        """Return the dataset ids."""
        with closing(self._connect()) as db:
            return [row[0] for row in db.execute("SELECT DISTINCT dataset FROM returns ORDER BY 1")]

    def get(self, dataset_id: str) -> Dataset:
        """Return one dataset, parsed the way a CSV file is, so it gets the same checks."""
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT date, series, value FROM returns WHERE dataset = ? ORDER BY date",
                (dataset_id,),
            ).fetchall()
        if not rows:
            known = ", ".join(self.ids()) or "none"
            raise UnknownDatasetError(f"Unknown dataset '{dataset_id}'. Known: {known}.")
        series = sorted({row[1] for row in rows})
        values = {(date, name): value for date, name, value in rows}
        text = io.StringIO()
        writer = csv.writer(text)
        writer.writerow(["date", *series])
        for date in sorted({row[0] for row in rows}):
            writer.writerow([date, *(values.get((date, name), "") for name in series)])
        return parse_dataset(dataset_id, text.getvalue().encode())


def create_sample_database(path: Path) -> None:
    """Write a small fictional database, so the example runs with nothing else."""
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE returns (dataset TEXT, date TEXT, series TEXT, value REAL)")
        db.executemany(
            "INSERT INTO returns VALUES ('desks', ?, 'growth', ?)",
            [
                (f"{2024 + month // 12}-{month % 12 + 1:02d}-28", value)
                for month, value in enumerate(SAMPLE_RETURNS)
            ],
        )
