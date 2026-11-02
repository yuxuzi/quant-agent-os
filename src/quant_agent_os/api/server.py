"""Serve the REST app with uvicorn (optional extra ``quant-agent-os[api]``)."""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING

from quant_agent_os.api.app import INSTALL_HINT, ApiUnavailableError

if TYPE_CHECKING:
    from fastapi import FastAPI

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def is_loopback(host: str) -> bool:
    """Return whether ``host`` only accepts connections from this machine."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def run_http(app: FastAPI, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Serve ``app`` until interrupted; raises `ApiUnavailableError` without the extra."""
    try:
        import uvicorn
    except ImportError as error:
        raise ApiUnavailableError(f"REST support is not installed; {INSTALL_HINT}.") from error
    uvicorn.run(app, host=host, port=port, server_header=False, date_header=False)
