"""Register a firm's own functions as tools: an existing function plus a few lines (decision 0024).

    risk = FirmApp("risk", instructions="Use desk VaR for any VaR question.")
    risk.expose(historical_var, returns=Series, convention="Historical, nearest rank")

`FirmApp.expose` reads the function's type hints for the input schema, and its docstring for the
description and parameter descriptions. A parameter marked `Series`, `Frame`, or `Prices` takes
a dataset reference; the dataset is read through the store the app is served with, so the access
guard applies, and the function gets plain values, a NumPy array, or a pandas object, whichever
its hint asks for. Results gain the data's fingerprints, the code's version, the stated
convention, and ``computed`` evidence. Registration stays explicit, in the firm's own server
file (decision 0020): nothing here discovers or loads code.

NumPy and pandas are never imported unless a function's hints name them.
"""

from __future__ import annotations

import dataclasses
import importlib.metadata
import inspect
import math
import re
import types
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Union, get_args, get_origin, get_type_hints

from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic.fields import FieldInfo

from quant_agent_os.data.datasets import Dataset, DatasetStore
from quant_agent_os.evidence import Evidence, SourceRef
from quant_agent_os.tools.contracts import ToolContractError, ToolSpec
from quant_agent_os.tools.permissions import Permission
from quant_agent_os.tools.registry import DuplicateToolError, ToolRegistry

BY_REFERENCE = "Pass data as dataset references from list_datasets, not as values."
PROVENANCE = frozenset({"data_versions", "code_version", "convention", "warnings", "evidence"})
MERGED = frozenset({"data_versions", "warnings", "evidence"})  # a function may return its own


@dataclass(frozen=True)
class DataMarker:
    """Marks a parameter that takes a dataset reference instead of values."""

    kind: Literal["series", "frame"]
    description: str


Series = DataMarker("series", "One column of a dataset, by reference.")
Frame = DataMarker("frame", "Columns of one dataset, by reference; every column by default.")
Prices = DataMarker("frame", "Asset prices, one column per asset, by reference.")


class SeriesRef(BaseModel):
    """One column of a dataset."""

    model_config = ConfigDict(extra="forbid")

    dataset: str = Field(min_length=1, description="A dataset id from list_datasets")
    column: str = Field(min_length=1, description="The column in that dataset")


class FrameRef(BaseModel):
    """Columns of one dataset."""

    model_config = ConfigDict(extra="forbid")

    dataset: str = Field(min_length=1, description="A dataset id from list_datasets")
    columns: list[str] | None = Field(
        default=None, min_length=1, description="Columns to use; default: every column"
    )


class FrameValue(BaseModel):
    """A table returned by a tool: an index and named columns."""

    index: list[str] | None = None
    columns: dict[str, list[Any]]


@dataclass(frozen=True)
class _Input:
    name: str
    marker: DataMarker | None
    target: Any  # the function's own hint, without Annotated or None
    optional: bool


@dataclass(frozen=True)
class _Exposed:
    function: Callable[..., Any]
    name: str
    description: str
    version: str
    inputs: tuple[_Input, ...]
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    flatten: bool  # the result's fields go at the top of the output, beside the provenance
    convention: str | None
    code_version: str | None
    permissions: frozenset[Permission]
    timeout_seconds: float
    max_output_bytes: int
    deterministic: bool
    idempotent: bool


