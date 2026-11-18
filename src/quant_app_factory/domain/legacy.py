"""Legacy code as tools: what a scan finds, how it is classified, and what the developer decides.

A scan reads a fund's existing Python statically (decision 0029) and finds its public functions
and methods. Each is classified as one of three kinds:
- ``expose``: typed, documented, and pure, with types `FirmApp.expose` accepts; register it as is.
- ``facade``: wrap it in a typed function that maps inputs to dataset references, quietens
  printing, and states what the legacy code reads; the legacy code stays unchanged.
- ``service``: it keeps state, loads slowly, or caches; serve it behind HTTP from one process,
  and register each endpoint with `FirmApp.call_service`.

A module-level memo cache (a dict filled on read, whose results could be computed again) or a
connection opened once is not state of that kind: a function that only reads through one is a
facade, with a note on the cache.

The classification is a first guess with reasons. The developer edits the inventory, and the
drafted code marks every decision it could not make with a TODO.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal

from quant_agent_os.tools.permissions import Permission

DataKind = Literal["series", "frame"]
ParamKind = Literal["positional_only", "positional", "var_positional", "keyword", "var_keyword"]


class LegacyError(ValueError):
    """Raised when legacy code cannot be scanned or an inventory cannot be used; safe to show."""


class Kind(StrEnum):
    """How a legacy callable becomes a tool."""

    EXPOSE = "expose"
    FACADE = "facade"
    SERVICE = "service"


class Trait(StrEnum):
    """Something a scan found in a callable's signature or body."""

    UNTYPED = "untyped"
    UNDOCUMENTED = "undocumented"
    UNSUPPORTED_TYPE = "unsupported_type"
    VAR_ARGS = "var_args"
    POSITIONAL_ONLY = "positional_only"
    RETURNS_NOTHING = "returns_nothing"
    PANDAS = "pandas"
    NUMPY = "numpy"
    MODULE_SETTINGS = "module_settings"
    ENV = "env"
    DATABASE = "database"
    READS_FILES = "reads_files"
    WRITES_FILES = "writes_files"
    PRINTS = "prints"
    NETWORK = "network"
    SUBPROCESS = "subprocess"
    MUTABLE_STATE = "mutable_state"
    MODULE_CACHE = "module_cache"
    NONDETERMINISTIC = "nondeterministic"
    STATEFUL_CLASS = "stateful_class"
    SLOW_INIT = "slow_init"
    CACHE = "cache"
    BLOCKS = "blocks"


# Traits of what a body does, which pass from a helper to its callers.
BEHAVIOUR = frozenset(
    {
        Trait.MODULE_SETTINGS,
        Trait.ENV,
        Trait.DATABASE,
        Trait.READS_FILES,
        Trait.WRITES_FILES,
        Trait.PRINTS,
        Trait.NETWORK,
        Trait.SUBPROCESS,
        Trait.MUTABLE_STATE,
        Trait.MODULE_CACHE,
        Trait.NONDETERMINISTIC,
        Trait.BLOCKS,
    }
)
# Any of these keeps a callable from being exposed as it is.
NEEDS_FACADE = frozenset(
    {
        Trait.UNTYPED,
        Trait.UNDOCUMENTED,
        Trait.UNSUPPORTED_TYPE,
        Trait.VAR_ARGS,
        Trait.POSITIONAL_ONLY,
        Trait.RETURNS_NOTHING,
        Trait.MODULE_SETTINGS,
        Trait.ENV,
        Trait.DATABASE,
        Trait.READS_FILES,
        Trait.WRITES_FILES,
        Trait.PRINTS,
        Trait.NETWORK,
        Trait.SUBPROCESS,
        Trait.MODULE_CACHE,
    }
)
LOCAL_DATABASES = ("sqlite3.", "duckdb.")
# Entry points, which run a program rather than compute a result: left out of a draft by default.
ENTRY_POINTS = frozenset({"main", "cli", "run", "serve", "start"})


