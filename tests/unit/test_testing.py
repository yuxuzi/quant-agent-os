"""`check_tool`, the contract check generated tool packages run."""

import itertools

import pytest
from pydantic import BaseModel

from quant_agent_os.testing import check_tool
from quant_agent_os.tools import ToolContractError, tool


class Text(BaseModel):
    text: str


class Count(BaseModel):
    words: int


@tool(name="count_words", description="Count words.")
def count_words(params: Text) -> Count:
    return Count(words=len(params.text.split()))


COUNTER = itertools.count()


@tool(name="unstable", description="Claims determinism but is not.")
def unstable(params: Text) -> Count:
    return Count(words=next(COUNTER))


def test_a_good_tool_passes_and_returns_its_record() -> None:
    record = check_tool(count_words, {"text": "a b"})

    assert record.output == {"words": 2}


def test_an_expected_failure_status_can_be_checked() -> None:
    assert check_tool(count_words, {}, expect="invalid_input").output is None


def test_a_tool_that_breaks_the_contract_is_reported() -> None:
    with pytest.raises(ToolContractError, match="declared deterministic"):
        check_tool(unstable, {"text": "a"})
    with pytest.raises(ToolContractError, match="status is 'invalid_input'"):
        check_tool(count_words, {})