class FirmApp:
    """A named group of a firm's tools, with instructions for clients.

    Expose functions with `expose`, add ready-made tools (such as a generated application's
    ``agent-tools``) with `include`, and serve the app with ``quant_platform.serve_apps``. With
    ``prefix``, every exposed tool's name starts with the app's name, which avoids clashes
    between apps.
    """

    def __init__(self, name: str, *, instructions: str | None = None, prefix: bool = False) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", name):
            raise ToolContractError(
                f"App name '{name}' must be snake_case, start with a letter, and be at most "
                "32 characters."
            )
        self.name = name
        self.instructions = instructions
        self.prefix = prefix
        self._exposed: list[_Exposed] = []
        self._included: list[ToolSpec[Any, Any]] = []

    @property
    def names(self) -> list[str]:
        """The names of the app's tools, in the order they were added."""
        return [entry.name for entry in self._exposed] + [spec.name for spec in self._included]

    def expose(
        self,
        function: Callable[..., Any],
        /,
        *,
        name: str | None = None,
        description: str | None = None,
        convention: str | None = None,
        version: str = "1.0.0",
        permissions: Iterable[Permission] = (),
        timeout_seconds: float = 10.0,
        max_output_bytes: int = 100_000,
        deterministic: bool = True,
        idempotent: bool = True,
        **data: DataMarker,
    ) -> Callable[..., Any]:
        """Register ``function`` as a tool and return it unchanged.

        Mark parameters that take a dataset reference by name, as in ``returns=Series``, or
        in the function's own hint, as in ``Annotated[pd.Series, Series]``. ``convention``
        says how the figure is defined, and is added to the description and every result.
        Declare every permission the function needs beyond reading data: the platform cannot
        detect a function that writes files or calls out. Raises `ToolContractError` naming
        what to change when the function cannot be exposed as it is.
        """
        tool_name = f"{self.name}_{name or function.__name__}" if self.prefix else name
        tool_name = tool_name or function.__name__
        if tool_name in self.names:
            raise DuplicateToolError(f"App '{self.name}' already has a tool named '{tool_name}'.")
        doc = _Docstring.parse(inspect.getdoc(function) or "")
        inputs = _inputs(function, tool_name, data)
        input_model = _input_model(tool_name, function, inputs, doc)
        output_model, flatten = _output_model(tool_name, function)
        summary = description or doc.summary
        if not summary:
            raise ToolContractError(
                f"Tool '{tool_name}' needs a description: give the function a docstring, or "
                "pass description=."
            )
        text = summary.rstrip(".") + "."
        if convention:
            text += f" Convention: {convention.rstrip('.')}."
        if any(entry.marker for entry in inputs):
            text += f" {BY_REFERENCE}"
            permissions = {*permissions, Permission.READ_LOCAL_DATA}
        entry = _Exposed(
            function=function,
            name=tool_name,
            description=text,
            version=version,
            inputs=inputs,
            input_model=input_model,
            output_model=output_model,
            flatten=flatten,
            convention=convention,
            code_version=_code_version(function),
            permissions=frozenset(permissions),
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            deterministic=deterministic,
            idempotent=idempotent,
        )
        _build(entry, None)  # checks the name, version, and limits now, not when served
        self._exposed.append(entry)
        return function

    def include(self, tools: Iterable[ToolSpec[Any, Any]] | ToolRegistry) -> None:
        """Add ready-made tools as they are, such as a generated application's registry."""
        specs = tools.specs() if isinstance(tools, ToolRegistry) else list(tools)
        for spec in specs:
            if spec.name in self.names:
                raise DuplicateToolError(
                    f"App '{self.name}' already has a tool named '{spec.name}'."
                )
            self._included.append(spec)

    def tools(self, datasets: DatasetStore | None = None) -> list[ToolSpec[Any, Any]]:
        """Return the app's tools, reading data from ``datasets``.

        Pass the store the server serves, wrapped in its access guard when there is one, so a
        caller reaches only the datasets it may. Raises `ToolContractError` when a tool reads
        data and no store is given.
        """
        readers = [entry.name for entry in self._exposed if any(i.marker for i in entry.inputs)]
        if readers and datasets is None:
            raise ToolContractError(
                f"{', '.join(readers)} read datasets; serve app '{self.name}' with datasets=."
            )
        return [_build(entry, datasets) for entry in self._exposed] + list(self._included)


def _build(entry: _Exposed, store: DatasetStore | None) -> ToolSpec[Any, Any]:
    def run(params: BaseModel) -> BaseModel:
        return _call(entry, store, params)

    return ToolSpec(
        name=entry.name,
        description=entry.description,
        version=entry.version,
        input_model=entry.input_model,
        output_model=entry.output_model,
        function=run,
        permissions=entry.permissions,
        timeout_seconds=entry.timeout_seconds,
        max_output_bytes=entry.max_output_bytes,
        deterministic=entry.deterministic,
        idempotent=entry.idempotent,
    )


