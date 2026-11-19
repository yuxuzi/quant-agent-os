"""Draft the code that serves legacy callables: facades, a server file, a service, and a test.

The draft is ordinary Python the firm edits and commits (decisions 0020, 0024, and 0029). It
names every function it registers, declares the permissions the scan saw, and marks each
decision it could not make with a ``TODO(...)``: ``type``, ``data``, ``unit``, ``doc``,
``convention``, ``config``, and ``review``. Nothing here runs the code it writes.
"""

from __future__ import annotations

import ast
import json
import re
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import PurePath

from quant_app_factory.domain.legacy import (
    DataKind,
    Entry,
    Found,
    Inventory,
    Kind,
    LegacyError,
    Param,
    Trait,
    snake,
)
from quant_app_factory.domain.legacy_analysis import SLOW_CALL

DRAFTED = ("facades.py", "server.py", "stubs.py", "service/app.py", "test_tools.py")
STDLIB = frozenset(sys.stdlib_module_names)
# Ruff's default line length: the draft passes ruff with no configuration.
WIDTH = 88
PATH_NAME = re.compile(r"(^|_)(path|file|filename|dest|destination|out|output|target)$")
WINDOW_NAME = re.compile(r"(window|lookback|period|periods|days|horizon|halflife|span|lag|n)$")
PRICE_NAME = re.compile(r"price|px|close|level|nav")
# Untyped parameters named like these take a string: a name, a label, or a date.
STRING_NAME = re.compile(
    r"(^|_)(names?|desk|class|sector|region|country|currency|ccy|ticker|symbol|scenario|method|"
    r"kind|mode|book|fund|strategy|portfolio|account|id|key|label|code|model|source|by|group|"
    r"benchmark|freq|frequency|type|style|side|venue|exchange)$"
)
DATE_NAME = re.compile(r"(^|_)(date|as_of|asof|start|end|since|until|day)$")
SAMPLE_DATE = "2024-06-28"  # a business day inside the generated test's sample data
SIMPLE_HINT = re.compile(r"^(int|float|str|bool|None|list|dict|tuple|\[|\]|,| |\||\.\.\.)+$")
SERVICE_PORT = 8100


@dataclass(frozen=True)
class Draft:
    """The drafted files by relative path, and the open decisions they mark."""

    files: dict[str, str]
    todos: list[str]
    tools: list[str]


@dataclass
class _Imports:
    """The import lines a drafted module needs, rendered in isort's usual order."""

    lines: set[str] = field(default_factory=set)
    local: frozenset[str] = frozenset({"facades", "stubs", "server", "service"})

    def add(self, *lines: str) -> None:
        self.lines.update(lines)

    def clashes(self, *lines: str) -> bool:
        """Whether any of ``lines`` binds a name another import already binds differently."""
        bound = {_bound(line): line for line in self.lines}
        return any(bound.get(_bound(line), line) != line for line in lines)

    def render(self) -> str:
        """The imports in four groups (future, standard library, third party, local), with
        names from one module merged into one statement, as isort writes them."""
        plain: dict[str, set[str]] = {}
        names: dict[str, set[str]] = {}
        for line in self.lines:
            words = line.split()
            if words[0] == "import":
                plain.setdefault(words[1], set()).add(line)
            else:
                names.setdefault(words[1], set()).add(" ".join(words[3:]))
        groups: list[list[str]] = [[], [], [], []]
        for module in sorted(plain, key=str.lower):  # straight imports first, as isort does
            groups[self._group(module)] += sorted(plain[module])
        for module in sorted(names, key=str.lower):
            head = f"from {module} import "
            imported = sorted(names[module], key=_by_type)
            merged = [name for name in imported if " as " not in name]  # isort keeps "as" apart
            blocks = [
                (_by_type(name), _wrapped("", head, [name], brackets=False))
                for name in imported
                if " as " in name
            ]
            if merged:
                blocks.append((_by_type(merged[0]), _wrapped("", head, merged, brackets=False)))
            for _, block in sorted(blocks):
                groups[self._group(module)] += block
        return "\n\n".join("\n".join(group) for group in groups if group)

    def _group(self, module: str) -> int:
        if module.startswith("."):
            return 3  # the draft's own modules, imported relative to the package
        top = module.split(".")[0]
        if top == "__future__":
            return 0
        if top in STDLIB:
            return 1
        return 3 if top in self.local else 2


def _bound(line: str) -> str:
    """The name an import line binds."""
    words = line.split()
    if "as" in words:
        return words[-1]
    return words[-1] if words[0] == "from" else words[1].split(".")[0]


def _by_type(name: str) -> tuple[int, str]:
    """isort's order: constants, then classes, then everything else."""
    bare = name.split()[0]
    return (0 if bare.isupper() else 1 if bare[:1].isupper() else 2, bare)


def _wrapped(
    indent: str, head: str, items: list[str], tail: str = "", *, brackets: bool = True
) -> list[str]:
    """``head(items)tail`` on one line when it fits in `WIDTH` columns, else one per line."""
    opening, closing = ("(", ")") if brackets else ("", "")
    one = f"{indent}{head}{opening}{', '.join(items)}{closing}{tail}"
    if len(one) <= WIDTH:
        return [one]
    return [f"{indent}{head}(", *(f"{indent}    {item}," for item in items), f"{indent}){tail}"]


def _comment(text: str, indent: str = "") -> list[str]:
    """``text`` as comment lines of at most `WIDTH` columns, continued with an indent."""
    lines = textwrap.wrap(text, width=WIDTH - 4 - len(indent), break_on_hyphens=False)
    return [f"{indent}# {lines[0]}", *(f"{indent}#   {line}" for line in lines[1:])]


@dataclass
class _Selected:
    found: Found
    entry: Entry


@dataclass
class _Example:
    """One tool's example call for the generated test."""

    tool: str
    arguments: list[tuple[str, str, bool]]  # name, Python source, guessed


@dataclass
class _Samples:
    """The made-up datasets the generated test needs, by the data parameters' names."""

    series: dict[str, str] = field(default_factory=dict)  # column -> "price" or "return"
    frames: dict[str, str] = field(default_factory=dict)  # parameter -> "price" or "return"


