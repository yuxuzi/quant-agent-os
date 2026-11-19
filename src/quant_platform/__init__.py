"""Quant-Agent-OS platform server: every product's toolset behind one MCP server.

`compose` assembles the chosen toolsets' tools, resources, and prompts (`Catalog`), adding the
deterministic `research_report`. `qaos serve` serves them, a firm's own server does so with
`serve_apps` (or `serve_http` and its own catalog), `build_server_app` gives a firm's HTTP app
for testing in process, and `open_platform` opens them from a notebook. `SqlDatasetStore` reads a
firm's SQL databases as datasets (the ``sql`` extra); it loads on first use, so importing this
package needs no extra.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from quant_platform.composition import TOOLSETS, Catalog, Toolset, compose
from quant_platform.notebook import NotebookPlatform, open_platform
from quant_platform.report import ReportRequest, ResearchReport, research_report_tool
from quant_platform.serve import build_server_app, serve_apps, serve_http, serve_stdio

if TYPE_CHECKING:
    from quant_platform.sql_store import SqlDatasetStore

__all__ = [
    "TOOLSETS",
    "Catalog",
    "NotebookPlatform",
    "ReportRequest",
    "ResearchReport",
    "SqlDatasetStore",
    "Toolset",
    "build_server_app",
    "compose",
    "open_platform",
    "research_report_tool",
    "serve_apps",
    "serve_http",
    "serve_stdio",
]


def __getattr__(name: str) -> Any:
    """Load `SqlDatasetStore` on first use: it needs Quant Toolbox, which the base install lacks."""
    if name == "SqlDatasetStore":
        from quant_platform.sql_store import SqlDatasetStore

        return SqlDatasetStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