def _call(entry: _Exposed, store: DatasetStore | None, params: BaseModel) -> BaseModel:
    arguments: dict[str, Any] = {}
    read: dict[str, Dataset] = {}
    sources: list[SourceRef] = []
    warnings: list[str] = []
    for item in entry.inputs:
        value = getattr(params, item.name)
        if item.marker is None or value is None:
            arguments[item.name] = value
            continue
        assert store is not None  # FirmApp.tools refuses a reader without a store
        dataset = read.get(value.dataset) or store.get(value.dataset)
        read[dataset.id] = dataset
        if isinstance(value, SeriesRef):
            columns = {value.column: dataset.column(value.column)}
        else:
            columns = {name: dataset.column(name) for name in value.columns or dataset.columns}
        for column, values in columns.items():
            missing = sum(math.isnan(number) for number in values)
            if missing:
                warnings.append(
                    f"{dataset.id}.{column} has {missing} missing values, passed as NaN."
                )
            sources.append(
                SourceRef(
                    source_id=f"{dataset.id}.{column}",
                    location=f"{len(values)} rows, sha256 {dataset.fingerprint[:12]}",
                )
            )
        arguments[item.name] = _as_target(item, dataset, columns)
    result = _plain(entry.function(**arguments))
    fields: dict[str, Any] = dict(result) if entry.flatten else {"result": result}
    versions = {f"dataset:{d.id}": d.fingerprint for d in read.values()}
    versions.update(fields.pop("data_versions", None) or {})
    statement = f"{entry.name} computed {_summary(result)}"
    if sources:
        statement += f" from {', '.join(source.source_id for source in sources)}"
    if entry.convention:
        statement += f", by the convention: {entry.convention.rstrip('.')}"
    own_warnings = fields.pop("warnings", None) or []
    own_evidence = fields.pop("evidence", None) or []
    return entry.output_model.model_validate(
        {
            **fields,
            "data_versions": versions,
            "code_version": entry.code_version,
            "convention": entry.convention,
            "warnings": [*own_warnings, *warnings],
            "evidence": [
                *own_evidence,
                Evidence(kind="computed", statement=statement + ".", sources=sources),
            ],
        }
    )


def _summary(result: Any) -> str:
    if isinstance(result, bool) or not isinstance(result, int | float):
        return "its result"
    return f"{result:.6g}"


# Inputs


def _inputs(
    function: Callable[..., Any], tool_name: str, marked: Mapping[str, DataMarker]
) -> tuple[_Input, ...]:
    hints = _hints(function, tool_name)
    parameters = inspect.signature(function).parameters
    unknown = set(marked) - set(parameters)
    if unknown:
        raise ToolContractError(
            f"Tool '{tool_name}': {', '.join(sorted(unknown))} marked as data, but "
            f"{function.__name__} has no such parameter."
        )
    if not all(isinstance(given, DataMarker) for given in marked.values()):
        raise ToolContractError(
            f"Tool '{tool_name}': mark data parameters with Series, Frame, or Prices."
        )
    inputs: list[_Input] = []
    for name, parameter in parameters.items():
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            raise ToolContractError(
                f"Tool '{tool_name}': *{name} cannot be exposed; name each parameter."
            )
        if parameter.kind is parameter.POSITIONAL_ONLY:
            raise ToolContractError(
                f"Tool '{tool_name}': {name} is positional-only; tools are called by name."
            )
        if name not in hints:
            raise ToolContractError(
                f"Tool '{tool_name}': parameter '{name}' needs a type hint, such as float or "
                "list[float]."
            )
        hint, annotated = _unwrap(hints[name])
        marker: DataMarker | None = marked.get(name) or next(
            (meta for meta in annotated if isinstance(meta, DataMarker)), None
        )
        target, optional = _without_none(hint)
        if marker is not None:
            _check_data_target(tool_name, name, marker, target)
        elif _library(target):
            raise ToolContractError(
                f"Tool '{tool_name}': parameter '{name}' is a {_library(target)} value; mark it "
                f"as data ({name}=Series or {name}=Frame), so a client passes a dataset reference."
            )
        inputs.append(_Input(name, marker, target, optional))
    return tuple(inputs)