def draft(
    inventory: Inventory,
    found: dict[str, Found],
    *,
    source_root: str,
    legacy: str,
    package: str = "legacy_tools",
) -> Draft:
    """Return the drafted files for the included entries of ``inventory``.

    ``found`` is a fresh scan of the source by target, ``source_root`` the directory the legacy
    package imports from, relative to the output, ``legacy`` the package's name, for the
    files' words, and ``package`` the draft's own: it is a package whose modules import each
    other relatively, so a firm's server imports ``package.server`` as it is. Raises
    `LegacyError` when an entry names nothing the scan found, or asks for something a draft
    cannot do.
    """
    selected: list[_Selected] = []
    for entry in inventory.entries:
        if not entry.include:
            continue
        item = found.get(entry.target)
        if item is None:
            raise LegacyError(
                f"{entry.target} is in the inventory but not in {inventory.source}; rescan, or "
                "set include = false."
            )
        unknown = sorted(set(entry.data) - {param.name for param in item.params})
        if unknown:
            raise LegacyError(
                f"{entry.target} has no parameter {', '.join(unknown)} to mark as data."
            )
        if entry.kind is Kind.EXPOSE and item.owner is not None:
            raise LegacyError(
                f"{entry.target} is a method, which needs an object to call it on; make it a "
                "facade or a service."
            )
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", entry.name):
            raise LegacyError(
                f"{entry.target}: name '{entry.name}' must be lowercase letters, digits, and "
                "underscores, starting with a letter."
            )
        selected.append(_Selected(item, entry))
    if not selected:
        raise LegacyError("No entry is included; set include = true on the entries to draft.")
    facades = [s for s in selected if s.entry.kind is Kind.FACADE]
    services = [s for s in selected if s.entry.kind is Kind.SERVICE]
    samples = _Samples()
    examples = [_example(s, samples) for s in selected]
    files: dict[str, str] = {}
    if facades:
        files["facades.py"] = _facades_module(facades, legacy)
    if services:
        files["stubs.py"] = _stubs_module(services, legacy)
        files["service/__init__.py"] = '"""The draft service for stateful legacy code."""\n'
        files["service/app.py"] = _service_module(services, legacy, list(found.values()))
    files["__init__.py"] = (
        f'"""Legacy code as tools, drafted by `qaos legacy wrap`; import {package}.server."""\n'
    )
    files["server.py"] = _server_module(inventory.app, selected, legacy, bool(services), package)
    files["conftest.py"] = _conftest(source_root)
    files["test_tools.py"] = _test_module(examples, samples, bool(services))
    todos = [
        f"{name}:{number}: {line.strip()}"
        for name in DRAFTED
        if name in files
        for number, line in enumerate(files[name].splitlines(), 1)
        if "TODO" in line
    ]
    return Draft(files, todos, [s.entry.name for s in selected])


# Facades


def _facades_module(selected: list[_Selected], legacy: str) -> str:
    imports = _Imports()
    imports.add("from __future__ import annotations", "from typing import Any")
    bodies = [_facade(s, imports) for s in selected]
    helpers = [JSON_READY]
    imports.add("import dataclasses", "import math")
    if any(_slices(s.found) for s in selected):
        helpers.append(CROSS_SECTION)
    if any(_one_at_a_time(s.found) for s in selected):
        helpers.append(LEGACY)
        imports.add(
            "import contextlib",
            "import sys",
            "import threading",
            "from collections.abc import Iterator",
        )
    doc = (
        f'"""Typed facades over {legacy}, drafted by `qaos legacy wrap`; edit them freely.\n\n'
        "Each facade calls the legacy code unchanged and adds only what a tool needs: type\n"
        "hints, a docstring, data by reference, quiet printing one call at a time, and\n"
        "JSON-ready results. Keep them thin: business logic stays in the legacy code.\n"
        '"""'
    )
    return "\n\n\n".join([doc + "\n\n" + imports.render(), *bodies, *helpers]).rstrip() + "\n"


def _facade(selected: _Selected, imports: _Imports) -> str:
    found, entry = selected.found, selected.entry
    callee = _legacy_import(found, imports)
    todos: list[str] = []
    params = [p for p in found.params if p.kind not in ("var_positional", "var_keyword")]
    init = [
        p
        for p in found.init_params
        if p.kind not in ("var_positional", "var_keyword")
        and p.name not in {q.name for q in params}
    ]
    kept_init = [p for p in init if p.default is None or _default(p) is not None]
    for param in init:
        if param not in kept_init:
            todos.append(
                f"TODO(config): {found.owner} is built with its default {param.name}="
                f"{param.default}; add a parameter if a caller may choose it."
            )
    signature: list[str] = []
    late: list[str] = []  # parameters whose legacy default cannot be repeated here
    for param in [*params, *kept_init]:
        hint = _hint(param, entry.data.get(param.name), found, imports, todos)
        default = _default(param)
        if param.default is not None and default is None:
            late.append(param.name)
            signature.append(f"{param.name}: {hint} | None = None")
        elif param.default is not None:
            signature.append(f"{param.name}: {_optional(hint, default)} = {default}")
        else:
            signature.append(f"{param.name}: {hint}")
    if kept_init:
        signature.insert(0, "*")
    row = None
    extra_doc: list[tuple[str, str]] = []
    if _slices(found):
        taken = {p.name for p in [*params, *kept_init]}
        row = "as_of" if "as_of" not in taken else "row_date"
        signature.append(f"{row}: str | None = None")
        extra_doc.append((row, "The row to return, a date as YYYY-MM-DD; the last when empty."))
        returns, use_json = "dict[str, Any]", False
    else:
        returns, use_json = _returns(found, imports, todos)
    lines = [line for todo in todos for line in _comment(todo)]
    lines += _wrapped("", f"def {entry.name}", signature, f" -> {returns}:")
    lines.append(_docstring(found, entry, params + kept_init, "    ", extra_doc))
    lines += [line for note in _behaviour_todos(found) for line in _comment(note, "    ")]
    if row is not None:
        lines += _comment(
            "TODO(review): the legacy code returns a whole dates-by-names table (from "
            f"{found.seen(Trait.RETURNS_TABLE)}), and output over max_output_bytes (100 KB by "
            f"default) is refused, so this facade returns one date's row ({row}). Return "
            "another slice, or raise max_output_bytes in server.py, if callers need more.",
            "    ",
        )
    if found.has(Trait.VAR_ARGS):
        lines += _comment(
            f"TODO(type): the legacy code also takes {found.seen(Trait.VAR_ARGS)}; add a "
            "named parameter for each one callers need.",
            "    ",
        )
    call_args = [_argument(p) for p in params if p.name not in late]
    if late:
        lines.append("    extra: dict[str, Any] = {}")
        for param in params:
            if param.name in late:
                lines.append(f"    if {param.name} is not None:  # else the legacy default")
                lines.append(f'        extra["{param.name}"] = {param.name}')
        call_args.append("**extra")
    if found.owner is not None:
        init_args = [f"{p.name}={p.name}" for p in kept_init]
        lines += _wrapped("    ", f"instance = {callee}", init_args)
        function = f"instance.{found.name}"
    else:
        function = callee
    if _one_at_a_time(found):
        lines.append("    with _legacy():")
        lines += _wrapped("        ", f"result = {function}", call_args)
    else:
        lines += _wrapped("    ", f"result = {function}", call_args)
    if found.has(Trait.RETURNS_NOTHING):
        path = next((p.name for p in params if PATH_NAME.search(p.name)), None)
        message = f'f"Wrote {{{path}}}."' if path else '"Done."'
        lines.append("    del result  # the legacy code returns nothing")
        lines.append(f"    return {message}")
    elif row is not None:
        lines.append(f"    return _cross_section(result, {row})")
    else:
        lines.append(f"    return {'_json_ready(result)' if use_json else 'result'}")
    return "\n".join(lines)


