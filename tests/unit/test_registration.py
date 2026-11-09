"""Registering a firm's functions (decision 0024): schemas, data by reference, and provenance."""

import math
import re
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

import pytest
from pydantic import BaseModel

from quant_agent_os.data import Dataset, UnknownDatasetError, parse_dataset
from quant_agent_os.registration import FirmApp, Frame, Prices, Series, _code_version, _Docstring
from quant_agent_os.runtime.call import ToolCall
from quant_agent_os.runtime.executor import ToolCallRecord, ToolExecutor
from quant_agent_os.testing import check_tool
from quant_agent_os.tools import (
    DuplicateToolError,
    Permission,
    PermissionPolicy,
    ToolContractError,
    ToolRegistry,
    ToolSpec,
    tool,
)

GROWTH = "growth"
DESKS = (
    "date,growth,value\n"
    "2024-01-31,0.02,0.01\n"
    "2024-02-29,-0.03,0.00\n"
    "2024-03-31,0.01,nan\n"
    "2024-04-30,-0.05,0.02\n"
)


class Store:
    """One dataset, as a firm's own store might serve it."""

    def __init__(self) -> None:
        self.desks = parse_dataset("desks", DESKS.encode())

    def get(self, dataset_id: str) -> Dataset:
        if dataset_id != "desks":
            raise UnknownDatasetError(f"No dataset '{dataset_id}'.")
        return self.desks

    def ids(self) -> list[str]:
        return ["desks"]


@pytest.fixture
def store() -> Store:
    return Store()


def _tools(app: FirmApp, store: Store) -> dict[str, ToolSpec[Any, Any]]:
    return {spec.name: spec for spec in app.tools(store)}


def _run(spec: ToolSpec[Any, Any], arguments: dict[str, Any]) -> ToolCallRecord:
    executor = ToolExecutor(ToolRegistry([spec]), PermissionPolicy())
    return executor.execute(ToolCall(tool=spec.name, arguments=arguments))


def historical_var(returns: list[float], confidence: float = 0.75) -> float:
    """One-period historical VaR: the nearest-rank loss.

    Args:
        returns: Periodic returns, oldest first.
        confidence: For example 0.95 for 95%.
    """
    values = sorted(value for value in returns if not math.isnan(value))
    rank = max(math.ceil(round((1 - confidence) * len(values), 9)), 1)
    return max(-values[rank - 1], 0.0)


SERIES = {"dataset": "desks", "column": GROWTH}


# A function becomes a tool


def test_a_typed_function_becomes_a_tool_with_schemas_from_its_hints_and_docstring(
    store: Store,
) -> None:
    app = FirmApp("risk")
    assert app.expose(historical_var, returns=Series, convention="Nearest rank") is historical_var

    spec = _tools(app, store)["historical_var"]
    schema = spec.descriptor()["input_schema"]

    assert schema["required"] == ["returns"]
    assert schema["properties"]["returns"]["description"] == "Periodic returns, oldest first."
    assert schema["properties"]["confidence"] == {
        "default": 0.75,
        "description": "For example 0.95 for 95%.",
        "title": "Confidence",
        "type": "number",
    }
    assert schema["additionalProperties"] is False
    assert spec.description.startswith("One-period historical VaR: the nearest-rank loss.")
    assert "Convention: Nearest rank." in spec.description
    assert "dataset references" in spec.description
    assert spec.permissions == {Permission.READ_LOCAL_DATA}


def test_the_result_carries_the_data_versions_convention_and_computed_evidence(
    store: Store,
) -> None:
    app = FirmApp("risk")
    app.expose(historical_var, returns=Series, convention="Nearest rank")

    record = check_tool(_tools(app, store)["historical_var"], {"returns": SERIES})

    output = record.output
    assert output["result"] == pytest.approx(0.05)  # rank 1 of 4 at 75%: the 5% loss
    assert output["data_versions"] == {"dataset:desks": store.get("desks").fingerprint}
    assert output["convention"] == "Nearest rank"
    (evidence,) = output["evidence"]
    assert evidence["kind"] == "computed"
    assert evidence["statement"] == (
        "historical_var computed 0.05 from desks.growth, by the convention: Nearest rank."
    )
    assert evidence["sources"][0]["source_id"] == "desks.growth"


def test_missing_values_are_passed_as_nan_and_reported(store: Store) -> None:
    def count(values: list[float]) -> int:
        """Count the values, missing ones included."""
        return len(values)

    app = FirmApp("risk")
    app.expose(count, values=Series)

    record = _run(_tools(app, store)["count"], {"values": {"dataset": "desks", "column": "value"}})

    assert record.output is not None
    assert record.output["result"] == 4
    assert record.output["warnings"] == ["desks.value has 1 missing values, passed as NaN."]


