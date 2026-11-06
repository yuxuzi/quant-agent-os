"""Resources and prompts (plan step P2.2): URI templates backed by tools, and workflow prompts."""

import pytest

from quant_agent_os.tools import (
    PromptArgument,
    PromptArgumentError,
    PromptSpec,
    ResourceSpec,
    ToolContractError,
    UnknownResourceError,
)
from quant_agent_os.tools.catalog import find_resource

PASSAGE = ResourceSpec(
    uri_template="qaos://passage/{chunk_id}",
    name="passage",
    description="One passage in full.",
    tool="get_source_passage",
)
TEMPLATE = ResourceSpec(
    uri_template="qaos://template/{template}/{profile}",
    name="template",
    description="What a template asks.",
    tool="describe_template",
)
PROMPT = PromptSpec(
    name="research_signal",
    description="Research one signal.",
    template="Research {signal}{focus}.",
    arguments=(
        PromptArgument("signal", "The signal"),
        PromptArgument("focus", "What to look at", required=False),
    ),
)


def test_a_template_matches_one_segment_per_variable_and_decodes_it() -> None:
    assert PASSAGE.variables == ["chunk_id"]
    assert PASSAGE.match("qaos://passage/doc%3Aab12%231") == {"chunk_id": "doc:ab12#1"}
    assert PASSAGE.match("qaos://passage/doc:ab12#1") == {"chunk_id": "doc:ab12#1"}
    assert TEMPLATE.match("qaos://template/quant-baseline/quant-lab") == {
        "template": "quant-baseline",
        "profile": "quant-lab",
    }
    assert PASSAGE.match("qaos://passage/a/b") is None
    assert PASSAGE.match("qaos://passages/a") is None


def test_find_resource_picks_the_matching_template_or_says_none_matches() -> None:
    assert find_resource([TEMPLATE, PASSAGE], "qaos://passage/x") == (PASSAGE, {"chunk_id": "x"})
    with pytest.raises(UnknownResourceError, match="qaos://run/1"):
        find_resource([TEMPLATE, PASSAGE], "qaos://run/1")


@pytest.mark.parametrize("template", ["passage/{id}", "qaos://passage/{Chunk}", "qaos://a b"])
def test_a_malformed_uri_template_is_refused(template: str) -> None:
    with pytest.raises(ToolContractError, match="URI template"):
        ResourceSpec(uri_template=template, name="bad", description="Bad.", tool="t")


def test_a_prompt_renders_its_arguments_and_defaults_optional_ones() -> None:
    assert PROMPT.render({"signal": "momentum"}) == "Research momentum."
    assert PROMPT.render({"signal": "carry", "focus": " in FX"}) == "Research carry in FX."


def test_a_prompt_refuses_missing_and_unexpected_arguments() -> None:
    with pytest.raises(PromptArgumentError, match="needs signal and does not take topic"):
        PROMPT.render({"topic": "x"})


def test_a_prompt_must_declare_exactly_the_placeholders_it_uses() -> None:
    with pytest.raises(ToolContractError, match="declares arguments"):
        PromptSpec(name="p", description="P.", template="Study {signal}.")