def _slices(found: Found) -> bool:
    """Whether the facade returns one row of the whole table the legacy code returns: a pandas
    table, as the hint says or as untyped code most often returns."""
    hint = found.returns or ""
    return found.has(Trait.RETURNS_TABLE) and not re.search(r"ndarray|NDArray|list|dict", hint)


def _one_at_a_time(found: Found) -> bool:
    """Whether the facade runs the legacy code under `_legacy()`: it prints, or shares module
    state that another thread may be using."""
    return found.has(Trait.PRINTS, Trait.MODULE_CACHE, Trait.MUTABLE_STATE)


def _argument(param: Param) -> str:
    if param.kind == "positional_only":
        return param.name
    return f"{param.name}={param.name}"


def _legacy_import(found: Found, imports: _Imports) -> str:
    name = found.owner or found.name
    alias = f"Legacy{name}" if found.owner else f"legacy_{name}"
    line = f"from {found.module} import {name} as {alias}"
    if imports.clashes(line):  # the same name in two modules
        alias = f"{alias}_{found.module.replace('.', '_')}"
        line = f"from {found.module} import {name} as {alias}"
    imports.add(line)
    return alias


def _hint(
    param: Param, data: DataKind | None, found: Found, imports: _Imports, todos: list[str]
) -> str:
    """The facade's hint for a parameter, reusing the legacy hint where there is one."""
    if param.annotation is not None and param.name in _unsupported(found):
        todos.append(
            f"TODO(type): {param.name}'s legacy hint, {param.annotation}, is not a value a client "
            "can send; build it inside the facade, or leave this entry out. Any stands in."
        )
        return "Any"
    if param.annotation is not None and _reusable(param.annotation, found, imports):
        imports.add(*_imports_for(param.annotation, found))
        return param.annotation
    if param.annotation is not None:
        todos.append(
            f"TODO(type): {param.name}'s legacy hint, {param.annotation}, names something this "
            "file cannot import; Any stands in."
        )
        return "Any"
    if data is not None:
        # Untyped data is most often pandas, and a Series still iterates like a list.
        library = param.library or "pandas"
        if library == "pandas":
            imports.add("import pandas as pd")
            hint = "pd.Series" if data == "series" else "pd.DataFrame"
        elif library == "numpy":
            imports.add("import numpy as np")
            hint = "np.ndarray"
        else:
            hint = "list[float]" if data == "series" else "dict[str, list[float]]"
        shape = "one column" if data == "series" else "columns of one dataset"
        todos.append(
            f"TODO(data): {param.name} is guessed to be {shape}, passed by reference as {hint}; "
            "confirm its shape and unit."
        )
        return hint
    guess = _type_of_default(param.default)
    if guess:
        todos.append(f"TODO(type): {param.name}'s hint, {guess}, is guessed from its default.")
        return guess
    guess = _type_of_name(param.name)
    if guess:
        todos.append(f"TODO(type): {param.name}'s hint, {guess}, is guessed from its name.")
        return guess
    todos.append(f"TODO(type): {param.name} has no hint in the legacy code; name its type.")
    return "Any"


def _unsupported(found: Found) -> set[str]:
    """The parameters, and ``return``, whose hints the scan found no schema for."""
    return {seen.split(":")[0] for seen in found.traits.get(Trait.UNSUPPORTED_TYPE, ())}


BUILTIN_HINTS = frozenset(
    {"int", "float", "str", "bool", "bytes", "complex", "list", "dict", "tuple", "set", "frozenset"}
    | {"object", "type", "None"}
)


def _reusable(hint: str, found: Found, imports: _Imports) -> bool:
    """Whether a legacy hint can be written in a drafted file: every name in it is a builtin or
    has an import line that clashes with no other, and it holds no quoted forward reference."""
    if imports.clashes(*_imports_for(hint, found)):
        return False
    try:
        tree = ast.parse(hint, mode="eval").body
    except SyntaxError:
        return False

    def fine(node: ast.expr) -> bool:
        if isinstance(node, ast.Subscript):
            outer = node.value
            if (isinstance(outer, ast.Name) and outer.id == "Literal") or (
                isinstance(outer, ast.Attribute) and outer.attr == "Literal"
            ):
                return fine(outer)
            return fine(outer) and fine(node.slice)
        if isinstance(node, ast.Attribute):
            return fine(node.value)
        if isinstance(node, ast.Name):
            return node.id in BUILTIN_HINTS or node.id in found.imports
        if isinstance(node, ast.Constant):
            return node.value is None or node.value is Ellipsis
        if isinstance(node, ast.Tuple | ast.List):
            return all(fine(item) for item in node.elts)
        if isinstance(node, ast.BinOp):
            return fine(node.left) and fine(node.right)
        return False

    return fine(tree)


def _imports_for(hint: str, found: Found) -> list[str]:
    try:
        tree = ast.parse(hint, mode="eval")
    except SyntaxError:
        return []
    heads = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id in found.imports
    }
    return [found.imports[head] for head in sorted(heads)]


def _type_of_default(default: str | None) -> str | None:
    if default is None:
        return None
    try:
        value = ast.literal_eval(default)
    except (ValueError, SyntaxError):
        return None
    for kind in (bool, int, float, str):
        if type(value) is kind:
            return kind.__name__
    return None


def _type_of_name(name: str) -> str | None:
    """A hint for an untyped parameter from its name: ``desk`` or ``as_of`` is a string, and
    ``window`` an int, so a client sending the wrong type is refused."""
    if STRING_NAME.search(name) or DATE_NAME.search(name):
        return "str"
    if WINDOW_NAME.search(name):
        return "int"
    return None


def _optional(hint: str, default: str | None) -> str:
    """``hint`` allowing None when the default is None."""
    if default == "None" and hint != "Any" and "None" not in hint.replace(" ", "").split("|"):
        return f"{hint} | None"
    return hint


