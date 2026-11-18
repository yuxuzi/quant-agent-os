"""Read legacy Python with `ast`, never by importing it (decision 0029).

The code may need the fund's database, network, or heavy dependencies to import, so nothing here
runs it. What it finds is a heuristic: names are resolved through each module's imports, and a
helper's behaviour passes to its callers within the scanned code. It misses what only running
would show, such as a method called on an object passed in.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from typing import Literal

from quant_app_factory.domain.legacy import BEHAVIOUR, DataKind, Found, Param, ParamKind, Trait

DATABASE_CALLS = (
    "sqlite3.connect",
    "duckdb.connect",
    "psycopg.connect",
    "psycopg2.connect",
    "pyodbc.connect",
    "pymysql.connect",
    "mysql.connector.connect",
    "cx_Oracle.connect",
    "oracledb.connect",
    "snowflake.connector.connect",
    "sqlalchemy.create_engine",
    "pandas.read_sql",
    "pandas.read_sql_query",
    "pandas.read_sql_table",
    "pymongo.MongoClient",
    "redis.Redis",
)
DATABASE_METHODS = frozenset(
    {"cursor", "execute", "executemany", "fetchall", "fetchone", "fetchmany", "commit"}
)
NETWORK_MODULES = (
    "requests",
    "httpx",
    "urllib.request",
    "urllib3",
    "aiohttp",
    "socket",
    "http.client",
    "smtplib",
    "ftplib",
    "boto3",
    "botocore",
    "paramiko",
    "websocket",
    "websockets",
    "grpc",
)
SUBPROCESS_CALLS = ("subprocess.", "os.system", "os.popen", "os.spawn", "os.exec")
READ_CALLS = (
    "pandas.read_",
    "numpy.load",
    "numpy.loadtxt",
    "numpy.genfromtxt",
    "numpy.fromfile",
    "json.load",
    "pickle.load",
    "joblib.load",
    "yaml.load",
    "yaml.safe_load",
    "h5py.File",
    "pyarrow.parquet.read_table",
    "tomllib.load",
)
READ_METHODS = frozenset({"read_text", "read_bytes"})
WRITE_CALLS = (
    "numpy.save",
    "numpy.savez",
    "numpy.savetxt",
    "json.dump",
    "pickle.dump",
    "joblib.dump",
    "shutil.",
    "os.remove",
    "os.unlink",
    "os.makedirs",
    "os.mkdir",
    "os.rename",
    "os.replace",
    "os.rmdir",
)
WRITE_METHODS = frozenset(
    {
        "to_csv",
        "to_parquet",
        "to_excel",
        "to_pickle",
        "to_hdf",
        "to_feather",
        "to_sql",
        "write_text",
        "write_bytes",
        "savefig",
        "mkdir",
        "unlink",
        "rmdir",
        "touch",
    }
)
NONDETERMINISTIC_CALLS = (
    "random.",
    "numpy.random.",
    "datetime.datetime.now",
    "datetime.datetime.today",
    "datetime.datetime.utcnow",
    "datetime.date.today",
    "time.time",
    "uuid.uuid1",
    "uuid.uuid4",
    "secrets.",
)
CACHE_DECORATORS = ("functools.lru_cache", "functools.cache", "functools.cached_property")
MUTATORS = frozenset(
    {"append", "extend", "insert", "update", "setdefault", "pop", "popitem", "clear", "add"}
)
SLOW_CALL = re.compile(r"(^|_)(load|fit|train|calibrate|download|warm|estimate|bootstrap)")
PANDAS_ATTRIBUTES = frozenset(
    {
        "rolling",
        "pct_change",
        "shift",
        "resample",
        "groupby",
        "iloc",
        "loc",
        "dropna",
        "fillna",
        "ewm",
        "diff",
        "cumsum",
        "cumprod",
        "index",
        "columns",
        "apply",
        "corr",
        "cov",
        "clip",
        "rank",
        "iterrows",
        "itertuples",
        "to_numpy",
        "reindex",
        "align",
        "to_csv",
        "to_frame",
    }
)
FRAME_ATTRIBUTES = frozenset({"columns", "iterrows", "itertuples", "to_csv", "to_excel"})
DATA_NAMES = re.compile(
    r"^(returns?|rets|prices?|px|close|levels?|nav|pnl|series|signals?|weights|positions|"
    r"holdings|exposures|factors|df|frame|panel|data|universe)$|(_returns|_prices|_df|_series)$"
)
FRAME_NAMES = re.compile(
    r"^(df|frame|panel|data|positions|holdings|exposures|factors|universe)$|_df$"
)
# Names a hint may use that `FirmApp.expose` turns into a schema.
SUPPORTED = frozenset(
    {
        "int",
        "float",
        "str",
        "bool",
        "None",
        "list",
        "dict",
        "tuple",
        "typing.Any",
        "typing.List",
        "typing.Dict",
        "typing.Tuple",
        "typing.Optional",
        "typing.Union",
        "typing.Literal",
        "typing.Annotated",
        "typing.Sequence",
        "typing.Mapping",
        "collections.abc.Sequence",
        "collections.abc.Mapping",
        "datetime.date",
        "datetime.datetime",
        "pandas.Series",
        "pandas.DataFrame",
        "numpy.ndarray",
        "numpy.typing.NDArray",
        "numpy.float64",
        "numpy.int64",
    }
)
DATA_TYPE_BASES = frozenset(
    {"pydantic.BaseModel", "typing.TypedDict", "typing_extensions.TypedDict", "typing.NamedTuple"}
)
SKIPPED_BASES = frozenset(
    {"Enum", "IntEnum", "StrEnum", "Flag", "Exception", "BaseException", "Protocol", "ABC"}
)


@dataclass(frozen=True)
class ModuleSource:
    """One module's dotted name and source text."""

    name: str
    text: str
    is_package: bool = False  # an __init__.py, whose relative imports start at itself


