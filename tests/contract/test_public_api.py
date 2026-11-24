"""What 1.0 promises a firm's code is locked: a public name changes only on purpose (decision 0035).

`public_api.lock.json` records each promised module's public names, with each callable's signature
(a class's constructor). A change fails this test until the lock is rewritten and `CHANGELOG.md`
names the change:

    QUANT_AGENT_OS_UPDATE_API_LOCK=1 uv run pytest tests/contract/test_public_api.py

From 1.0, removing a name or changing a signature is a major release; adding one is a minor one.
"""

from __future__ import annotations

import enum
import importlib
import inspect
import json
import os
from pathlib import Path
from typing import Any

LOCK = Path(__file__).with_name("public_api.lock.json")
UPDATE = os.environ.get("QUANT_AGENT_OS_UPDATE_API_LOCK") == "1"
MODULES = (
    "quant_platform",
    "quant_agent_os.registration",
    "quant_agent_os.testing",
    "quant_agent_os.tools",
    "quant_agent_os.data",
)


def _describe(value: Any) -> str:
    """A name's promise: an enum's members, a callable's signature, or a constant's type."""
    if inspect.isclass(value) and issubclass(value, enum.Enum):
        return f"enum: {', '.join(member.name for member in value)}"
    if inspect.isclass(value) or callable(value):
        try:
            signature = str(inspect.signature(value))
        except (TypeError, ValueError):
            return type(value).__name__
        # Python 3.11 prints typing.Annotated where later versions print Annotated.
        return f"{type(value).__name__}{signature.replace('typing.', '')}"
    return type(value).__name__


def _surface() -> dict[str, dict[str, str]]:
    surface: dict[str, dict[str, str]] = {}
    for name in MODULES:
        module = importlib.import_module(name)
        names = getattr(module, "__all__", None)
        assert names is not None, f"{name} must state its public names in __all__"
        surface[name] = {item: _describe(getattr(module, item)) for item in sorted(names)}
    return surface


def test_the_promised_names_match_the_lock() -> None:
    surface = _surface()
    if UPDATE:
        LOCK.write_text(json.dumps(surface, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    locked = json.loads(LOCK.read_text(encoding="utf-8"))
    changed = {
        module: sorted(set(surface[module].items()) ^ set(locked.get(module, {}).items()))
        for module in MODULES
    }
    assert not any(changed.values()), (
        "The public API changed. Rewrite the lock with QUANT_AGENT_OS_UPDATE_API_LOCK=1 and say "
        f"so in CHANGELOG.md: {changed}"
    )