def test_a_frame_reaches_the_function_as_a_dict_of_columns(store: Store) -> None:
    def widths(table: dict[str, list[float]]) -> dict[str, int]:
        """Rows per column."""
        return {name: len(values) for name, values in table.items()}

    app = FirmApp("risk")
    app.expose(widths, table=Frame)
    spec = _tools(app, store)["widths"]

    every = check_tool(spec, {"table": {"dataset": "desks"}})
    chosen = check_tool(spec, {"table": {"dataset": "desks", "columns": ["value"]}})

    assert every.output["result"] == {"growth": 4, "value": 4}
    assert chosen.output["result"] == {"value": 4}


def test_a_marker_can_sit_in_the_functions_own_hint(store: Store) -> None:
    def first(returns: Annotated[list[float], Series], scale: float = 1.0) -> float:
        """The first value, scaled."""
        return returns[0] * scale

    def assets(prices: Annotated[dict[str, list[float]], Prices]) -> list[str]:
        """The assets priced."""
        return list(prices)

    app = FirmApp("risk")
    app.expose(first)
    app.expose(assets)
    tools = _tools(app, store)

    record = check_tool(tools["first"], {"returns": SERIES, "scale": 2.0})

    assert record.output["result"] == pytest.approx(0.04)
    assert check_tool(tools["assets"], {"prices": {"dataset": "desks"}}).output["result"] == [
        "growth",
        "value",
    ]


def test_a_failing_function_is_recorded_on_the_call_not_raised(store: Store) -> None:
    def broken(values: list[float]) -> float:
        """Always fails."""
        raise ValueError("No good.")

    app = FirmApp("risk")
    app.expose(broken, values=Series)
    spec = _tools(app, store)["broken"]

    failed = _run(spec, {"values": SERIES})
    missing = _run(spec, {"values": {"dataset": "nothing", "column": "x"}})

    assert failed.status == "error" and "No good." in (failed.error or "")
    assert missing.status == "error" and "nothing" in (missing.error or "")


# What may come back


@dataclass
class Summary:
    """A dataclass result."""

    worst: float
    periods: int
    notes: list[str] = field(default_factory=list)


class Stats(BaseModel):
    """A model result, with its own warnings."""

    mean: float
    warnings: list[str] = []


class Shape(TypedDict):
    rows: int


def as_dataclass(values: list[float]) -> Summary:
    """Summarize as a dataclass."""
    return Summary(min(values), len(values))


def as_model(values: list[float]) -> Stats:
    """Summarize as a model."""
    return Stats(mean=0.1, warnings=["thin"])


def as_typed_dict(values: list[float]) -> Shape:
    """Summarize as a typed dict."""
    return Shape(rows=len(values))


@pytest.mark.parametrize(
    ("function", "expected"),
    [
        (as_dataclass, {"worst": -0.05, "periods": 4, "notes": []}),
        (as_model, {"mean": 0.1, "warnings": ["thin"]}),
        (as_typed_dict, {"rows": 4}),
    ],
)
def test_structured_results_are_flattened_beside_the_provenance(
    store: Store, function: Any, expected: dict[str, Any]
) -> None:
    app = FirmApp("risk")
    app.expose(function, values=Series)

    record = check_tool(app.tools(store)[0], {"values": SERIES})

    output = record.output
    assert {key: output[key] for key in expected} == expected
    assert output["data_versions"] and output["evidence"]


def test_numpy_and_pandas_values_go_in_and_come_back_as_plain_values(
    store: Store,
) -> None:
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")

    def scaled(values: np.ndarray) -> np.ndarray:
        """Doubled."""
        return values * 2

    def mean(values: pd.Series) -> np.float64:
        """The mean; the index holds the dates."""
        assert isinstance(values.index, pd.DatetimeIndex)
        return np.float64(values.mean())

    def last(table: pd.Series) -> pd.Series:
        """The series as given."""
        return table

    def corr(table: pd.DataFrame) -> pd.DataFrame:
        """Correlations."""
        return table.corr()

    def stacked(table: np.ndarray) -> list[int]:
        """The shape of a two-column array."""
        return list(table.shape)

    app = FirmApp("risk")
    for function in (scaled, mean, last):
        app.expose(function, **{next(iter(function.__annotations__)): Series})
    app.expose(corr, table=Frame)
    app.expose(stacked, table=Frame)
    tools = _tools(app, store)
    table = {"dataset": "desks", "columns": ["growth", "value"]}

    assert check_tool(tools["scaled"], {"values": SERIES}).output["result"][0] == 0.04
    assert check_tool(tools["mean"], {"values": SERIES}).output["result"] == pytest.approx(-0.0125)
    assert check_tool(tools["last"], {"table": SERIES}).output["result"]["2024-01-31"] == 0.02
    frame = check_tool(tools["corr"], {"table": {"dataset": "desks"}}).output["result"]
    assert frame["index"] == ["growth", "value"] and frame["columns"]["growth"][0] == 1.0
    assert check_tool(tools["stacked"], {"table": table}).output["result"] == [4, 2]


# Mistakes are refused when exposed, with what to change


def _no_hints(returns, confidence=0.95):  # type: ignore[no-untyped-def]
    """Untyped."""
    return 0.0


def _no_return(returns: list[float]):  # type: ignore[no-untyped-def]
    """No return hint."""
    return 0.0