@dataclass
class _Module:
    """What a module defines and imports, for resolving names in its bodies."""

    name: str
    tree: ast.Module
    imports: dict[str, str] = field(default_factory=dict)  # local name -> dotted origin
    import_lines: dict[str, str] = field(default_factory=dict)  # local name -> import statement
    settings: dict[str, str] = field(default_factory=dict)  # global -> "setting", "env", ...
    connections: dict[str, str] = field(default_factory=dict)  # global -> the connect call
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = field(default_factory=dict)
    classes: dict[str, ast.ClassDef] = field(default_factory=dict)
    public: set[str] | None = None  # from __all__, when given

    def resolve(self, dotted: str) -> str:
        """``dotted`` with its first part replaced by what the module imported under that name."""
        head, _, rest = dotted.partition(".")
        origin = self.imports.get(head)
        if origin is None:
            if head in self.functions or head in self.classes:
                origin = f"{self.name}.{head}"
            else:
                return dotted
        return f"{origin}.{rest}" if rest else origin


@dataclass
class _Facts:
    """What one body was seen to do."""

    traits: dict[Trait, set[str]] = field(default_factory=lambda: defaultdict(set))
    calls: set[str] = field(default_factory=set)  # resolved names called
    self_calls: set[str] = field(default_factory=set)  # self.method() calls
    attributes: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    frame_use: set[str] = field(default_factory=set)  # params used as tables
    self_stores: set[str] = field(default_factory=set)  # self.x assigned or changed

    def add(self, trait: Trait, seen: str) -> None:
        self.traits[trait].add(seen)