def _default(param: Param) -> str | None:
    """The legacy default when it can be repeated here: a literal."""
    if param.default is None:
        return None
    try:
        value = ast.literal_eval(param.default)
    except (ValueError, SyntaxError):
        return None
    return json.dumps(value) if isinstance(value, str) else param.default  # "x", as ruff writes


def _returns(found: Found, imports: _Imports, todos: list[str]) -> tuple[str, bool]:
    """The facade's return hint, and whether the result goes through ``_json_ready``."""
    if found.has(Trait.RETURNS_NOTHING):
        return "str", False
    if found.returns is not None and "return" in _unsupported(found):
        todos.append(
            f"TODO(type): the legacy return hint, {found.returns}, may have no JSON schema; "
            "return plain values, a dataclass, or a Pydantic model. Any stands in."
        )
        return "Any", True
    if found.returns is not None and not _reusable(found.returns, found, imports):
        todos.append(
            f"TODO(type): the legacy return hint, {found.returns}, names something this file "
            "cannot import; Any stands in."
        )
        return "Any", True
    if found.returns is not None:
        imports.add(*_imports_for(found.returns, found))
        plain = found.returns in ("float", "int", "str", "bool")
        return found.returns, not plain
    if found.returns_guess is not None:
        todos.append(
            f"TODO(type): the return hint, {found.returns_guess}, is guessed from the return "
            "statements."
        )
        return found.returns_guess, found.returns_guess.startswith("dict")
    todos.append(
        "TODO(type): name the return type, such as float, a dataclass, or pd.Series; Any "
        "gives clients no schema."
    )
    return "Any", True


def _docstring(
    found: Found,
    entry: Entry,
    params: list[Param],
    indent: str,
    extra: list[tuple[str, str]] | None = None,
) -> str:
    args = [f"    {p.name}: TODO(unit): what it holds, and in which unit." for p in params]
    args += [f"    {name}: {text}" for name, text in extra or []]
    if found.doc:
        text = found.doc
        if not re.search(r"^(Args|Arguments|Parameters):?\s*$", text, re.MULTILINE) and args:
            text += "\n\nArgs:\n" + "\n".join(args)
    else:
        text = f"TODO(doc): say what {entry.name} returns, in one sentence; clients read it."
        if args:
            text += "\n\nArgs:\n" + "\n".join(args)
    text = text.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    lines = text.splitlines()
    body = [lines[0], *[f"{indent}{line}" if line.strip() else "" for line in lines[1:]]]
    closing = f"\n{indent}" if len(lines) > 1 else ""
    return f'{indent}"""' + "\n".join(body) + f'{closing}"""'


def _behaviour_todos(found: Found) -> list[str]:
    notes = []
    if found.has(Trait.MODULE_SETTINGS):
        notes.append(
            "TODO(config): the legacy code reads module settings "
            f"({found.seen(Trait.MODULE_SETTINGS)}); "
            "add a parameter for each a caller may choose, and leave the rest the firm's."
        )
    if found.has(Trait.ENV):
        notes.append(
            f"TODO(config): reads the environment ({found.seen(Trait.ENV)}); set it where the "
            "server starts."
        )
    if found.has(Trait.DATABASE):
        notes.append(
            f"TODO(review): uses a database itself ({found.seen(Trait.DATABASE)}); prefer data "
            "by reference, or keep the permission declared in server.py."
        )
    if found.has(Trait.READS_FILES):
        notes.append(
            f"TODO(review): reads files ({found.seen(Trait.READS_FILES)}); prefer data by "
            "reference, or keep read_local_data declared."
        )
    if found.has(Trait.WRITES_FILES):
        notes.append(
            f"TODO(review): writes files ({found.seen(Trait.WRITES_FILES)}) on the server; each "
            "call needs approval. Prefer returning the table for the caller to save."
        )
    if found.has(Trait.NETWORK):
        notes.append(
            f"TODO(review): calls out over the network ({found.seen(Trait.NETWORK)}); network is "
            "declared, and each call needs approval."
        )
    if found.has(Trait.SUBPROCESS):
        notes.append(
            f"TODO(review): runs programs ({found.seen(Trait.SUBPROCESS)}); execute_code is "
            "declared. Prefer calling the code directly."
        )
    if found.has(Trait.MODULE_CACHE):
        notes.append(
            f"TODO(review): reads through a module cache ({found.seen(Trait.MODULE_CACHE)}) "
            "that lives as long as the server, so a result stays as first read until the cache "
            "is cleared or the server restarts; say how fresh the data must be."
        )
    if found.has(Trait.MUTABLE_STATE):
        notes.append(
            f"TODO(review): keeps state between calls ({found.seen(Trait.MUTABLE_STATE)}); tools "
            "run on several threads at once."
        )
    return notes


JSON_READY = '''\
def _json_ready(value: Any) -> Any:
    """The legacy result as JSON values: tables and arrays as lists, missing values as null."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _json_ready(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {_key(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_ready(item) for item in value]
    if hasattr(value, "columns") and hasattr(value, "index"):  # a pandas DataFrame
        return {
            "index": [_key(key) for key in value.index],
            "columns": {str(name): _json_ready(value[name].tolist()) for name in value.columns},
        }
    if hasattr(value, "items") and hasattr(value, "index"):  # a pandas Series
        return {_key(key): _json_ready(item) for key, item in value.items()}
    if hasattr(value, "tolist"):  # a NumPy array or scalar
        return _json_ready(value.tolist())
    if hasattr(value, "model_dump") and not isinstance(value, type):  # a Pydantic model
        return _json_ready(value.model_dump())
    if hasattr(value, "isoformat"):  # a date or time
        return _key(value)
    if value is None or isinstance(value, str | int):
        return value
    raise TypeError(
        f"The legacy result holds a {type(value).__name__}, which JSON cannot carry; "
        "convert it to plain values in the facade."
    )


def _key(key: Any) -> str:
    if hasattr(key, "isoformat"):
        return str(key.isoformat()).removesuffix("T00:00:00")
    return str(key)'''

CROSS_SECTION = '''\
def _cross_section(table: Any, as_of: str | None) -> dict[str, Any]:
    """One date's row of a dates-by-names table: the last, or the one dated ``as_of``."""
    if len(table.index) == 0:
        raise ValueError("The legacy code returned no rows; give it a longer history.")
    dates = [_key(key) for key in table.index]
    if as_of is not None and as_of not in dates:
        raise ValueError(f"No row dated {as_of}; the rows run from {dates[0]} to {dates[-1]}.")
    position = len(dates) - 1 if as_of is None else dates.index(as_of)
    return {"date": dates[position], "values": _json_ready(table.iloc[position])}'''

