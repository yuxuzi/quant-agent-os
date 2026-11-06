"""The team-server image must see what `uv sync --locked` needs, or its build fails."""

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from quant_platform.cli import app

ROOT = Path(__file__).resolve().parents[2]
NOT_QAOS = {"backup", "restore"}  # they stream the volumes, and run no platform code


def test_the_dockerfile_copies_and_dockerignore_admits_the_project() -> None:
    dockerfile = (ROOT / "deploy" / "Dockerfile").read_text("utf-8")
    ignore = (ROOT / ".dockerignore").read_text("utf-8").splitlines()

    for line in ("COPY pyproject.toml uv.lock ./", "COPY README.md LICENSE ./", "COPY src src"):
        assert line in dockerfile
    assert {"!pyproject.toml", "!uv.lock", "!README.md", "!LICENSE", "!src/"} <= set(ignore)


@pytest.mark.parametrize("name", ["compose.yaml", "compose.postgres.yaml"])
def test_every_compose_service_runs_a_real_qaos_command(name: str) -> None:
    services = yaml.safe_load((ROOT / "deploy" / name).read_text("utf-8"))["services"]
    commands = {
        (*service.get("entrypoint", ["qaos"]), *service.get("command", ()))
        for name, service in services.items()
        if name not in NOT_QAOS
        and ("build" in service or ("image" in service and "QAOS_IMAGE" in service["image"]))
    }

    assert commands
    for command in commands:
        assert command[0] == "qaos", command
        words = [word for word in command[1:] if not word.startswith("-")][:2]
        assert CliRunner().invoke(app, [*words[:1], "--help"]).exit_code == 0, command
