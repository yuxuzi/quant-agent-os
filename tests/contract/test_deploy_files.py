"""The team-server image must see every workspace member's metadata, or `uv sync --locked` fails."""

import glob
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _members() -> list[str]:
    patterns = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["tool"]["uv"][
        "workspace"
    ]["members"]
    return sorted(
        Path(path).relative_to(ROOT).as_posix()
        for pattern in patterns
        for path in glob.glob(str(ROOT / pattern))
        if (Path(path) / "pyproject.toml").is_file()
    )


def test_the_dockerfile_copies_and_dockerignore_admits_every_members_pyproject() -> None:
    dockerfile = (ROOT / "deploy" / "Dockerfile").read_text("utf-8")
    ignore = (ROOT / ".dockerignore").read_text("utf-8").splitlines()

    for member in _members():
        assert f"COPY {member}/pyproject.toml {member}/" in dockerfile, member
        assert {f"!{member}/pyproject.toml", f"!{member}/"} & set(ignore), member
