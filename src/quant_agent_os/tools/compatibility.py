"""Tool compatibility: classify a schema change and check it against a version bump.

A checked-in lock records each served tool's version, permissions, and schemas (decision 0015).
`check_lock` compares the tools against it: a schema change with no lock update fails, and so
does a bump smaller than the change. `classify` reads two JSON Schemas the way a client would:
- **Major:** an input that is new and required, or newly required; a removed field; a changed
  type; an input enum value removed; an output field that may now be missing; a widened
  permission set.
- **Minor:** additions only, such as an optional input, a new output field, or a new enum value.
- **Patch:** only descriptions, titles, or examples changed.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from quant_agent_os.tools.contracts import ToolSpec, parse_version

Change = Literal["major", "minor", "patch"]
Direction = Literal["input", "output"]
_RANK: dict[Change, int] = {"patch": 0, "minor": 1, "major": 2}
_ANNOTATIONS = frozenset({"title", "description", "examples"})


def lock_entry(spec: ToolSpec[Any, Any]) -> dict[str, Any]:
    """Return what the lock records for ``spec``: version, permissions, and both schemas."""
    descriptor = spec.descriptor()
    return {
        "version": spec.version,
        "permissions": descriptor["permissions"],
        "input_schema": descriptor["input_schema"],
        "output_schema": descriptor["output_schema"],
    }


def classify(old: dict[str, Any], new: dict[str, Any]) -> Change | None:
    """Return the kind of change from lock entry ``old`` to ``new``, or None when there is none.

    Versions are not compared here; `check_lock` checks the bump against the change.
    """
    changes: list[Change | None] = [
        _schema_change(_resolved(old["input_schema"]), _resolved(new["input_schema"]), "input"),
        _schema_change(_resolved(old["output_schema"]), _resolved(new["output_schema"]), "output"),
    ]
    if set(new["permissions"]) - set(old["permissions"]):
        changes.append("major")
    elif set(new["permissions"]) != set(old["permissions"]):
        changes.append("minor")
    return _most(changes)


def bump(old_version: str, new_version: str) -> Change | None:
    """Return the size of the step from ``old_version`` to ``new_version``, or None if it is
    not an increase."""
    old, new = parse_version(old_version), parse_version(new_version)
    if new <= old:
        return None
    if new[0] > old[0]:
        return "major"
    return "minor" if new[1] > old[1] else "patch"


def check_lock(lock: dict[str, dict[str, Any]], specs: Iterable[ToolSpec[Any, Any]]) -> list[str]:
    """Return a problem for every tool that disagrees with ``lock``; an empty list means none.

    A tool whose schemas changed must have its version raised by at least the change, and the
    lock must then be updated to match.
    """
    problems: list[str] = []
    current = {spec.name: lock_entry(spec) for spec in specs}
    for name in sorted(set(lock) - set(current)):
        problems.append(
            f"{name} is in the lock but not served; removing a tool needs a major platform "
            "release and a deprecation window (decision 0015). Update the lock if intended."
        )
    for name, entry in sorted(current.items()):
        if name not in lock:
            problems.append(f"{name} {entry['version']} is new; add it to the lock.")
            continue
        old = lock[name]
        if old == entry:
            continue
        change = classify(old, entry)
        step = bump(old["version"], entry["version"])
        if change is not None and (step is None or _RANK[step] < _RANK[change]):
            problems.append(
                f"{name}: a {change} schema change needs a {change} version bump from "
                f"{old['version']}, but the version is {entry['version']}."
            )
        else:
            problems.append(
                f"{name}: changed from {old['version']} to {entry['version']}; update the lock."
            )
    return problems


def _most(changes: Iterable[Change | None]) -> Change | None:
    most: Change | None = None
    for change in changes:
        if change is not None and (most is None or _RANK[change] > _RANK[most]):
            most = change
    return most


def _resolved(
    schema: Any, definitions: dict[str, Any] | None = None, seen: tuple[str, ...] = ()
) -> Any:
    """Return ``schema`` with local ``$ref``s inlined and ``$defs`` dropped (cycles stay refs)."""
    if definitions is None and isinstance(schema, dict):
        definitions = schema.get("$defs", {})
    definitions = definitions or {}
    if isinstance(schema, list):
        return [_resolved(item, definitions, seen) for item in schema]
    if not isinstance(schema, dict):
        return schema
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        name = reference.removeprefix("#/$defs/")
        if name in definitions and name not in seen:
            siblings = {key: value for key, value in schema.items() if key != "$ref"}
            return _resolved({**definitions[name], **siblings}, definitions, (*seen, name))
    return {
        key: _resolved(value, definitions, seen) for key, value in schema.items() if key != "$defs"
    }


def _structure(schema: Any) -> Any:
    """Return ``schema`` without annotations, which no client validates against."""
    if isinstance(schema, list):
        return [_structure(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    return {
        key: (
            {name: _structure(value) for name, value in child.items()}
            if key == "properties" and isinstance(child, dict)
            else _structure(child)
        )
        for key, child in schema.items()
        if key not in _ANNOTATIONS
    }


def _schema_change(old: Any, new: Any, direction: Direction) -> Change | None:
    if old == new:
        return None
    if _structure(old) == _structure(new):
        return "patch"
    if not (isinstance(old, dict) and isinstance(new, dict)):
        return "major"
    changes: list[Change | None] = []
    old_properties, new_properties = old.get("properties", {}), new.get("properties", {})
    old_required, new_required = set(old.get("required", [])), set(new.get("required", []))
    if set(old_properties) - set(new_properties):
        changes.append("major")
    for name in set(new_properties) - set(old_properties):
        changes.append("major" if direction == "input" and name in new_required else "minor")
    for name in set(old_properties) & set(new_properties):
        changes.append(_schema_change(old_properties[name], new_properties[name], direction))
    common = set(old_properties) & set(new_properties)
    tightened = (new_required - old_required) & common
    loosened = (old_required - new_required) & common
    if direction == "input":
        changes += ["major" if tightened else None, "minor" if loosened else None]
    else:
        changes += ["major" if loosened else None, "minor" if tightened else None]
    if "enum" in old and "enum" in new:
        removed = [value for value in old["enum"] if value not in new["enum"]]
        added = [value for value in new["enum"] if value not in old["enum"]]
        changes.append("major" if removed and direction == "input" else None)
        changes.append("minor" if added or removed else None)
    if "items" in old and "items" in new:
        changes.append(_schema_change(old["items"], new["items"], direction))
    handled = {"properties", "required", "enum", "items"}
    rest_old = {key: value for key, value in old.items() if key not in handled}
    rest_new = {key: value for key, value in new.items() if key not in handled}
    if _structure(rest_old) != _structure(rest_new):
        changes.append("major")
    return _most(changes) or "patch"
