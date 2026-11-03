"""Every served tool matches tool-versions.lock, so schema changes are versioned (decision 0015)."""

from tool_lock import LOCK, read_lock, render, served_tools

from quant_agent_os.tools.compatibility import check_lock


def test_every_served_tool_matches_the_lock() -> None:
    problems = check_lock(read_lock(), served_tools())

    assert problems == [], (
        "Tool schemas changed. Raise each tool's version as the change needs, run "
        "`uv run python apps/quant-platform/tests/tool_lock.py --update`, and add the change "
        "to docs/tools-changelog.md:\n" + "\n".join(problems)
    )


def test_the_lock_is_written_canonically() -> None:
    assert LOCK.read_text("utf-8") == render(served_tools())