def analyse(sources: Iterable[ModuleSource]) -> tuple[list[Found], list[str]]:
    """Return every public function and method in ``sources``, and the files skipped, with why.

    Nothing is imported or run: each module is parsed, and names are resolved through its
    imports.
    """
    modules: list[_Module] = []
    skipped: list[str] = []
    for source in sources:
        try:
            tree = ast.parse(source.text)
        except SyntaxError as error:
            skipped.append(f"{source.name}: cannot parse line {error.lineno}: {error.msg}")
            continue
        modules.append(_index(source, tree))
    data_types = {
        f"{module.name}.{name}"
        for module in modules
        for name, node in module.classes.items()
        if _is_data_type(module, node)
    }
    facts: dict[str, _Facts] = {}
    for module in modules:
        for name, node in module.functions.items():
            facts[f"{module.name}.{name}"] = _body(module, node, _param_names(node))
        for class_name, class_node in module.classes.items():
            for item in class_node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    facts[f"{module.name}.{class_name}.{item.name}"] = _body(
                        module, item, _param_names(item)
                    )
    _propagate(facts)
    found: list[Found] = []
    for module in modules:
        for name, node in module.functions.items():
            if _is_public(module, name):
                found.append(_function(module, node, facts, data_types))
        for name, class_node in module.classes.items():
            if _is_public(module, name) and not _skipped_class(module, class_node, data_types):
                found += _methods(module, class_node, facts, data_types)
    return found, skipped


# Indexing a module


def _index(source: ModuleSource, tree: ast.Module) -> _Module:
    module = _Module(source.name, tree)
    package = source.name if source.is_package else source.name.rpartition(".")[0]
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                module.imports[local] = alias.name if alias.asname else local
                module.import_lines[local] = (
                    f"import {alias.name} as {alias.asname}"
                    if alias.asname
                    else f"import {alias.name.split('.')[0]}"
                )
        elif isinstance(node, ast.ImportFrom):
            origin = _absolute(package, node.module, node.level)
            for alias in node.names:
                local = alias.asname or alias.name
                module.imports[local] = f"{origin}.{alias.name}"
                suffix = f" as {alias.asname}" if alias.asname else ""
                module.import_lines[local] = f"from {origin} import {alias.name}{suffix}"
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            module.functions[node.name] = node
        elif isinstance(node, ast.ClassDef):
            module.classes[node.name] = node
    for node in tree.body:
        targets, value = _assignment(node)
        for target in targets:
            if target == "__all__":
                module.public = _names_in(value)
            elif value is not None:
                kind, seen = _global_kind(module, value)
                if kind == "connection":
                    module.connections[target] = seen
                elif kind != "constant":
                    module.settings[target] = kind
    return module


def _absolute(package: str, name: str | None, level: int) -> str:
    if level == 0:
        return name or ""
    parts = package.split(".") if package else []
    base = parts[: len(parts) - (level - 1)] if level > 1 else parts
    return ".".join([*base, *([name] if name else [])])


def _assignment(node: ast.stmt) -> tuple[list[str], ast.expr | None]:
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)], node.value
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id], node.value
    return [], None


def _names_in(value: ast.expr | None) -> set[str] | None:
    if isinstance(value, ast.List | ast.Tuple):
        return {
            item.value
            for item in value.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        }
    return None


def _global_kind(module: _Module, value: ast.expr) -> tuple[str, str]:
    """'constant', 'connection', 'env', or 'setting', and the call seen for a connection."""
    try:
        literal = ast.literal_eval(value)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        literal = ...
    if literal is not ... and isinstance(literal, int | float | str | bytes | bool | tuple):
        return "constant", ""
    if literal is None:
        return "constant", ""
    for node in ast.walk(value):
        if isinstance(node, ast.Call):
            called = module.resolve(_dotted(node.func) or "")
            if called.startswith(DATABASE_CALLS):
                return "connection", called
        dotted = _dotted(node) if isinstance(node, ast.Attribute | ast.Name) else None
        if dotted and module.resolve(dotted) in ("os.environ", "os.getenv"):
            return "env", ""
    return "setting", ""


def _is_public(module: _Module, name: str) -> bool:
    if module.public is not None:
        return name in module.public
    return not name.startswith("_")


def _is_data_type(module: _Module, node: ast.ClassDef) -> bool:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if module.resolve(_dotted(target) or "") in ("dataclasses.dataclass", "dataclass"):
            return True
    return any(module.resolve(_dotted(base) or "") in DATA_TYPE_BASES for base in node.bases)


def _skipped_class(module: _Module, node: ast.ClassDef, data_types: set[str]) -> bool:
    if f"{module.name}.{node.name}" in data_types:
        return True
    return any((_dotted(base) or "").split(".")[-1] in SKIPPED_BASES for base in node.bases)


