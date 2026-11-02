"""REST boundary: FastAPI routes over the executor and scoped API keys (decision 0011).

The routes need the optional ``api`` extra; importing this package and managing keys do not.
"""

from quant_agent_os.api.app import ApiUnavailableError, CallRequest, CallResponse, build_app
from quant_agent_os.api.keys import (
    SCOPES,
    ApiKey,
    ApiKeyError,
    KeyRing,
    Scope,
    add_key,
    hash_key,
    load_keys,
    new_key,
)
from quant_agent_os.api.server import DEFAULT_HOST, DEFAULT_PORT, is_loopback, run_http

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "SCOPES",
    "ApiKey",
    "ApiKeyError",
    "ApiUnavailableError",
    "CallRequest",
    "CallResponse",
    "KeyRing",
    "Scope",
    "add_key",
    "build_app",
    "hash_key",
    "is_loopback",
    "load_keys",
    "new_key",
    "run_http",
]