LEGACY = '''\
# One lock for the legacy calls: redirect_stdout swaps sys.stdout for the whole process, and
# tools run on several threads, so two calls at once could leave stdout pointing at stderr.
# The lock also keeps the legacy code's module state to one call at a time.
_LEGACY_LOCK = threading.RLock()


@contextlib.contextmanager
def _legacy() -> Iterator[None]:
    """Run legacy code one call at a time, its prints sent to stderr: over stdio, stdout
    carries the protocol."""
    with _LEGACY_LOCK, contextlib.redirect_stdout(sys.stderr):
        yield'''


# The service


def _service_module(selected: list[_Selected], legacy: str, scanned: list[Found]) -> str:
    imports = _Imports()
    imports.add(
        "from __future__ import annotations",
        "import dataclasses",
        "import math",
        "import threading",
        "from collections.abc import AsyncIterator",
        "from contextlib import asynccontextmanager",
        "from typing import Any",
        "from fastapi import FastAPI",
        "from pydantic import BaseModel",
    )
    pandas = any(_wants_pandas(s) for s in selected)
    holders: dict[str, Found] = {}
    for s in selected:
        holders.setdefault(_holder(s.found), s.found)
    build = []
    for key, found in holders.items():
        if found.owner is None:
            continue
        alias = _legacy_import(found, imports)
        arguments, todos = _construction(found)
        build += [line for todo in todos for line in _comment(todo, "    ")]
        build.append(f'    OBJECTS["{key}"] = {alias}({arguments})')
        for method in _set_up_methods(found, scanned, selected):
            todo = (
                f"TODO(config): {found.owner}.{method}() looks like set-up, and it is not "
                "served, so the draft calls it once here; delete the call if the object "
                "needs none, or add the firm's arguments."
            )
            build += [*_comment(todo, "    "), f'    OBJECTS["{key}"].{method}()']
    if not build:
        build.append("    # Nothing to build: the legacy functions keep their own module state.")
    endpoints = [_endpoint(s, imports) for s in selected]
    helpers = [JSON_READY]
    if pandas:
        imports.add("import pandas as pd", "import contextlib")
        helpers.append(PANDAS_INPUTS)
    if any(s.entry.data and not _wants_pandas(s) for s in selected):
        helpers.append(PLAIN_INPUTS)
    locks = ", ".join(f'"{key}": threading.Lock()' for key in holders)
    doc = (
        f'"""A draft service for stateful code in {legacy}, written by `qaos legacy wrap`.\n\n'
        "It builds each legacy object once, when it starts, and answers read-only POST requests;\n"
        "stubs.py registers each endpoint as a tool with FirmApp.call_service. Run it with:\n\n"
        f"    uv run uvicorn service.app:app --host 127.0.0.1 --port {SERVICE_PORT}\n"
        '"""'
    )
    setup = "\n".join(
        [
            "OBJECTS: dict[str, Any] = {}",
            "# One lock per legacy object: requests run on several threads, and legacy code is",
            "# rarely thread-safe.",
            "# TODO(review): drop a lock only for code known to be thread-safe.",
            f"LOCKS = {{{locks}}}",
            "",
            "",
            "@asynccontextmanager",
            "async def lifespan(_: FastAPI) -> AsyncIterator[None]:",
            '    """Build each legacy object once: the slow set-up happens here, not per call."""',
            *build,
            "    yield",
            "    OBJECTS.clear()",
            "",
            "",
            f'app = FastAPI(title="{legacy} service (draft)", lifespan=lifespan)',
        ]
    )
    parts = [doc + "\n\n" + imports.render() + "\n\n" + setup, *endpoints, *helpers]
    return "\n\n\n".join(parts).rstrip() + "\n"


def _set_up_methods(owner: Found, scanned: list[Found], selected: list[_Selected]) -> list[str]:
    """Public methods of ``owner``'s class named like set-up (``load``, ``fit``, ...) that take
    no required argument and are not served: an object built without them may not answer."""
    served = {s.found.target for s in selected}
    return [
        item.name
        for item in scanned
        if item.module == owner.module
        and item.owner == owner.owner
        and item.target not in served
        and SLOW_CALL.search(item.name)
        and all(
            p.default is not None or p.kind in ("var_positional", "var_keyword")
            for p in item.params
        )
    ]


def _holder(found: Found) -> str:
    return snake(found.owner) if found.owner else found.name


def _wants_pandas(selected: _Selected) -> bool:
    """Whether data goes to the legacy code as pandas objects: unless every data parameter is
    hinted as something else, such as ``list[float]``."""
    data = [p for p in selected.found.params if selected.entry.data.get(p.name)]
    return any(p.library == "pandas" or p.annotation is None for p in data)


def _construction(found: Found) -> tuple[str, list[str]]:
    arguments, todos = [], []
    for param in found.init_params:
        if param.kind in ("var_positional", "var_keyword") or param.default is not None:
            continue
        arguments.append(f"{param.name}=None")
        todos.append(
            f"TODO(config): {found.owner}'s {param.name}; the legacy code gives no default."
        )
    defaulted = [p.name for p in found.init_params if p.default is not None]
    if defaulted:
        todos.append(
            f"TODO(config): {found.owner} is built with its defaults for {', '.join(defaulted)}; "
            "set the firm's values."
        )
    return ", ".join(arguments), todos


def _endpoint(selected: _Selected, imports: _Imports) -> str:
    found, entry = selected.found, selected.entry
    pandas = _wants_pandas(selected)
    model = "".join(part.capitalize() for part in entry.name.split("_")) + "Request"
    fields, conversions = [], []
    for param in found.params:
        if param.kind in ("var_positional", "var_keyword"):
            continue
        data = entry.data.get(param.name)
        hint = _wire_hint(param, data, pandas)
        default = _default(param)
        optional = param.default is not None
        if optional and default is None:
            fields.append(
                f"    {param.name}: {hint} | None = None  # legacy default: {param.default}"
            )
        elif optional:
            fields.append(f"    {param.name}: {_optional(hint, default)} = {default}")
        else:
            fields.append(f"    {param.name}: {hint}")
        if data is not None:
            convert = {
                ("series", True): "_series",
                ("frame", True): "_frame",
                ("series", False): "_values",
                ("frame", False): "_columns",
            }[(data, pandas)]
            line = f'arguments["{param.name}"] = {convert}(arguments["{param.name}"])'
            if optional:
                conversions += [f'    if "{param.name}" in arguments:', f"        {line}"]
            else:
                conversions.append(f"    {line}")
    if not fields:
        fields.append("    pass")
    key = _holder(found)
    if found.owner is not None:
        call = f'OBJECTS["{key}"].{found.name}(**arguments)'
    else:
        call = f"{_legacy_import(found, imports)}(**arguments)"
    summary = (found.doc or f"TODO(doc): what {entry.name} returns.").split("\n\n")[0]
    summary = " ".join(summary.split()).replace('"""', "'''")
    lines = [f"class {model}(BaseModel):", f'    """The arguments of {entry.name}."""', "", *fields]
    lines += [
        "",
        "",
        f'@app.post("/{entry.name}")',
        f"def {entry.name}(request: {model}) -> Any:",
        f'    """{summary}"""',
        "    arguments = request.model_dump(exclude_none=True)  # None: the legacy default",
        *conversions,
        f'    with LOCKS["{key}"]:',
        f"        result = {call}",
        "    return _json_ready(result)",
    ]
    return "\n".join(lines)


