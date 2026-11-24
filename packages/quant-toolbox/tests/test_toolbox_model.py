"""The standard tables: validation names each problem, the as-of rule, and the wide pivot."""

from __future__ import annotations

import datetime as dt

import numpy as np

from quant_toolbox.data import Table, TableError
from quant_toolbox.data.model import TABLES, as_of, to_wide, validate

D1, D2, D3 = dt.date(2024, 1, 2), dt.date(2024, 1, 3), dt.date(2024, 1, 4)


def _good(name: str) -> dict[str, list]:
    """A valid three-row example of each standard table."""
    examples: dict[str, dict[str, list]] = {
        "securities": {
            "instrument": ["AAA", "BBB"],
            "valid_from": [D1, D1],
            "valid_to": [None, D3],
            "ticker": ["AAA", "BBB"],
            "currency": ["USD", "EUR"],
        },
        "prices": {
            "instrument": ["AAA", "AAA", "BBB"],
            "date": [D1, D2, D1],
            "field": ["close", "close", "close"],
            "source": ["vendor", "vendor", "vendor"],
            "value": [10.0, 10.5, 20.0],
        },
        "returns": {
            "entity": ["FUND", "FUND", "BENCH"],
            "date": [D1, D2, D1],
            "as_of": [D2, D2, D2],
            "value": [0.01, -0.002, 0.003],
            "basis": ["net", "net", "gross"],
        },
        "holdings": {
            "book": ["GQ", "GQ"],
            "instrument": ["AAA", "BBB"],
            "date": [D1, D1],
            "quantity": [100.0, 5.0],
            "weight": [0.5, 0.5],
        },
        "measures": {
            "entity": ["FUND"],
            "date": [D1],
            "measure": ["var_95"],
            "model": ["hist"],
            "as_of": [D1],
            "value": [0.02],
        },
        "reference": {
            "kind": ["calendar", "fx"],
            "name": ["NYSE", "EURUSD"],
            "date": [D1, D1],
            "value": [None, 1.1],
        },
    }
    return examples[name]


def test_each_table_validates_a_good_example() -> None:
    for name in TABLES:
        assert validate(name, _good(name)) == [], name


def test_missing_required_column_is_named() -> None:
    columns = _good("prices")
    del columns["source"]
    problems = validate("prices", columns)
    assert problems == ["Missing required column 'source' of prices."]


def test_wrong_type_names_the_row_and_column() -> None:
    columns = _good("returns")
    columns["value"] = [0.01, "high", 0.003]
    columns["as_of"] = [D2, D2, "2024/01/03"]
    problems = validate("returns", columns)
    assert any("Row 2, column 'value'" in p and "expected a number" in p for p in problems)
    assert any("Row 3, column 'as_of'" in p and "YYYY-MM-DD" in p for p in problems)


def test_numbers_as_dates_and_booleans_as_numbers_are_refused() -> None:
    columns = _good("prices")
    columns["date"] = [20240102, D2, D1]
    columns["value"] = [True, 10.5, 20.0]
    problems = validate("prices", columns)
    assert "Row 1, column 'date': expected a date, got 20240102." in problems
    assert any("Row 1, column 'value'" in p for p in problems)


def test_empty_required_cell_is_named_and_optional_empty_is_allowed() -> None:
    columns = _good("holdings")
    columns["quantity"] = [None, 5.0]
    columns["weight"] = [None, None]
    problems = validate("holdings", columns)
    assert problems == ["Row 1, column 'quantity': is empty."]


def test_duplicate_key_names_both_rows() -> None:
    columns = _good("returns")
    columns["entity"] = ["FUND", "FUND", "FUND"]
    columns["date"] = [D1, D1, D2]
    columns["as_of"] = [D2, D2, D2]
    problems = validate("returns", columns)
    assert problems == [
        f"Row 2 repeats the key (entity, date, as_of) = (FUND, {D1}, {D2}) of row 1."
    ]


def test_unequal_columns_and_unknown_table_are_named() -> None:
    columns = _good("returns")
    columns["value"] = [0.01]
    assert any("different lengths" in p for p in validate("returns", columns))
    try:
        validate("nope", {})
    except TableError as error:
        assert "No standard table 'nope'" in str(error)
    else:
        raise AssertionError("an unknown table must raise")


def test_as_of_takes_the_latest_vintage_on_or_before_the_date() -> None:
    rows = [
        {"entity": "FUND", "date": D1, "as_of": D2, "value": 0.010},
        {"entity": "FUND", "date": D1, "as_of": D3, "value": 0.012},  # restatement
        {"entity": "FUND", "date": D2, "as_of": D2, "value": -0.002},
    ]
    before = as_of(rows, D2)
    assert [(r["date"], r["value"]) for r in before] == [(D1, 0.010), (D2, -0.002)]
    after = as_of(rows, D3)
    assert [(r["date"], r["value"]) for r in after] == [(D1, 0.012), (D2, -0.002)]


def test_as_of_with_a_vintage_asks_for_that_vintage_exactly() -> None:
    rows = [
        {"entity": "FUND", "date": D1, "as_of": D2, "value": 0.010},
        {"entity": "FUND", "date": D1, "as_of": D3, "value": 0.012},
        {"entity": "BENCH", "date": D1, "as_of": D3, "value": 0.003},
    ]
    kept = as_of(rows, D3, vintage=D2)
    assert [(r["entity"], r["value"]) for r in kept] == [("FUND", 0.010)]


def test_to_wide_matches_a_hand_built_table() -> None:
    columns = {
        "entity": ["FUND", "BENCH", "FUND", "BENCH"],
        "date": [D1, D1, D2, D2],
        "value": [0.01, 0.003, -0.002, 0.004],
    }
    wide = to_wide(columns)
    expected = Table(
        [D1, D2],
        ["BENCH", "FUND"],
        np.array([[0.003, 0.01], [0.004, -0.002]]),
    )
    assert wide.dates == expected.dates
    assert wide.columns == expected.columns
    np.testing.assert_allclose(wide.values, expected.values)


def test_to_wide_filters_one_field_of_prices() -> None:
    columns = {
        "instrument": ["AAA", "AAA", "AAA"],
        "date": [D1, D1, D2],
        "field": ["close", "yield", "close"],
        "source": ["vendor", "vendor", "vendor"],
        "value": [10.0, 3.5, 10.5],
    }
    wide = to_wide(columns, names="instrument", where={"field": "close"})
    assert wide.columns == ["AAA"]
    np.testing.assert_allclose(wide.values[:, 0], [10.0, 10.5])
