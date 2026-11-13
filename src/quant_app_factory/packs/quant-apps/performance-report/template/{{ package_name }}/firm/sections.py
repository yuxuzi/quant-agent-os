"""Your firm's own report sections. This file is yours to change.

Add a function that takes the `Context` and returns blocks, list it in ``SECTIONS``, and name it
in a tear sheet in ``config.toml``. For example::

    from ..context import Context
    from ..document import Block, Column, TableBlock
    from ..horizons import horizon_return


    def ytd_by_book(context: Context) -> list[Block]:
        \"\"\"Year to date, one row per book.\"\"\"
        rate = context.settings.periods_per_year
        rows = [
            [book, horizon_return(context.dates, context.returns(book), "YTD", rate)]
            for book in context.books.columns
        ]
        return [TableBlock("Year to date", [Column("Book", "text"), Column("YTD")], rows)]


    SECTIONS = {"ytd_by_book": ytd_by_book}

A section here with the same name as a built-in one replaces it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

SECTIONS: dict[str, Callable[[Any], list[Any]]] = {}