def _wire_hint(param: Param, data: DataKind | None, pandas: bool) -> str:
    """How a value travels in JSON: what `FirmApp.call_service` sends for the stub's hint."""
    if data == "series":
        return "dict[str, float | None]" if pandas else "list[float | None]"
    if data == "frame":
        return "dict[str, Any]" if pandas else "dict[str, list[float | None]]"
    if param.annotation and SIMPLE_HINT.match(param.annotation):
        return param.annotation
    return _type_of_default(param.default) or _type_of_name(param.name) or "Any"


PANDAS_INPUTS = '''\
def _series(values: dict[str, float | None]) -> pd.Series:
    """A Series from what the stub sends: values by date, missing values as null."""
    series = pd.Series(
        {key: math.nan if value is None else value for key, value in values.items()}, dtype=float
    )
    with contextlib.suppress(ValueError, TypeError):
        series.index = pd.to_datetime(series.index)
    return series


def _frame(value: dict[str, Any]) -> pd.DataFrame:
    """A DataFrame from what the stub sends: an index and named columns."""
    columns = {
        str(name): [math.nan if item is None else item for item in items]
        for name, items in value["columns"].items()
    }
    frame = pd.DataFrame(columns, index=value.get("index"), dtype=float)
    with contextlib.suppress(ValueError, TypeError):
        frame.index = pd.to_datetime(frame.index)
    return frame'''

PLAIN_INPUTS = '''\
def _values(values: list[float | None]) -> list[float]:
    """Numbers from what the stub sends, with missing values as NaN."""
    return [math.nan if value is None else value for value in values]


def _columns(columns: dict[str, list[float | None]]) -> dict[str, list[float]]:
    """Named columns from what the stub sends, with missing values as NaN."""
    return {name: _values(values) for name, values in columns.items()}'''


def _stubs_module(selected: list[_Selected], legacy: str) -> str:
    imports = _Imports()
    imports.add(
        "from __future__ import annotations",
        "from typing import Any",
        "from quant_agent_os.registration import FirmApp",
    )
    stubs, registrations = [], []
    for s in selected:
        found, entry = s.found, s.entry
        pandas = _wants_pandas(s)
        todos: list[str] = []
        signature = []
        for param in found.params:
            if param.kind in ("var_positional", "var_keyword"):
                continue
            data = entry.data.get(param.name)
            if data is not None and pandas:
                imports.add("import pandas as pd")
                hint = "pd.Series" if data == "series" else "pd.DataFrame"
            elif data is not None:
                hint = "list[float]" if data == "series" else "dict[str, list[float]]"
            elif param.annotation and SIMPLE_HINT.match(param.annotation):
                hint = param.annotation
            else:
                by_default = _type_of_default(param.default)
                hint = by_default or _type_of_name(param.name) or "Any"
                source = "its default" if by_default else "its name"
                what = f"guessed from {source}, is {hint}" if hint != "Any" else "is Any"
                todos.append(f"TODO(type): {param.name}'s hint {what}; name its type.")
            default = _default(param)
            if param.default is not None and default is None:
                signature.append(f"{param.name}: {hint} | None = None")
            elif param.default is not None:
                signature.append(f"{param.name}: {_optional(hint, default)} = {default}")
            else:
                signature.append(f"{param.name}: {hint}")
        returns = found.returns if found.returns and SIMPLE_HINT.match(found.returns) else None
        if returns is None:
            returns = "Any"
            todos.append(
                "TODO(type): name the return type, so the answer is checked; Any accepts anything."
            )
        doc = _docstring(
            found,
            entry,
            [p for p in found.params if p.kind not in ("var_positional", "var_keyword")],
            "    ",
        )
        stubs.append(
            "\n".join(
                [
                    *(line for todo in todos for line in _comment(todo)),
                    *_wrapped("", f"def {entry.name}", signature, f" -> {returns}:"),
                    doc,
                    "    raise NotImplementedError  # never runs: the service answers",
                ]
            )
        )
        options = [f'url=f"{{service_url}}/{entry.name}"']
        options += _data_options(entry, imports)
        options += _permission_options(entry, imports)
        if found.has(Trait.RETURNS_TABLE):
            registrations += _comment(
                f"TODO(review): {entry.name} returns a whole dates-by-names table, and output "
                "over max_output_bytes (100 KB by default) is refused; return a slice from a "
                "facade, or pass max_output_bytes.",
                "    ",
            )
        if entry.convention:
            options.append(f"convention={entry.convention!r}")
        else:
            registrations += _comment(
                f"TODO(convention): how {entry.name}'s result is defined (method, window, units).",
                "    ",
            )
        registrations += _wrapped("    ", "app.call_service", [entry.name, *options])
    doc = (
        f'"""Typed stubs for the draft service over {legacy}, written by `qaos legacy wrap`.\n\n'
        "Each stub's hints and docstring describe one endpoint of service/app.py; its body never\n"
        'runs. `register` adds them to an app with FirmApp.call_service.\n"""'
    )
    register = "\n".join(
        [
            "def register(app: FirmApp, service_url: str) -> None:",
            '    """Register each endpoint of the service at ``service_url`` as a tool."""',
            *registrations,
        ]
    )
    return "\n\n\n".join([doc + "\n\n" + imports.render(), *stubs, register]).rstrip() + "\n"


def _data_options(entry: Entry, imports: _Imports) -> list[str]:
    options = []
    for name, kind in entry.data.items():
        marker = "Series" if kind == "series" else "Frame"
        imports.add(f"from quant_agent_os.registration import {marker}")
        options.append(f"{name}={marker}")
    return options