def _input_model(
    tool_name: str, function: Callable[..., Any], inputs: Sequence[_Input], doc: _Docstring
) -> type[BaseModel]:
    hints = _hints(function, tool_name)
    parameters = inspect.signature(function).parameters
    fields: dict[str, Any] = {}
    for item in inputs:
        default = parameters[item.name].default
        required = default is inspect.Parameter.empty
        info: dict[str, Any] = {"description": doc.parameters.get(item.name)}
        if item.marker is not None:
            reference: Any = SeriesRef if item.marker.kind == "series" else FrameRef
            annotation = reference | None if item.optional else reference
            info["description"] = info["description"] or item.marker.description
            if not required and default is not None:
                raise ToolContractError(
                    f"Tool '{tool_name}': data parameter '{item.name}' can default only to None."
                )
        else:
            annotation = hints[item.name]
        field = Field(**info) if required else Field(default, **info)
        fields[item.name] = (annotation, field)
    model = create_model(
        _model_name(tool_name, "Input"), __config__=ConfigDict(extra="forbid"), **fields
    )
    _check_schema(tool_name, model, "input")
    return model


def _check_data_target(tool_name: str, name: str, marker: DataMarker, target: Any) -> None:
    origin = get_origin(target) or target
    if marker.kind == "series":
        ok = (
            target is Any
            or origin in (list, tuple, Sequence)
            or _library(target)
            in (
                "numpy",
                "pandas.Series",
            )
        )
        expected = "list[float], tuple[float, ...], a NumPy array, or a pandas Series"
    else:
        ok = (
            target is Any
            or origin in (dict, Mapping)
            or _library(target)
            in (
                "numpy",
                "pandas.DataFrame",
            )
        )
        expected = "dict[str, list[float]], a NumPy array, or a pandas DataFrame"
    if not ok:
        raise ToolContractError(
            f"Tool '{tool_name}': data parameter '{name}' must be hinted as {expected}."
        )


def _as_target(item: _Input, dataset: Dataset, columns: dict[str, list[float]]) -> Any:
    library = _library(item.target)
    if item.marker is not None and item.marker.kind == "series":
        ((column, values),) = columns.items()
        if library == "numpy":
            import numpy as np

            return np.asarray(values, dtype=float)
        if library == "pandas.Series":
            import pandas as pd

            return pd.Series(values, index=_pandas_index(dataset), name=column, dtype=float)
        origin = get_origin(item.target) or item.target
        return tuple(values) if origin is tuple else list(values)
    if library == "numpy":
        import numpy as np

        return np.column_stack([np.asarray(values, dtype=float) for values in columns.values()])
    if library == "pandas.DataFrame":
        import pandas as pd

        return pd.DataFrame(columns, index=_pandas_index(dataset), dtype=float)
    return {name: list(values) for name, values in columns.items()}


def _pandas_index(dataset: Dataset) -> Any:
    import pandas as pd

    if dataset.dates is not None:
        return pd.DatetimeIndex(dataset.dates, name=dataset.index_name)
    if dataset.index is not None:
        return pd.Index(dataset.index, name=dataset.index_name)
    return None


# Outputs


