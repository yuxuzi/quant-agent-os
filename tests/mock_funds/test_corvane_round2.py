"""Round 2: a new Cognito group in Corvane's access file applies with no restart (R2-G2, corrected).

The platform's `FileAccessPolicy` follows the file: its stamp (inode, size, mtime) is read on each
check, so an edit on disk changes the answer at once.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from quant_agent_os.tools import FileAccessPolicy
from quant_agent_os.tools.access import Caller

ROOT = Path(__file__).resolve().parents[2]
ACCESS = ROOT / "examples" / "mock-funds" / "corvane" / "facility" / "access.toml"

NEW_GROUP = """
[groups.analyst-quant-new]
tools = ["server_info", "list_datasets", "analyze_*"]
datasets = ["px_*", "scores_*"]
"""


def test_a_new_cognito_group_applies_without_a_restart(tmp_path: Path) -> None:
    file = tmp_path / "access.toml"
    shutil.copy(ACCESS, file)
    policy = FileAccessPolicy(file)
    analyst = Caller("new.analyst", "default", frozenset({"analyst-quant-new"}))

    assert policy.allows(analyst, "tool.call", "analyze_risk") is False  # not yet granted
    assert policy.allows(analyst, "dataset.read", "px_ret_usd") is False

    file.write_text(file.read_text() + NEW_GROUP)  # the group is added on disk; no restart

    assert policy.allows(analyst, "tool.call", "analyze_risk") is True
    assert policy.allows(analyst, "dataset.read", "px_ret_usd") is True
    assert policy.allows(analyst, "dataset.read", "weights_lcc") is False  # holdings still refused


def test_an_existing_group_is_unchanged_by_the_edit(tmp_path: Path) -> None:
    file = tmp_path / "access.toml"
    shutil.copy(ACCESS, file)
    policy = FileAccessPolicy(file)
    sector = Caller("ines", "default", frozenset({"analyst-industrials"}))
    before = policy.allows(sector, "dataset.read", "px_ret_usd")
    file.write_text(file.read_text() + NEW_GROUP)
    assert policy.allows(sector, "dataset.read", "px_ret_usd") is before is True