# Bodies


def _param_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    arguments = node.args
    every = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
    every += [item for item in (arguments.vararg, arguments.kwarg) if item]
    return {argument.arg for argument in every}


def _body(
    module: _Module, node: ast.FunctionDef | ast.AsyncFunctionDef, params: set[str]
) -> _Facts:
    facts = _Facts()
    local = set(params)
    declared_global: set[str] = set()
    for item in ast.walk(node):
        if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store):
            local.add(item.id)
        elif isinstance(item, ast.Global):
            declared_global.update(item.names)
    local -= declared_global
    for name in declared_global:
        facts.add(Trait.MUTABLE_STATE, f"global {name}")
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        called = module.resolve(_dotted(target) or "")
        if called.startswith(CACHE_DECORATORS) or called.startswith("cachetools."):
            facts.add(Trait.CACHE, f"@{called.rsplit('.', 1)[-1]}")
    for item in _walk_body(node):
        _visit(module, item, facts, local, params)
    return facts


def _walk_body(node: ast.AST) -> Iterator[ast.AST]:
    """Every node in a function's body, without its decorators or annotations."""
    for statement in getattr(node, "body", []):
        yield from ast.walk(statement)


def _visit(
    module: _Module, item: ast.AST, facts: _Facts, local: set[str], params: set[str]
) -> None:
    if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load) and item.id not in local:
        if item.id in module.connections:
            facts.add(Trait.DATABASE, f"{item.id} ({module.connections[item.id]})")
        elif module.settings.get(item.id) == "env":
            facts.add(Trait.ENV, item.id)
    elif isinstance(item, ast.Subscript):
        _subscript(module, item, facts, local, params)
    elif isinstance(item, ast.Attribute):
        _attribute(module, item, facts, local, params)
    elif isinstance(item, ast.Call):
        _call(module, item, facts, local)
    elif isinstance(item, ast.AugAssign | ast.Assign):
        targets = item.targets if isinstance(item, ast.Assign) else [item.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id not in local:
                facts.add(Trait.MUTABLE_STATE, target.id)  # only after `global`


def _subscript(
    module: _Module, item: ast.Subscript, facts: _Facts, local: set[str], params: set[str]
) -> None:
    value = item.value
    if isinstance(value, ast.Name) and value.id not in local and value.id in module.settings:
        key = item.slice
        shown = f"{value.id}[{key.value!r}]" if isinstance(key, ast.Constant) else value.id
        if isinstance(item.ctx, ast.Store | ast.Del):
            facts.add(Trait.MUTABLE_STATE, value.id)
        elif module.settings[value.id] != "env":
            facts.add(Trait.MODULE_SETTINGS, shown)
    if isinstance(value, ast.Name) and value.id in params:
        key = item.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            facts.frame_use.add(value.id)  # data["close"]: a table with named columns
    if (
        isinstance(item.ctx, ast.Store | ast.Del)
        and isinstance(value, ast.Attribute)
        and isinstance(value.value, ast.Name)
        and value.value.id == "self"
    ):
        facts.self_stores.add(value.attr)


def _attribute(
    module: _Module, item: ast.Attribute, facts: _Facts, local: set[str], params: set[str]
) -> None:
    dotted = _dotted(item)
    if dotted and dotted.split(".")[0] not in local and module.resolve(dotted) == "os.environ":
        facts.add(Trait.ENV, "os.environ")
    if isinstance(item.value, ast.Name):
        owner = item.value.id
        if owner in params:
            facts.attributes[owner].add(item.attr)
        if owner == "self" and isinstance(item.ctx, ast.Store):
            facts.self_stores.add(item.attr)
        if (
            owner not in local
            and owner in module.settings
            and module.settings[owner] != "env"
            and isinstance(item.ctx, ast.Load)
        ):
            facts.add(Trait.MODULE_SETTINGS, f"{owner}.{item.attr}")


def _call(module: _Module, item: ast.Call, facts: _Facts, local: set[str]) -> None:
    func = item.func
    dotted = _dotted(func) or ""
    head = dotted.split(".")[0]
    called = dotted if head in local else module.resolve(dotted)
    method = func.attr if isinstance(func, ast.Attribute) else None
    if dotted == "print" and "print" not in local:
        facts.add(Trait.PRINTS, "print")
    if (dotted == "open" and "open" not in local) or method == "open":
        mode = _mode(item, 1 if dotted == "open" else 0)
        trait = Trait.WRITES_FILES if any(flag in mode for flag in "wax+") else Trait.READS_FILES
        facts.add(trait, f"open({mode!r})" if mode else "open")
    if called.startswith(DATABASE_CALLS):
        facts.add(Trait.DATABASE, called)
    elif method in DATABASE_METHODS:
        facts.add(Trait.DATABASE, f".{method}()")
    if called.startswith(NETWORK_MODULES) or called.endswith("urlopen"):
        facts.add(Trait.NETWORK, called)
    if called.startswith(SUBPROCESS_CALLS):
        facts.add(Trait.SUBPROCESS, called)
    if called.startswith(READ_CALLS) and not called.startswith("pandas.read_sql"):
        facts.add(Trait.READS_FILES, called)
    elif method in READ_METHODS:
        facts.add(Trait.READS_FILES, f".{method}()")
    if called.startswith(WRITE_CALLS) and not called.endswith("dumps"):
        facts.add(Trait.WRITES_FILES, called)
    elif method in WRITE_METHODS:
        facts.add(Trait.WRITES_FILES, f".{method}()")
    if called.startswith(NONDETERMINISTIC_CALLS) and "seed" not in called:
        facts.add(Trait.NONDETERMINISTIC, called)
    if called == "time.sleep":
        facts.add(Trait.SLOW_INIT, "time.sleep")
    if method in MUTATORS and isinstance(func, ast.Attribute):
        target = func.value
        if isinstance(target, ast.Name) and target.id not in local and target.id in module.settings:
            facts.add(Trait.MUTABLE_STATE, target.id)
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        ):
            facts.self_stores.add(target.attr)
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "self"
    ):
        facts.self_calls.add(func.attr)
    if any(
        keyword.arg == "axis"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value == 1
        for keyword in item.keywords
    ) and isinstance(func, ast.Attribute):
        owner = func.value
        while isinstance(owner, ast.Attribute | ast.Call | ast.Subscript):
            owner = owner.func if isinstance(owner, ast.Call) else owner.value
        if isinstance(owner, ast.Name):
            facts.frame_use.add(owner.id)
    if called and head not in local:
        facts.calls.add(called)


