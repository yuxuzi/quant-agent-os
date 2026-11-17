"""Tables to and from pandas DataFrames, for notebooks. Needs pandas, which nothing else uses.

Importing this module never fails; converting without pandas raises
`quant_toolbox.MissingExtraError`.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from quant_toolbox._optional import require
from quant_toolbox.data._tables import Table, parse_date


def to_pandas(table: Table) -> Any:
    """``table`` as a DataFrame indexed by date, one column per name."""
    pandas = require("pandas", purpose="converting a table to a DataFrame")
    index = pandas.DatetimeIndex(table.dates, name="date")
    return pandas.DataFrame(table.values, index=index, columns=list(table.columns))


def from_pandas(frame: Any) -> Table:
    """A table from a DataFrame indexed by date, with numeric columns."""
    require("pandas", purpose="converting a DataFrame to a table")
    dates = [
        parse_date(value.to_pydatetime() if hasattr(value, "to_pydatetime") else value)
        for value in frame.index
    ]
    values = np.asarray(frame.to_numpy(dtype=np.float64), dtype=np.float64)
    return Table(dates, [str(name) for name in frame.columns], values)
