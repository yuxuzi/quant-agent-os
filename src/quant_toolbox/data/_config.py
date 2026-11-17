"""Configuration files: flat TOML keys read into a frozen dataclass, with every mistake named.

Unknown keys and wrong types are refused with a message that names them, so a typo never goes
unnoticed. A field typed ``tuple[int, ...]`` reads a TOML list. ``bool`` is never accepted for a
number, and a float field accepts a whole number.
"""

from __future__ import annotations

import dataclasses
import json
import tomllib
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")


class ConfigError(ValueError):
    """Raised when a configuration file is invalid; the message says which key and why."""


def read_toml(path: Path, error: type[ValueError] = ConfigError) -> dict[str, Any]:
    """Return the TOML at ``path``, or raise ``error`` naming the file and the problem."""
    try:
        return tomllib.loads(path.read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as problem:
        raise error(f"Cannot read {path}: {problem}") from problem


def load_config(path: Path, cls: type[T], error: type[ValueError] = ConfigError) -> T:
    """Read ``path`` (flat keys named like the fields of dataclass ``cls``) over its defaults."""
    fields = {field.name: field for field in dataclasses.fields(cls)}  # type: ignore[arg-type]
    data = read_toml(path, error)
    unknown = sorted(set(data) - set(fields))
    if unknown:
        raise error(f"{path}: unknown keys {', '.join(unknown)}; use {', '.join(fields)}.")
    for key, value in data.items():
        annotation = fields[key].type
        if not _type_ok(annotation, value):
            raise error(f"{path}: {key} must be {annotation}, not {value!r}.")
        if str(annotation).startswith("tuple"):
            data[key] = tuple(value)
    return cls(**data)


def dump_config(config: object, header: str = "") -> str:
    """``config`` (a dataclass) as TOML that `load_config` reads back unchanged."""
    lines = [f"# {header}"] if header else []
    for name, value in dataclasses.asdict(config).items():  # type: ignore[call-overload]
        if value is not None:
            lines.append(f"{name} = {json.dumps(value)}")
    return "\n".join(lines) + "\n"


def _type_ok(annotation: object, value: object) -> bool:
    text = str(annotation)
    if text.startswith("tuple"):
        return isinstance(value, list) and all(_type_ok("int", item) for item in value)
    if isinstance(value, bool):
        return text.startswith("bool")
    if text.startswith("str"):
        return isinstance(value, str)
    if text.startswith("int"):
        return isinstance(value, int)
    if text.startswith("bool"):
        return False
    return isinstance(value, int | float)  # float fields accept whole numbers