def _mode(call: ast.Call, position: int) -> str:
    candidates = [call.args[position]] if len(call.args) > position else []
    candidates += [keyword.value for keyword in call.keywords if keyword.arg == "mode"]
    for candidate in candidates:
        if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
            return candidate.value
    return ""


def _propagate(facts: dict[str, _Facts]) -> None:
    """Give each body the behaviour of the scanned helpers it calls, until nothing changes."""
    changed = True
    while changed:
        changed = False
        for target, own in facts.items():
            owner = target.rpartition(".")[0]
            callees = [facts[name] for name in own.calls if name in facts]
            callees += [
                facts[f"{owner}.{name}"] for name in own.self_calls if f"{owner}.{name}" in facts
            ]
            for callee in callees:
                if callee is own:
                    continue
                for trait, seen in callee.traits.items():
                    if trait in BEHAVIOUR and not seen <= own.traits[trait]:
                        own.traits[trait] |= seen
                        changed = True
                if not callee.self_stores <= own.self_stores:
                    own.self_stores |= callee.self_stores
                    changed = True


# Results


def _function(
    module: _Module,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    facts: dict[str, _Facts],
    data_types: set[str],
) -> Found:
    own = facts[f"{module.name}.{node.name}"]
    params, traits = _signature(module, node, own, data_types, skip_first=False)
    for trait, seen in own.traits.items():
        traits[trait] = traits.get(trait, set()) | seen
    return _found(module, node, None, params, traits)


