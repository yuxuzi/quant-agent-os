"""Your firm's own report sections. Generated once; later template updates leave this file alone.

Add a function that takes the `RiskView` and returns blocks, list it in ``SECTIONS``, and name it
under ``sections`` in ``config.toml``. A section with a built-in's name replaces it. For example::

    from ..document import Block, Column, TableBlock
    from ..risk import RiskView


    def long_short_ratio(view: RiskView) -> list[Block]:
        \"\"\"Long exposure over short exposure.\"\"\"
        long = float(view.weights[view.weights > 0].sum())
        short = float(-view.weights[view.weights < 0].sum())
        ratio = long / short if short else None
        return [TableBlock("Long/short", [Column("Ratio", "ratio")], [[ratio]])]


    SECTIONS = {"long_short_ratio": long_short_ratio}
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

SECTIONS: dict[str, Callable[[Any], list[Any]]] = {}
