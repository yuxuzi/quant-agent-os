"""The tutorial's lab scripts and sample data, so docs/tutorial.md stays true."""

import io
import json
import re
import runpy
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from quant_agent_os.data import DirectoryDatasetStore

ROOT = Path(__file__).resolve().parents[2]
LABS = ROOT / "examples" / "tutorial"
TUTORIAL = ROOT / "docs" / "tutorial.md"


def run_lab(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    script: str,
    *args: str,
) -> str:
    path = LABS / script
    monkeypatch.setattr(sys, "argv", [str(path), *args])
    runpy.run_path(str(path), run_name="__main__")
    return capsys.readouterr().out


def test_analytics_lab_prints_the_numbers_the_tutorial_shows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = DirectoryDatasetStore(tmp_path)
    store.put("fund", LABS / "data" / "fund_returns.csv")
    store.put("portfolio", LABS / "data" / "portfolio_returns.csv")

    output = run_lab(monkeypatch, capsys, "lab_analytics.py", str(tmp_path))

    for line in ("sharpe_ratio         1.1043", "equities  weight  60%  share of risk  88.9%"):
        assert line in output
        assert line in TUTORIAL.read_text(encoding="utf-8")


def test_analytics_lab_finds_the_datasets_in_the_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("QUANT_AGENT_OS_HOME", str(tmp_path))
    store = DirectoryDatasetStore(tmp_path / "datasets")
    store.put("fund", LABS / "data" / "fund_returns.csv")
    store.put("portfolio", LABS / "data" / "portfolio_returns.csv")

    assert "sharpe_ratio         1.1043" in run_lab(monkeypatch, capsys, "lab_analytics.py")


def test_tutorial_names_no_removed_command() -> None:
    text = TUTORIAL.read_text(encoding="utf-8")

    for removed in ("quant-agent-os --version", "dataset push", "api-key new", "serve-mcp"):
        assert removed not in text


def test_guardrails_lab_shows_every_refusal_in_order(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = run_lab(monkeypatch, capsys, "lab_guardrails.py")

    statuses = re.findall(r"status: (\w+)", output)
    assert statuses == [
        "denied",
        "approval_required",
        "ok",
        "approval_required",
        "unknown_tool",
        "invalid_input",
    ]


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ([{"name": "a"}, {"name": "b"}], "tools: a, b"),
        ({"detail": "No key."}, "refused: No key."),
        ({"status": "denied", "error": "No.", "hint": "Stop."}, "error: No."),
        ({"status": "ok", "output": {"metrics": {"sharpe_ratio": 1.5}}}, "sharpe_ratio: 1.5000"),
        ({"status": "ok", "output": {"spec": {"open_questions": ["a"]}}}, "open questions: 1"),
        ([], "nothing listed"),
        (
            {
                "status": "ok",
                "output": {"breaches": 1, "checks": [{"name": "Gross", "breached": True}]},
            },
            "breaches: 1 of 1: Gross",
        ),
        ({"status": "ok", "output": {"datasets": [{"id": "fund"}]}}, "datasets: fund"),
        ({"status": "ok", "output": {"result": -0.08, "convention": "Peak"}}, "result: -0.0800"),
        (
            {
                "run": {"principal": "bob"},
                "calls": [{"tool": "t", "status": "error", "refused": ["x"]}],
            },
            "bob called t: error, refused: x",
        ),
    ],
)
def test_show_makes_rest_responses_readable(
    response: object,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(response)))

    assert expected in run_lab(monkeypatch, capsys, "show.py")


def test_show_fails_when_no_server_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(sys, "argv", [str(LABS / "show.py")])

    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(LABS / "show.py"), run_name="__main__")

    assert "is the server running" in str(stopped.value.code)  # a message exits with status 1


def _shell(shell: str, folder: Path, script: str) -> subprocess.CompletedProcess[str]:
    if shutil.which(shell) is None:
        # macOS always has zsh, and CI runs there, so the zsh path cannot go untested.
        assert not (shell == "zsh" and sys.platform == "darwin"), "zsh is missing on macOS"
        pytest.skip(f"{shell} is not installed")
    env = {"HOME": str(folder), "PATH": "/usr/bin:/bin"}
    return subprocess.run(
        [shell, "-c", script], cwd=folder, env=env, capture_output=True, text=True
    )


# On Windows, `bash` is often the WSL launcher; the tutorial sends Windows readers to WSL.
posix_shells = pytest.mark.skipif(sys.platform == "win32", reason="env.sh is for bash and zsh")


@posix_shells
@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_env_sh_finds_the_clone_from_any_folder(tmp_path: Path, shell: str) -> None:
    result = _shell(
        shell, tmp_path, f'source {shlex.quote(str(LABS / "env.sh"))} && echo "REPO is $REPO"'
    )

    assert f"REPO is {ROOT}" in result.stdout, result.stderr


@posix_shells
@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_env_sh_prints_a_source_line_that_is_safe_to_paste(tmp_path: Path, shell: str) -> None:
    clone = tmp_path / "a $(touch pwned) clone"
    shutil.copytree(LABS, clone / "examples" / "tutorial")
    env_sh = shlex.quote(str(clone / "examples" / "tutorial" / "env.sh"))

    printed = _shell(shell, tmp_path, f"source {env_sh}").stdout
    line = next(text for text in printed.splitlines() if "first run: source" in text)
    pasted = _shell(shell, tmp_path, line.split("first run: ", 1)[1] + ' && echo "REPO is $REPO"')

    assert f"REPO is {clone}" in pasted.stdout, pasted.stderr
    assert not (tmp_path / "pwned").exists()


def test_tutorial_links_resolve() -> None:
    text = TUTORIAL.read_text(encoding="utf-8")
    targets = re.findall(r"\]\((?!https?://|#)([^)#]+)", text)

    assert targets
    missing = [target for target in targets if not (TUTORIAL.parent / target).exists()]
    assert not missing