def _methods(
    module: _Module, node: ast.ClassDef, facts: dict[str, _Facts], data_types: set[str]
) -> list[Found]:
    init = next(
        (
            item
            for item in node.body
            if isinstance(item, ast.FunctionDef) and item.name == "__init__"
        ),
        None,
    )
    class_traits: dict[Trait, set[str]] = defaultdict(set)
    init_params: tuple[Param, ...] = ()
    if init is not None:
        own = facts[f"{module.name}.{node.name}.__init__"]
        init_params, _ = _signature(module, init, own, data_types, skip_first=True)
        state = sorted(own.self_stores)
        if state:
            class_traits[Trait.STATEFUL_CLASS].add(node.name)
        slow = set(own.traits.get(Trait.SLOW_INIT, set()))
        for trait in (Trait.DATABASE, Trait.READS_FILES, Trait.NETWORK):
            slow |= {
                f"{trait.value.replace('_', ' ')} {seen}" for seen in own.traits.get(trait, ())
            }
        slow |= {
            f"{name}()"
            for name in [*own.self_calls, *(call.rsplit(".", 1)[-1] for call in own.calls)]
            if SLOW_CALL.search(name)
        }
        if slow:
            class_traits[Trait.SLOW_INIT] |= slow
        caches = {name for name in state if re.search("cache|memo", name, re.IGNORECASE)}
        if caches:
            class_traits[Trait.CACHE] |= {f"self.{name}" for name in caches}
    found: list[Found] = []
    for item in node.body:
        if not isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if item.name.startswith("_") or _decorated(
            module, item, ("property", "abc.abstractmethod")
        ):
            continue
        own = facts[f"{module.name}.{node.name}.{item.name}"]
        static = _decorated(module, item, ("staticmethod",))
        params, traits = _signature(module, item, own, data_types, skip_first=not static)
        for trait, seen in own.traits.items():
            if trait is not Trait.SLOW_INIT:
                traits[trait] = traits.get(trait, set()) | seen
        for trait, seen in class_traits.items():
            traits[trait] = traits.get(trait, set()) | seen
        cached = {name for name in own.self_stores if re.search("cache|memo", name, re.IGNORECASE)}
        if cached:
            traits[Trait.CACHE] = traits.get(Trait.CACHE, set()) | {f"self.{n}" for n in cached}
        changed = own.self_stores - cached
        if changed and init is not None:
            traits[Trait.MUTABLE_STATE] = traits.get(Trait.MUTABLE_STATE, set()) | {
                f"self.{name}" for name in changed
            }
        found.append(_found(module, item, node.name, params, traits, init_params))
    return found


def _decorated(
    module: _Module, node: ast.FunctionDef | ast.AsyncFunctionDef, names: tuple[str, ...]
) -> bool:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if module.resolve(_dotted(target) or "") in names:
            return True
    return False


def _found(
    module: _Module,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    owner: str | None,
    params: tuple[Param, ...],
    traits: dict[Trait, set[str]],
    init_params: tuple[Param, ...] = (),
) -> Found:
    doc = ast.get_docstring(node)
    if not doc:
        traits[Trait.UNDOCUMENTED] = set()
    returns = ast.unparse(node.returns) if node.returns else None
    returns_value = _returns_value(node)
    if returns in ("None", None) and not returns_value:
        traits[Trait.RETURNS_NOTHING] = set()
    if returns is None and returns_value:
        traits.setdefault(Trait.UNTYPED, set()).add("return")
    imports: dict[str, str] = {}
    hints = [param.annotation for param in (*params, *init_params) if param.annotation]
    for hint in [*hints, *([returns] if returns else [])]:
        imports.update(_hint_imports(module, hint))
    return Found(
        module=module.name,
        name=node.name,
        owner=owner,
        params=params,
        returns=returns,
        doc=doc,
        traits={trait: tuple(sorted(seen)) for trait, seen in sorted(traits.items())},
        returns_guess=_guess_return(node) if returns is None else None,
        init_params=init_params,
        imports=imports,
    )