@dataclass(frozen=True)
class Param:
    """One parameter of a legacy callable, as written."""

    name: str
    annotation: str | None  # source text of the hint
    default: str | None  # source text of the default
    kind: ParamKind = "positional"
    data: DataKind | None = None  # suggested: takes a dataset reference
    library: Literal["pandas", "numpy"] | None = None  # what the code seems to expect


@dataclass(frozen=True)
class Found:
    """A public function or method a scan found, with everything the draft needs."""

    module: str
    name: str
    owner: str | None  # the class, for a method
    params: tuple[Param, ...]
    returns: str | None  # source text of the return hint
    doc: str | None
    traits: dict[Trait, tuple[str, ...]]  # each trait with what was seen
    returns_guess: str | None = None  # a hint guessed from the return statements
    init_params: tuple[Param, ...] = ()  # the owner's constructor, for a method
    imports: dict[str, str] = field(default_factory=dict)  # name in a hint -> its import line

    @property
    def target(self) -> str:
        """The dotted name the inventory uses: ``module.function`` or ``module.Class.method``."""
        return ".".join(part for part in (self.module, self.owner, self.name) if part)

    @property
    def signature(self) -> str:
        """The signature as written, without ``self``."""
        parts: list[str] = []
        for param in self.params:
            text = {"var_positional": "*", "var_keyword": "**"}.get(param.kind, "") + param.name
            if param.annotation:
                text += f": {param.annotation}"
            if param.default is not None:
                text += f" = {param.default}" if param.annotation else f"={param.default}"
            parts.append(text)
        returns = f" -> {self.returns}" if self.returns else ""
        return f"{self.owner + '.' if self.owner else ''}{self.name}({', '.join(parts)}){returns}"

    def has(self, *traits: Trait) -> bool:
        """Whether any of ``traits`` was found."""
        return any(trait in self.traits for trait in traits)

    def seen(self, trait: Trait) -> str:
        """What was seen for ``trait``, as a short list."""
        return ", ".join(self.traits.get(trait, ()))


@dataclass
class Entry:
    """The developer's decision for one found callable, as the inventory file holds it."""

    target: str
    kind: Kind
    name: str
    include: bool = True
    data: dict[str, DataKind] = field(default_factory=dict)
    permissions: list[Permission] = field(default_factory=list)
    convention: str = ""


@dataclass
class Inventory:
    """The editable result of a scan: what was scanned, and one entry per callable."""

    source: Path
    app: str
    entries: list[Entry]
    found: dict[str, Found] = field(default_factory=dict)  # by target; empty when read back
    skipped: list[str] = field(default_factory=list)  # files that could not be read, and why


def classify(found: Found) -> tuple[Kind, list[str]]:
    """Return the suggested kind for ``found`` and the reasons, in plain words."""
    reasons = describe(found)
    if found.has(Trait.MUTABLE_STATE) or (
        found.has(Trait.STATEFUL_CLASS) and found.has(Trait.SLOW_INIT, Trait.CACHE)
    ):
        return Kind.SERVICE, reasons
    if found.owner is not None or found.has(*NEEDS_FACADE):
        return Kind.FACADE, reasons
    return Kind.EXPOSE, ["typed, documented, and pure", *reasons]