def _output_model(tool_name: str, function: Callable[..., Any]) -> tuple[type[BaseModel], bool]:
    hints = _hints(function, tool_name)
    if "return" not in hints:
        raise ToolContractError(
            f"Tool '{tool_name}': {function.__name__} needs a return type hint, such as float, "
            "a dataclass, or a Pydantic model."
        )
    returned, _ = _unwrap(hints["return"])
    fields: dict[str, Any]
    if isinstance(returned, type) and issubclass(returned, BaseModel):
        fields = {
            name: (_output_type(info.annotation), _copy_field(info))
            for name, info in returned.model_fields.items()
        }
        flatten = True
    elif dataclasses.is_dataclass(returned) and isinstance(returned, type):
        types_of = get_type_hints(returned)
        fields = {
            field.name: (_output_type(types_of[field.name]), _dataclass_field(field))
            for field in dataclasses.fields(returned)
        }
        flatten = True
    elif _is_typeddict(returned):
        fields = {
            name: (_output_type(hint), ...) for name, hint in get_type_hints(returned).items()
        }
        flatten = True
    elif returned is None or returned is type(None):
        raise ToolContractError(
            f"Tool '{tool_name}': {function.__name__} returns nothing; a tool must return a result."
        )
    else:
        fields = {"result": (_output_type(returned), ...)}
        flatten = False
    clash = (set(fields) & PROVENANCE) - MERGED
    if clash:
        raise ToolContractError(
            f"Tool '{tool_name}': the result's field {', '.join(sorted(clash))} is added by the "
            "platform; rename it in the result."
        )
    fields.update(
        data_versions=(dict[str, str], Field(default_factory=dict)),
        code_version=(str | None, Field(default=None, description="Version of the firm's code")),
        convention=(str | None, None),
        warnings=(list[str], Field(default_factory=list)),
        evidence=(list[Evidence], Field(default_factory=list)),
    )
    model = create_model(_model_name(tool_name, "Output"), **fields)
    _check_schema(tool_name, model, "output")
    return model, flatten


def _output_type(hint: Any) -> Any:
    """The schema type of a returned value after `_plain` converts it."""
    hint, _ = _unwrap(hint)
    library = _library(hint)
    if library == "numpy":
        name = getattr(hint, "__name__", "")
        if "int" in name:
            return int
        if "float" in name:
            return float
        if "bool" in name:
            return bool
        return list[Any]
    if library == "pandas.Series":
        return dict[str, Any]
    if library == "pandas.DataFrame":
        return FrameValue
    if dataclasses.is_dataclass(hint) and isinstance(hint, type):
        types_of = get_type_hints(hint)
        return create_model(  # type: ignore[call-overload]
            hint.__name__,
            **{
                f.name: (_output_type(types_of[f.name]), _dataclass_field(f))
                for f in dataclasses.fields(hint)
            },
        )
    origin, args = get_origin(hint), get_args(hint)
    if origin in (list, Sequence) and args:
        return list[_output_type(args[0])]  # type: ignore[misc]
    if origin is tuple and args:
        return list[Any]
    if origin in (dict, Mapping) and len(args) == 2:
        return dict[str, _output_type(args[1])]  # type: ignore[misc]
    if origin in (Union, types.UnionType):
        return Union[tuple(_output_type(arg) for arg in args)]  # noqa: UP007
    return hint


