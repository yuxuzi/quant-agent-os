"""Quant App Factory: generate tested, packaged Python applications with Quant-Agent-OS.

The foundation lands in backlog item B-10; this package currently reserves the namespace and the
workspace boundary.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("quant-agent-os-factory")
except PackageNotFoundError:  # pragma: no cover - source checkout without installation
    __version__ = "0.0.0"
