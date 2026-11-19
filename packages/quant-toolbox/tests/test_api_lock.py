"""Quant Toolbox's public surface is locked: a generated project pins it, so it changes on purpose.

`api.lock.json` records every public name of every subpackage, with its signature (or, for a
dataclass, its fields, and for a constant, its type). A change fails this test until the lock is
rewritten and the package's `CHANGELOG.md` names the change:

    QUANT_TOOLBOX_UPDATE_LOCK=1 uv run pytest packages/quant-toolbox/tests/test_api_lock.py

Removing a name or changing a signature is a minor version bump while the toolbox is 0.x, and a
major one after 1.0. Adding a name is a feature, and nothing else.
"""

from __future__ import annotations

import collections.abc
import dataclasses
import importlib
import inspect
import json
import os
import types
from pathlib import Path
from typing import Any, Union, get_args, get_origin

import pytest

import quant_toolbox

LOCK = Path(__file__).with_name("api.lock.json")
UPDATE = os.environ.get("QUANT_TOOLBOX_UPDATE_LOCK") == "1"


def _describe(value: Any) -> str:
    origin = get_origin(value)
    if origin in (Union, types.UnionType):  # a type alias; its type's name varies by Python
        members = sorted(_name(member) for member in get_args(value))
        return f"union: {' | '.join(members)}"
    if origin is collections.abc.Callable:
        arguments, result = get_args(value)
        return f"callable: ({', '.join(_name(item) for item in arguments)}) -> {_name(result)}"
    if _name(value) == "ndarray":
        return "array alias: ndarray"
    if dataclasses.is_dataclass(value) and isinstance(value, type):
        fields = ", ".join(f"{field.name}: {field.type}" for field in dataclasses.fields(value))
        return f"dataclass({fields})"
    if inspect.isclass(value):
        try:
            return f"class{inspect.signature(value)}"
        except (TypeError, ValueError):
            return "class"
    if callable(value):
        return f"def{inspect.signature(value)}"
    return f"constant: {type(value).__name__}"


def _name(member: Any) -> str:
    """A stable name: numpy's array aliases are spelled differently by version."""
    if "ndarray" in str(member).lower():
        return "ndarray"
    if member is type(None):
        return "None"
    origin = get_origin(member)
    return getattr(origin or member, "__name__", str(member))


def _surface() -> dict[str, dict[str, str]]:
    surface = {
        "quant_toolbox": {
            name: _describe(getattr(quant_toolbox, name))
            for name in ("MissingExtraError", "features")
        }
    }
    for name in quant_toolbox.SUBPACKAGES:
        module = importlib.import_module(f"quant_toolbox.{name}")
        surface[module.__name__] = {
            item: _describe(getattr(module, item)) for item in sorted(module.__all__)
        }
    for leaf in (
        "data.excel",
        "data.frames",
        "data.sql",
        "report.svg",
        "report.interactive",
        "report.excel",
        "report.pdf",
    ):
        module = importlib.import_module(f"quant_toolbox.{leaf}")
        surface[module.__name__] = {
            item: _describe(value)
            for item, value in sorted(vars(module).items())
            if not item.startswith("_")
            and inspect.isfunction(value)
            and value.__module__ == module.__name__
        }
    return surface


def test_every_public_name_is_exported() -> None:
    for name in quant_toolbox.SUBPACKAGES:
        module = importlib.import_module(f"quant_toolbox.{name}")
        missing = [item for item in module.__all__ if not hasattr(module, item)]
        assert not missing, f"{module.__name__} lists {missing} in __all__ but has no such name"


def test_the_public_surface_matches_the_lock() -> None:
    surface = _surface()
    if UPDATE:
        LOCK.write_text(json.dumps(surface, indent=2, sort_keys=True) + "\n", "utf-8")
        pytest.skip("rewrote the lock")
    if not LOCK.exists():
        pytest.fail("No api.lock.json; create it with QUANT_TOOLBOX_UPDATE_LOCK=1.")
    locked = json.loads(LOCK.read_text("utf-8"))
    changes = []
    for module in sorted(set(locked) | set(surface)):
        before, after = locked.get(module, {}), surface.get(module, {})
        changes += [f"removed {module}.{name}" for name in sorted(set(before) - set(after))]
        changes += [f"added {module}.{name}" for name in sorted(set(after) - set(before))]
        changes += [
            f"changed {module}.{name}: {before[name]} -> {after[name]}"
            for name in sorted(set(before) & set(after))
            if before[name] != after[name]
        ]
    assert not changes, (
        "Quant Toolbox's public API changed; if that is intended, rewrite the lock with "
        "QUANT_TOOLBOX_UPDATE_LOCK=1 and say so in packages/quant-toolbox/CHANGELOG.md:\n"
        + "\n".join(changes)
    )