def _plain(value: Any) -> Any:
    """``value`` as JSON-ready Python: models, dataclasses, NumPy, and pandas values converted."""
    if isinstance(value, BaseModel):
        return {name: _plain(getattr(value, name)) for name in type(value).model_fields}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _plain(getattr(value, field.name)) for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {_key(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    library = _library(type(value))
    if library == "numpy":
        return _plain(value.tolist())  # an array becomes lists, a scalar a Python number
    if library == "pandas.Series":
        return {_key(key): _plain(item) for key, item in value.items()}
    if library == "pandas.DataFrame":
        return {
            "index": [_key(key) for key in value.index],
            "columns": {_key(name): _plain(value[name].tolist()) for name in value.columns},
        }
    return value


def _key(key: Any) -> str:
    if hasattr(key, "isoformat"):
        return str(key.isoformat()).removesuffix("T00:00:00")
    return str(key)


# Shared helpers


def _hints(function: Callable[..., Any], tool_name: str) -> dict[str, Any]:
    try:
        return get_type_hints(function, include_extras=True)
    except (NameError, TypeError) as error:
        raise ToolContractError(
            f"Tool '{tool_name}': cannot read the type hints of {function.__name__}: {error}"
        ) from error


def _unwrap(hint: Any) -> tuple[Any, tuple[Any, ...]]:
    if get_origin(hint) is Annotated:
        base, *metadata = get_args(hint)
        return base, tuple(metadata)
    return hint, ()


def _without_none(hint: Any) -> tuple[Any, bool]:
    if get_origin(hint) in (Union, types.UnionType):
        args = [arg for arg in get_args(hint) if arg is not type(None)]
        if len(args) == 1 and len(args) < len(get_args(hint)):
            return args[0], True
    return hint, False


def _library(hint: Any) -> str | None:
    """'numpy', 'pandas.Series', or 'pandas.DataFrame' for those libraries' types, else None."""
    hint = get_origin(hint) or hint
    module = getattr(hint, "__module__", "") or ""
    if module == "numpy" or module.startswith("numpy."):
        return "numpy"
    if module == "pandas" or module.startswith("pandas."):
        name = getattr(hint, "__name__", "")
        return f"pandas.{name}" if name in ("Series", "DataFrame") else "pandas"
    return None


def _is_typeddict(hint: Any) -> bool:
    return isinstance(hint, type) and issubclass(hint, dict) and hasattr(hint, "__total__")


def _copy_field(info: FieldInfo) -> Any:
    if info.is_required():
        return Field(description=info.description)
    if info.default_factory is not None:
        return Field(default_factory=info.default_factory, description=info.description)
    return Field(info.default, description=info.description)


def _dataclass_field(field: dataclasses.Field[Any]) -> Any:
    if field.default is not dataclasses.MISSING:
        return Field(field.default)
    if field.default_factory is not dataclasses.MISSING:
        return Field(default_factory=field.default_factory)
    return ...


def _model_name(tool_name: str, suffix: str) -> str:
    return "".join(part.capitalize() for part in tool_name.split("_")) + suffix


def _check_schema(tool_name: str, model: type[BaseModel], direction: str) -> None:
    try:
        model.model_json_schema(mode="serialization" if direction == "output" else "validation")
    except Exception as error:  # pydantic raises several types for a type it cannot describe
        raise ToolContractError(
            f"Tool '{tool_name}': its {direction} has a type with no JSON schema ({error}); use "
            "plain types, dataclasses, or Pydantic models."
        ) from error


def _code_version(function: Callable[..., Any]) -> str | None:
    """'<distribution> <version>' for the installed package that holds ``function``, or None."""
    package = (getattr(function, "__module__", "") or "").split(".")[0]
    for distribution in importlib.metadata.packages_distributions().get(package, []):
        try:
            return f"{distribution} {importlib.metadata.version(distribution)}"
        except importlib.metadata.PackageNotFoundError:
            continue
    return None


@dataclass(frozen=True)
class _Docstring:
    """A docstring's first paragraph and its parameter descriptions (Google, NumPy, or Sphinx)."""

    summary: str
    parameters: dict[str, str]

    @classmethod
    def parse(cls, text: str) -> _Docstring:
        paragraphs = text.strip().split("\n\n")
        summary = " ".join(paragraphs[0].split()) if paragraphs and paragraphs[0] else ""
        if re.match(r"(Args|Arguments|Parameters|Returns|Raises):?\s*$", paragraphs[0].strip()):
            summary = ""
        found: dict[str, str] = {}
        for match in re.finditer(r"^\s*:param\s+(?:\S+\s+)?(\w+):\s*(.+)$", text, re.MULTILINE):
            found[match.group(1)] = match.group(2).strip()
        section = re.search(
            r"^(?:Args|Arguments|Parameters):?\s*\n(?:-+\s*\n)?(.*?)(?:\n\s*\n(?=\S)|\Z)",
            text,
            re.MULTILINE | re.DOTALL,
        )
        if section:
            current: str | None = None
            for line in section.group(1).splitlines():
                google = re.match(r"^\s{0,8}(\w+)\s*(?:\([^)]*\))?:\s*(.*)$", line)
                numpy = re.match(r"^(\w+)\s*:\s*\S.*$", line)
                if numpy and not line.startswith(" "):
                    current = numpy.group(1)
                    found.setdefault(current, "")
                elif google and not line.startswith("        "):
                    current = google.group(1)
                    found[current] = google.group(2).strip()
                elif current and line.strip():
                    found[current] = f"{found[current]} {line.strip()}".strip()
        return cls(summary, {name: text for name, text in found.items() if text})
