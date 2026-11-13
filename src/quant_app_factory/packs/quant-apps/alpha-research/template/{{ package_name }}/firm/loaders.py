"""Read your firm's own file formats. This file is yours to change.

Map a file suffix to a function that returns a `Table`. A loader here takes priority over the
built-in readers for that suffix. For example, for US dates in a ``.dat`` export::

    import datetime as dt
    from pathlib import Path

    import numpy as np

    from ..tables import Table


    def read_admin_export(path: Path) -> Table:
        rows = [line.split(";") for line in path.read_text().splitlines()]
        dates = [dt.datetime.strptime(row[0], "%m/%d/%Y").date() for row in rows[1:]]
        values = np.array([[float(cell) for cell in row[1:]] for row in rows[1:]])
        return Table(dates, rows[0][1:], values)


    LOADERS = {".dat": read_admin_export}
"""

from __future__ import annotations

from ..tables import Loader

LOADERS: dict[str, Loader] = {}