def describe(found: Found) -> list[str]:
    """Return what the scan found, one short phrase per trait, in a stable order."""
    phrases = {
        Trait.UNTYPED: "no type hint: {}",
        Trait.UNDOCUMENTED: "no docstring",
        Trait.UNSUPPORTED_TYPE: "a type a client cannot send or read: {}",
        Trait.VAR_ARGS: "takes {}; a tool names each parameter",
        Trait.POSITIONAL_ONLY: "positional-only {}; tools are called by name",
        Trait.RETURNS_NOTHING: "returns nothing; a tool must return a result",
        Trait.PANDAS: "pandas data: {}",
        Trait.NUMPY: "NumPy data: {}",
        Trait.MODULE_SETTINGS: "reads module settings: {}",
        Trait.ENV: "reads environment variables: {}",
        Trait.DATABASE: "opens or uses a database: {}",
        Trait.READS_FILES: "reads files: {}",
        Trait.WRITES_FILES: "writes files: {}",
        Trait.PRINTS: "prints",
        Trait.NETWORK: "calls out over the network: {}",
        Trait.SUBPROCESS: "runs programs: {}",
        Trait.MUTABLE_STATE: "keeps state between calls: {}",
        Trait.MODULE_CACHE: "reads through a module cache: {}",
        Trait.NONDETERMINISTIC: "not deterministic: {}",
        Trait.STATEFUL_CLASS: "a method of {}, whose constructor sets state",
        Trait.SLOW_INIT: "slow set-up: {}",
        Trait.CACHE: "caches: {}",
        Trait.BLOCKS: "may never return (an entry point or server): {}; left out",
    }
    reasons = []
    for trait, phrase in phrases.items():
        if trait in found.traits:
            reasons.append(phrase.format(found.seen(trait)) if "{}" in phrase else phrase)
    if found.owner is not None and Trait.STATEFUL_CLASS not in found.traits:
        reasons.append(f"a method: the facade builds a {found.owner} for each call")
    if found.owner is None and found.name in ENTRY_POINTS and Trait.BLOCKS not in found.traits:
        reasons.append("named like an entry point; left out")
    return reasons


def permissions_for(found: Found, kind: Kind) -> list[Permission]:
    """Return the permissions to declare for ``found``: never fewer than its code needs.

    Declared permissions are checked, not sandboxed: the platform cannot tell what the code
    does, so the draft declares what the scan saw, and the developer reviews it.
    """
    needed: set[Permission] = set()
    # A connection whose call was seen is local only for SQLite or DuckDB; one passed in, seen
    # only as .execute(), may be anywhere.
    connections = [
        seen for seen in found.traits.get(Trait.DATABASE, ()) if not seen.startswith(".")
    ]
    remote_database = found.has(Trait.DATABASE) and not (
        connections and all(any(db in seen for db in LOCAL_DATABASES) for seen in connections)
    )
    if kind is Kind.SERVICE:
        # The tool calls the service over HTTP (call_service adds network); the service must
        # change nothing, or the tool declares external_action.
        if found.has(Trait.WRITES_FILES, Trait.SUBPROCESS):
            needed.add(Permission.EXTERNAL_ACTION)
        return sorted(needed)
    if found.has(Trait.READS_FILES) or (found.has(Trait.DATABASE) and not remote_database):
        needed.add(Permission.READ_LOCAL_DATA)
    if found.has(Trait.WRITES_FILES):
        needed.add(Permission.WRITE_LOCAL_DATA)
    if found.has(Trait.NETWORK) or remote_database:
        needed.add(Permission.NETWORK)
    if found.has(Trait.SUBPROCESS):
        needed.add(Permission.EXECUTE_CODE)
    return sorted(needed)


def tool_name(found: Found) -> str:
    """The suggested tool name: the function's, or ``class_method`` for a method."""
    base = f"{snake(found.owner)}_{found.name}" if found.owner else found.name
    return snake(base)[:64]


def snake(name: str) -> str:
    """``name`` in snake_case, as a tool name needs: ``RiskModel`` becomes ``risk_model``."""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text).strip("_")
    return text if text[:1].isalpha() else f"tool_{text}"


def suggest(found: list[Found]) -> list[Entry]:
    """Return an entry per found callable, with unique tool names."""
    entries: list[Entry] = []
    used: set[str] = set()
    for item in found:
        kind, _ = classify(item)
        name = tool_name(item)
        if name in used:
            name = snake(f"{item.module.rsplit('.', 1)[-1]}_{name}")[:64]
        number = 2
        while name in used:
            name, number = f"{tool_name(item)[:60]}_{number}", number + 1
        used.add(name)
        entry_point = item.has(Trait.BLOCKS) or (item.owner is None and item.name in ENTRY_POINTS)
        entries.append(
            Entry(
                target=item.target,
                kind=kind,
                name=name,
                include=not entry_point,
                data={param.name: param.data for param in item.params if param.data},
                permissions=permissions_for(item, kind),
            )
        )
    return entries