def _signature(
    module: _Module,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    facts: _Facts,
    data_types: set[str],
    *,
    skip_first: bool,
) -> tuple[tuple[Param, ...], dict[Trait, set[str]]]:
    arguments = node.args
    traits: dict[Trait, set[str]] = defaultdict(set)
    positional = [*arguments.posonlyargs, *arguments.args]
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(arguments.defaults))
    defaults += list(arguments.defaults)
    entries: list[tuple[ast.arg, ast.expr | None, ParamKind]] = [
        (arg, default, "positional_only" if index < len(arguments.posonlyargs) else "positional")
        for index, (arg, default) in enumerate(zip(positional, defaults, strict=True))
    ]
    if skip_first and entries:
        entries = entries[1:]
    if arguments.vararg:
        entries.append((arguments.vararg, None, "var_positional"))
    entries += [
        (arg, default, "keyword")
        for arg, default in zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True)
    ]
    if arguments.kwarg:
        entries.append((arguments.kwarg, None, "var_keyword"))
    params: list[Param] = []
    for arg, default, kind in entries:
        annotation = ast.unparse(arg.annotation) if arg.annotation else None
        if kind in ("var_positional", "var_keyword"):
            traits[Trait.VAR_ARGS].add(("*" if kind == "var_positional" else "**") + arg.arg)
        elif kind == "positional_only":
            traits[Trait.POSITIONAL_ONLY].add(arg.arg)
        if annotation is None and kind not in ("var_positional", "var_keyword"):
            traits[Trait.UNTYPED].add(arg.arg)
        library = _library(module, arg.annotation) if arg.annotation else None
        used = facts.attributes.get(arg.arg, set())
        if library is None and annotation is None and used & PANDAS_ATTRIBUTES:
            library = "pandas"
        if library == "pandas":
            traits[Trait.PANDAS].add(arg.arg)
        elif library == "numpy":
            traits[Trait.NUMPY].add(arg.arg)
        if arg.annotation is not None and kind not in ("var_positional", "var_keyword"):
            unsupported = _unsupported(module, arg.annotation, data_types)
            if unsupported:
                traits[Trait.UNSUPPORTED_TYPE].add(f"{arg.arg}: {', '.join(unsupported)}")
        data = _data_kind(arg.arg, annotation, library, used, arg.arg in facts.frame_use)
        params.append(
            Param(
                name=arg.arg,
                annotation=annotation,
                default=ast.unparse(default) if default is not None else None,
                kind=kind,
                data=data if kind not in ("var_positional", "var_keyword") else None,
                library=library,
            )
        )
    if node.returns is not None:
        unsupported = _unsupported(module, node.returns, data_types)
        if unsupported:
            traits[Trait.UNSUPPORTED_TYPE].add(f"return: {', '.join(unsupported)}")
    return tuple(params), traits


def _data_kind(
    name: str, annotation: str | None, library: str | None, used: set[str], table: bool
) -> DataKind | None:
    """Whether a parameter looks like data a client should pass by reference, and its shape."""
    plain = annotation is not None and library is None
    if plain and not re.match(r"^(list|Sequence|tuple)\[float", annotation or ""):
        return None  # a typed scalar or a non-numeric container: a value, not data
    if annotation and re.search(r"DataFrame|^dict\[str, *list\[float", annotation):
        return "frame"
    if annotation and re.search(
        r"Series|ndarray|NDArray|^(list|Sequence|tuple)\[float", annotation
    ):
        return "series"
    looks = library is not None or bool(used & PANDAS_ATTRIBUTES) or bool(DATA_NAMES.search(name))
    if not looks:
        return None
    if table or used & FRAME_ATTRIBUTES or FRAME_NAMES.search(name):
        return "frame"
    return "series"


def _library(module: _Module, hint: ast.expr) -> Literal["pandas", "numpy"] | None:
    for name in _hint_names(hint):
        resolved = module.resolve(name)
        if resolved.startswith("pandas"):
            return "pandas"
        if resolved.startswith("numpy"):
            return "numpy"
    return None


