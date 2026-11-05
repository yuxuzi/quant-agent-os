"""The team-server image must see what `uv sync --locked` needs, or its build fails."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_dockerfile_copies_and_dockerignore_admits_the_project() -> None:
    dockerfile = (ROOT / "deploy" / "Dockerfile").read_text("utf-8")
    ignore = (ROOT / ".dockerignore").read_text("utf-8").splitlines()

    for line in ("COPY pyproject.toml uv.lock ./", "COPY README.md LICENSE ./", "COPY src src"):
        assert line in dockerfile
    assert {"!pyproject.toml", "!uv.lock", "!README.md", "!LICENSE", "!src/"} <= set(ignore)