def _returns_none(returns: list[float]) -> None:
    """Returns nothing."""


def _variadic(*returns: float) -> float:
    """Variadic."""
    return 0.0


def any_dict(returns: list[float]) -> dict[str, Any]:
    """Fine as a dict: only a structured result's fields are checked."""
    return {}


@dataclass
class _Conventional:
    convention: str


def _reserved(returns: list[float]) -> _Conventional:
    """Uses a reserved name."""
    return _Conventional("mine")


def _string(returns: str) -> float:
    """A string cannot hold a series."""
    return 0.0


def _undocumented(returns: list[float]) -> float:
    return 0.0


@pytest.mark.parametrize(
    ("function", "marks", "message"),
    [
        (_no_hints, {}, "parameter 'returns' needs a type hint"),
        (_no_return, {"returns": Series}, "needs a return type hint"),
        (_returns_none, {"returns": Series}, "returns nothing"),
        (_variadic, {}, "*returns cannot be exposed"),
        (_reserved, {"returns": Series}, "field convention is added by the platform"),
        (_string, {"returns": Series}, "must be hinted as list[float]"),
        (_undocumented, {"returns": Series}, "needs a description"),
        (historical_var, {"prices": Series}, "prices marked as data"),
        (historical_var, {"returns": "Series"}, "mark data parameters with Series"),
    ],
)
def test_a_function_that_cannot_be_exposed_is_refused_with_the_fix(
    function: Any, marks: dict[str, Any], message: str
) -> None:
    with pytest.raises(ToolContractError, match=re.escape(message)):
        FirmApp("risk").expose(function, **marks)


def test_an_unmarked_numpy_parameter_asks_to_be_marked_as_data() -> None:
    np = pytest.importorskip("numpy")

    def total(values: np.ndarray) -> float:
        """Sum."""
        return float(values.sum())

    with pytest.raises(ToolContractError, match="mark it as data"):
        FirmApp("risk").expose(total)


def test_a_dict_result_may_use_any_key() -> None:
    FirmApp("risk").expose(any_dict, returns=Series)


def test_a_bad_tool_name_or_version_is_refused_when_exposed() -> None:
    with pytest.raises(ToolContractError, match="snake_case"):
        FirmApp("risk").expose(historical_var, name="Desk-VaR", returns=Series)
    with pytest.raises(ToolContractError, match=r"MAJOR\.MINOR\.PATCH"):
        FirmApp("risk").expose(historical_var, version="1", returns=Series)
    with pytest.raises(ToolContractError, match="App name"):
        FirmApp("Risk Desk")


# Names, apps, and stores


def test_names_must_be_unique_within_an_app_and_a_prefix_keeps_apps_apart(
    store: Store,
) -> None:
    app = FirmApp("risk", prefix=True)
    app.expose(historical_var, returns=Series)
    app.expose(historical_var, name="var95", returns=Series)

    assert app.names == ["risk_historical_var", "risk_var95"]
    with pytest.raises(DuplicateToolError, match="risk_var95"):
        app.expose(historical_var, name="var95", returns=Series)


def test_ready_made_tools_join_an_app_as_they_are(store: Store) -> None:
    class Ping(BaseModel):
        text: str

    @tool(name="echo", description="Echo the text.")
    def echo(params: Ping) -> Ping:
        return params

    app = FirmApp("reports")
    app.include(ToolRegistry([echo]))

    assert app.tools() == [echo]
    with pytest.raises(DuplicateToolError):
        app.include([echo])


def test_an_app_that_reads_data_needs_a_store_to_be_served() -> None:
    app = FirmApp("risk")
    app.expose(historical_var, returns=Series)

    with pytest.raises(ToolContractError, match="serve app 'risk' with datasets="):
        app.tools()


def test_declared_permissions_are_kept_beside_reading_data(store: Store) -> None:
    app = FirmApp("risk")
    app.expose(historical_var, returns=Series, permissions=[Permission.NETWORK])

    assert _tools(app, store)["historical_var"].permissions == {
        Permission.READ_LOCAL_DATA,
        Permission.NETWORK,
    }


def test_the_code_version_names_the_installed_package() -> None:
    assert (_code_version(BaseModel.model_validate) or "").startswith("pydantic ")
    assert _code_version(historical_var) is None  # a test module is not an installed package


# Docstrings


@pytest.mark.parametrize(
    "text",
    [
        "Sum it.\n\nArgs:\n    values: The values.\n    scale (float): Applied\n        last.\n",
        "Sum it.\n\nParameters\n----------\nvalues : list[float]\n    The values.\n"
        "scale : float\n    Applied\n    last.\n",
        "Sum it.\n\n:param values: The values.\n:param float scale: Applied last.\n",
    ],
)
def test_parameter_descriptions_are_read_from_google_numpy_and_sphinx_docstrings(
    text: str,
) -> None:
    doc = _Docstring.parse(text)

    assert doc.summary == "Sum it."
    assert doc.parameters == {"values": "The values.", "scale": "Applied last."}