def _unsupported(module: _Module, hint: ast.expr, data_types: set[str]) -> list[str]:
    unsupported = []
    for name in _hint_names(hint):
        resolved = module.resolve(name)
        if resolved in SUPPORTED or resolved in data_types:
            continue
        if resolved.startswith(("typing.", "collections.abc.")) and resolved.rsplit(".", 1)[-1] in {
            "Any",
            "Optional",
            "Union",
            "Literal",
            "Annotated",
            "Sequence",
            "Mapping",
            "List",
            "Dict",
            "Tuple",
        }:
            continue
        unsupported.append(name)
    return unsupported


def _hint_names(hint: ast.expr) -> list[str]:
    """The dotted names a hint uses, leaving out the values inside ``Literal[...]``."""
    if isinstance(hint, ast.Constant):
        if isinstance(hint.value, str):  # a quoted hint
            try:
                return _hint_names(ast.parse(hint.value, mode="eval").body)
            except SyntaxError:
                return [hint.value]
        return ["None"] if hint.value is None else []
    if isinstance(hint, ast.Name | ast.Attribute):
        return [_dotted(hint) or ast.unparse(hint)]
    if isinstance(hint, ast.Subscript):
        outer = _hint_names(hint.value)
        if outer and outer[0].rsplit(".", 1)[-1] == "Literal":
            return outer
        return outer + _hint_names(hint.slice)
    if isinstance(hint, ast.Tuple | ast.List):
        return [name for item in hint.elts for name in _hint_names(item)]
    if isinstance(hint, ast.BinOp):
        return _hint_names(hint.left) + _hint_names(hint.right)
    return [ast.unparse(hint)]


def _hint_imports(module: _Module, hint: str) -> dict[str, str]:
    """The import line for each name a hint needs, so a draft can reuse the hint as written."""
    try:
        tree = ast.parse(hint, mode="eval").body
    except SyntaxError:
        return {}
    needed: dict[str, str] = {}
    for name in _hint_names(tree):
        head = name.split(".")[0]
        if head in module.import_lines:
            needed[head] = module.import_lines[head]
        elif head in module.classes:
            needed[head] = f"from {module.name} import {head}"
    return needed


def _returns_value(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        (
            isinstance(item, ast.Return)
            and item.value is not None
            and not (isinstance(item.value, ast.Constant) and item.value.value is None)
        )
        or isinstance(item, ast.Yield | ast.YieldFrom)
        for item in _own_nodes(node)
    )


def _own_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """The nodes of a function's body, without nested functions or classes."""
    stack = list(getattr(node, "body", []))
    while stack:
        item = stack.pop()
        yield item
        for child in ast.iter_child_nodes(item):
            if not isinstance(
                child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda
            ):
                stack.append(child)


def _guess_return(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """A hint for an untyped function, when every return statement says the same plain type."""
    kinds = set()
    for item in _own_nodes(node):
        if not isinstance(item, ast.Return) or item.value is None:
            continue
        value = item.value
        if isinstance(value, ast.Constant) and isinstance(value.value, bool):
            kinds.add("bool")
        elif isinstance(value, ast.Constant) and isinstance(value.value, int | float):
            kinds.add("float")
        elif isinstance(value, ast.JoinedStr) or (
            isinstance(value, ast.Constant) and isinstance(value.value, str)
        ):
            kinds.add("str")
        elif isinstance(value, ast.Compare | ast.BoolOp) or (
            isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.Not)
        ):
            kinds.add("bool")
        elif isinstance(value, ast.Dict):
            kinds.add("dict[str, Any]")
        elif isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            kinds.add(
                {
                    "float": "float",
                    "round": "float",
                    "int": "int",
                    "len": "int",
                    "str": "str",
                    "bool": "bool",
                }.get(value.func.id, "?")
            )
        else:
            kinds.add("?")
    return kinds.pop() if len(kinds) == 1 and "?" not in kinds else None


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _dotted(node.value)
        return f"{inner}.{node.attr}" if inner else None
    return None


def with_data(found: Found, data: dict[str, DataKind]) -> Found:
    """``found`` with its parameters' data kinds set as the developer decided."""
    return replace(
        found,
        params=tuple(replace(param, data=data.get(param.name)) for param in found.params),
    )