def _permission_options(entry: Entry, imports: _Imports) -> list[str]:
    if not entry.permissions:
        return []
    imports.add("from quant_agent_os.tools import Permission")
    names = ", ".join(f"Permission.{permission.name}" for permission in entry.permissions)
    return [f"permissions=[{names}]"]


# The server


def _server_module(
    app: str, selected: list[_Selected], legacy: str, services: bool, package: str
) -> str:
    imports = _Imports()
    imports.add(
        "from __future__ import annotations",
        "from pathlib import Path",
        "from quant_agent_os.registration import FirmApp",
    )
    lines = []
    for s in selected:
        found, entry = s.found, s.entry
        if entry.kind is Kind.SERVICE:
            continue
        options: list[str] = []
        if entry.kind is Kind.EXPOSE:
            function = found.name
            line = f"from {found.module} import {function}"
            if imports.clashes(line) or function in ("app", "build_app", "main", "SERVICE_URL"):
                function = f"legacy_{entry.name}"
                line = f"from {found.module} import {found.name} as {function}"
            imports.add(line)
            if entry.name != found.name:
                options.append(f'name="{entry.name}"')
        else:
            imports.add("from . import facades")
            function = f"facades.{entry.name}"
        options += _data_options(entry, imports)
        options += _permission_options(entry, imports)
        if found.has(Trait.NONDETERMINISTIC):
            options.append("deterministic=False")
        if found.has(Trait.RETURNS_TABLE) and entry.kind is Kind.EXPOSE:
            lines += _comment(
                f"TODO(review): {entry.name} returns a whole dates-by-names table, and output "
                "over max_output_bytes (100 KB by default) is refused; return a slice from a "
                "facade, or pass max_output_bytes.",
                "    ",
            )
        if entry.permissions:
            granted = ", ".join(permission.value for permission in entry.permissions)
            lines += _comment(
                f"TODO(review): {entry.name} declares {granted}, from what the scan saw; check "
                "the code needs no more.",
                "    ",
            )
        if entry.convention:
            options.append(f"convention={entry.convention!r}")
        else:
            lines += _comment(
                f"TODO(convention): how {entry.name}'s figure is defined (window, "
                "annualisation, units).",
                "    ",
            )
        lines += _wrapped("    ", "app.expose", [function, *options])
    if services:
        imports.add("from . import stubs")
        lines.append("    stubs.register(app, service_url)")
    doc = (
        f'"""{legacy} as tools: a draft written by `qaos legacy wrap`; edit it and commit it.\n\n'
        "Every tool is named here, in the firm's own code (decisions 0020 and 0024); nothing is\n"
        "discovered when the server starts. A firm's server imports build_app from\n"
        f"{package}.server; to serve the draft alone, run this from the folder above it:\n\n"
        f"    uv run python -m {package}.server\n"
        '"""'
    )
    service_url = (
        "# TODO(review): where service/app.py runs; use https off this machine.\n"
        if services
        else "# No service in this draft.\n"
    ) + f'SERVICE_URL = "http://127.0.0.1:{SERVICE_PORT}"'
    build = "\n".join(
        [
            "def build_app(service_url: str = SERVICE_URL) -> FirmApp:",
            '    """Return the app, with each accepted entry registered by name."""',
            "    app = FirmApp(",
            f'        "{app}",',
            "        instructions=None,  # TODO(doc): tell clients when to prefer these tools",
            "    )",
            *lines,
            "    return app",
        ]
    )
    main = "\n".join(
        [
            "def main() -> None:",
            '    """Serve the app beside the platform\'s toolsets, with the usual settings."""',
            "    from quant_agent_os.data import DirectoryDatasetStore",
            "    from quant_platform import serve_apps",
            "",
            "    # TODO(review): serve the firm's own DatasetStore; CSV files in a directory are",
            "    #   the simplest.",
            '    serve_apps([build_app()], datasets=DirectoryDatasetStore(Path("datasets")))',
        ]
    )
    tail = 'if __name__ == "__main__":\n    main()'
    head = doc + "\n\n" + imports.render() + "\n\n" + service_url  # one line after imports
    return "\n\n\n".join([head, build, main, tail]) + "\n"


# The test


def _conftest(source_root: str) -> str:
    where = (
        f"Path({json.dumps(source_root)})"
        if source_root.startswith("/") or ":" in source_root
        else f"Path(__file__).parent / {json.dumps(source_root)}"
    )
    root = f"ROOT = {where}"
    if len(root) > WIDTH:
        root = f"ROOT = Path(\n    {json.dumps(source_root)}\n)"
    return (
        '"""Lets the draft\'s test import the legacy code; written by `qaos legacy wrap`."""\n\n'
        "import sys\n"
        "from pathlib import Path\n\n"
        "# Delete these lines once the legacy package is installed in this environment.\n"
        f"{root}\n"
        "sys.path.insert(0, str(ROOT.resolve()))\n"
    )


def _example(selected: _Selected, samples: _Samples) -> _Example:
    found, entry = selected.found, selected.entry
    arguments: list[tuple[str, str, bool]] = []
    params = [*found.params, *found.init_params] if entry.kind is Kind.FACADE else found.params
    seen: set[str] = set()
    for param in params:
        data = entry.data.get(param.name)
        # An optional data parameter gets an example too: without one, the legacy code falls
        # back to reading the firm's data itself.
        if param.kind in ("var_positional", "var_keyword") or (
            param.default is not None and data is None
        ):
            continue
        if param.name in seen:
            continue
        seen.add(param.name)
        kind = "price" if PRICE_NAME.search(param.name) else "return"
        if data == "series":
            samples.series.setdefault(param.name, kind)
            arguments.append(
                (param.name, f'{{"dataset": "sample", "column": "{param.name}"}}', False)
            )
        elif data == "frame":
            samples.frames.setdefault(param.name, kind)
            arguments.append((param.name, f'{{"dataset": "sample_{param.name}"}}', False))
        elif PATH_NAME.search(param.name):
            arguments.append((param.name, f'str(SCRATCH / "{entry.name}.csv")', False))
        else:
            arguments.append((param.name, _value_for(param), True))
    return _Example(entry.name, arguments)


def _value_for(param: Param) -> str:
    """A placeholder of the parameter's type, or of the type its name suggests: never a number
    for what may be a name, so a wrong guess fails instead of passing."""
    hint = (param.annotation or "").replace(" ", "") or _type_of_name(param.name) or ""
    if hint.startswith(("list[float]", "Sequence[float]", "tuple[float")):
        return "[0.01, -0.02, 0.015]"
    if hint.startswith(("dict", "Mapping")):
        return "{}"
    if hint == "bool":
        return "False"
    if hint == "str":
        return f'"{SAMPLE_DATE}"' if DATE_NAME.search(param.name) else '"example"'
    if hint == "int" or (not hint and WINDOW_NAME.search(param.name)):
        return "20" if WINDOW_NAME.search(param.name) else "1"
    if "conf" in param.name:
        return "0.95"
    return "1.0"


