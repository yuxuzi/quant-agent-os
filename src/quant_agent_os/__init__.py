"""QuantAgentOS runtime: typed tools, a bounded orchestrator, and evidence-linked reports."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("quant-agent-os")
except PackageNotFoundError:  # pragma: no cover - source checkout without installation
    __version__ = "0.0.0"