def _test_module(examples: list[_Example], samples: _Samples, services: bool) -> str:
    imports = _Imports()
    imports.add(
        "from __future__ import annotations",
        "import datetime",
        "import math",
        "import tempfile",
        "from pathlib import Path",
        "from typing import Any",
        "from quant_agent_os.data import Dataset, UnknownDatasetError, parse_dataset",
        "from quant_agent_os.testing import check_app",
        "import pytest",
        "from . import server",
    )
    entries = []
    for example in examples:
        if not example.arguments:
            entries.append(f'    "{example.tool}": {{}},')
            continue
        entries.append(f'    "{example.tool}": {{')
        for name, value, guessed in example.arguments:
            note = "  # TODO(data): a realistic value; this one is a guess" if guessed else ""
            entries.append(f'        "{name}": {value},{note}')
        entries.append("    },")
    datasets = []
    if samples.series:
        columns = ", ".join(f'"{name}": "{kind}"' for name, kind in samples.series.items())
        datasets.append(f'    _sample("sample", {{{columns}}}),')
    for name, kind in samples.frames.items():
        columns = ", ".join(f'"{asset}": "{kind}"' for asset in ("asset_a", "asset_b", "asset_c"))
        datasets.append(f'    _sample("sample_{name}", {{{columns}}}),')
    if services:
        imports.add("import threading", "import time", "import uvicorn")
        fixture = SERVICE_FIXTURE
    else:
        fixture = NO_SERVICE_FIXTURE
    doc = (
        '"""Contract test for the drafted tools, written by `qaos legacy wrap`.\n\n'
        "Run it, fix what fails, and run it again:\n\n"
        "    uv run pytest test_tools.py\n\n"
        "The sample data is made up; replace it with a small sample of the firm's own.\n"
        '"""'
    )
    body = "\n".join(
        [
            "HERE = Path(__file__).parent",
            'SCRATCH = Path(tempfile.mkdtemp(prefix="legacy-tools-"))  # where writing tools write',
            f"DRAFTED = {json.dumps(list(DRAFTED))}",
            "ROWS = 260",
        ]
    )
    store = "\n".join(
        [
            "STORE = SampleStore(",
            *(datasets or ['    _sample("sample", {"returns": "return"}),']),
            ")",
            "EXAMPLES: dict[str, dict[str, Any]] = {",
            *entries,
            "}",
        ]
    )
    return (
        "\n\n\n".join(
            [doc + "\n\n" + imports.render() + "\n\n" + body, SAMPLE_HELPERS, store, fixture, TESTS]
        )
        + "\n"
    )


SAMPLE_HELPERS = '''\
def _dates(count: int) -> list[str]:
    day, dates = datetime.date(2024, 1, 2), []
    while len(dates) < count:
        if day.weekday() < 5:
            dates.append(day.isoformat())
        day += datetime.timedelta(days=1)
    return dates


def _sample(dataset_id: str, columns: dict[str, str]) -> Dataset:
    """Made-up business-day data: "price" columns wander near 100, "return" columns near 0."""
    lines = ["date," + ",".join(columns)]
    for row, date in enumerate(_dates(ROWS)):
        cells = []
        for number, kind in enumerate(columns.values(), start=1):
            wave = math.sin(row * 0.37 * number) + math.cos(row * 0.11 + number)
            value = 100 + 5 * wave + 0.05 * row if kind == "price" else 0.01 * wave
            cells.append(f"{value:.6f}")
        lines.append(f"{date}," + ",".join(cells))
    return parse_dataset(dataset_id, "\\n".join(lines).encode())


class SampleStore:
    """Datasets held in memory, as a firm's own DatasetStore would serve them."""

    def __init__(self, *datasets: Dataset) -> None:
        self.datasets = {dataset.id: dataset for dataset in datasets}

    def get(self, dataset_id: str) -> Dataset:
        if dataset_id not in self.datasets:
            raise UnknownDatasetError(f"No dataset '{dataset_id}'.")
        return self.datasets[dataset_id]

    def ids(self) -> list[str]:
        return sorted(self.datasets)'''

SERVICE_FIXTURE = '''\
@pytest.fixture(scope="module")
def service_url() -> Any:
    """Serve service/app.py on a free loopback port for the test, then stop it."""
    from .service.app import app

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    running = uvicorn.Server(config)
    thread = threading.Thread(target=running.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not running.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("service/app.py did not start; run it with uvicorn to see why.")
        time.sleep(0.05)
    port = running.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    running.should_exit = True
    thread.join(timeout=10)'''

NO_SERVICE_FIXTURE = '''\
@pytest.fixture(scope="module")
def service_url() -> str:
    """No service in this draft."""
    return server.SERVICE_URL'''

TESTS = '''\
def test_every_tool_meets_the_contract(service_url: str) -> None:
    """check_app calls each tool with its example, directly and over MCP, and checks the output."""
    check_app(server.build_app(service_url), EXAMPLES, datasets=STORE)


def test_no_decision_is_left_open() -> None:
    """The draft marks what it could not decide; decide each with the developer, then delete it."""
    marker = "TO" + "DO"
    open_items = [
        f"{name}:{number}: {line.strip()}"
        for name in DRAFTED
        if (HERE / name).exists()
        for number, line in enumerate((HERE / name).read_text("utf-8").splitlines(), 1)
        if marker in line
    ]
    assert not open_items, f"{len(open_items)} open decisions:\\n" + "\\n".join(open_items)'''


def describe_next_steps(out: str, draft_: Draft, services: bool) -> list[str]:
    """What the developer does next, in order."""
    package = PurePath(out).name
    steps = [f"uv run pytest {out}/test_tools.py   (needs quant-agent-os installed)"]
    if services:
        steps[0] += "; the service also needs fastapi and uvicorn"
    steps += [
        "Read each failure, fix the facade or the inventory, and run it again.",
        f"Decide each of the {len(draft_.todos)} TODOs with the developer, then delete it; "
        "test_no_decision_is_left_open lists those left.",
        f"Done when both tests pass. The draft is the package {package}: a firm's server "
        f"imports build_app from {package}.server; to serve it alone, run "
        f"uv run python -m {package}.server from the folder above it.",
    ]
    return steps
